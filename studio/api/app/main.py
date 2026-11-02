from __future__ import annotations

import threading
import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .auth.sessions import scan_all, start_official_login
from .languages import LANGUAGES, find_language
from .legacy import create_from_strategy, import_legacy_decks, legacy_audio_path, list_strategies
from .lexicon import lookup_word, row_to_fields
from .editor import EditError, add_words, delete_cards, patch_card, rename_deck
from .models import Card, CardPatch, CardsAdd, CardsBulkDelete, CardsEnrich, Deck, DeckCreate, DeckPatch, Settings, ImagesReplace
from .paths import EXPORTS_DIR, MEDIA_DIR, ensure_dirs
from .pipeline import fill_missing, run_stage, run_until_blocked
from .protocol import ProtocolError, STAGES, qa_deck
from .services.export import export_apkg, export_csv
from .services.images import image_generation_status
from .services.publish import (
    AnkiConnectError,
    PublishError,
    anki_deck_name,
    anki_status,
    delete_release_asset,
    language_release_status,
    list_release_assets,
    publish_language_release,
    publish_release,
    release_asset_name,
    release_status,
    send_to_anki,
    unpublish_language,
)
from .store import (
    delete_deck,
    list_decks,
    library_image_stats,
    load_deck,
    load_settings,
    new_id,
    new_stage_map,
    now_iso,
    save_deck,
    save_settings,
)

def _warm_cache() -> None:
    from .cache import warm_from_decks
    try:
        warm_from_decks()
    except Exception:
        pass


@asynccontextmanager
async def lifespan(_: FastAPI):
    ensure_dirs()
    import_legacy_decks()
    threading.Thread(target=_warm_cache, daemon=True, name="warm-cache").start()
    yield


app = FastAPI(title="Learning", version="0.1.0", lifespan=lifespan)

# One API writer per deck, including long-running generation and replacement.
_deck_writers: dict[str, asyncio.Lock] = {}


@app.middleware("http")
async def serialize_deck_writes(request, call_next):
    parts = request.url.path.split("/")
    if request.method in {"POST", "PATCH", "DELETE"} and len(parts) >= 4 and parts[1:3] == ["api", "decks"]:
        lock = _deck_writers.setdefault(parts[3], asyncio.Lock())
        async with lock:
            return await call_next(request)
    return await call_next(request)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "service": "learning"}


@app.get("/api/meta")
def meta() -> dict:
    settings = load_settings()
    image_provider = ("codex" if settings.default_image_bank in {"codex", "openai"} else
                      "grok" if settings.default_image_bank == "grok" else settings.default_ai_provider)
    ready, reason = image_generation_status(image_provider, settings)
    return {
        "stages": STAGES,
        "image_banks": [
            {"id": "grok", "label": "Grok Imagine (IA)", "needs_key": False},
            {"id": "codex", "label": "Codex (ChatGPT)", "needs_key": False},
            {"id": "wikimedia", "label": "Wikimedia / Wikipédia", "needs_key": False},
            {"id": "unsplash", "label": "Unsplash", "needs_key": True},
            {"id": "pexels", "label": "Pexels", "needs_key": True},
        ],
        "ai_providers": [
            {"id": "grok", "label": "Grok Build (OAuth xAI)", "needs_key": False},
            {"id": "claude", "label": "Claude (OAuth / clé)", "needs_key": False},
            {"id": "codex", "label": "Codex ChatGPT (OAuth / clé)", "needs_key": False},
            {"id": "local", "label": "Local (secours, validé)", "needs_key": False},
        ],
        "languages": [{"id": item["id"], "label": item["label"]} for item in LANGUAGES],
        "strategies": list_strategies(),
        "keys": {
            "anthropic": bool(settings.anthropic_api_key),
            "openai": bool(settings.openai_api_key),
            "xai": bool(settings.xai_api_key),
            "unsplash": bool(settings.unsplash_access_key),
            "pexels": bool(settings.pexels_api_key),
        },
        "defaults": {
            "ai_provider": settings.default_ai_provider,
            "image_bank": settings.default_image_bank,
            "target_language": settings.default_target_language,
            "target_label": settings.default_target_label,
            "image_concurrency": settings.image_concurrency,
        },
        "image_generation": {
            "ok": ready,
            "reason": reason,
            "provider": image_provider,
        },
    }


@app.get("/api/settings", response_model=Settings)
def get_settings() -> Settings:
    return load_settings()


@app.put("/api/settings", response_model=Settings)
def put_settings(payload: Settings) -> Settings:
    return save_settings(payload)


@app.get("/api/auth/status")
def auth_status() -> dict:
    return {"providers": [row.model_dump() for row in scan_all()]}


@app.post("/api/auth/{provider}/login")
def auth_login(provider: str) -> dict:
    try:
        return start_official_login(provider)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/decks", response_model=list[Deck])
def get_decks() -> list[Deck]:
    return list_decks()


@app.post("/api/decks", response_model=Deck)
def create_deck(payload: DeckCreate) -> Deck:
    settings = load_settings()
    spec = find_language(payload.language_label or payload.language)
    language = spec["id"]
    label = payload.language_label.strip() or spec["label"]
    target_spec = find_language(
        payload.target_label or payload.target_language or settings.default_target_label or settings.default_target_language
    )
    if payload.source_id.strip():
        try:
            deck = create_from_strategy(
                payload.source_id.strip(),
                payload.limit,
                payload.name,
                features=payload.features,
                target_language=target_spec["id"],
                target_label=payload.target_label.strip() or target_spec["label"],
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        save_deck(deck)
        if getattr(deck, "kind", "vocab") == "grammar":
            try:
                fill_missing(deck, settings)
            finally:
                save_deck(deck)
        return deck
    cards: list[Card] = []
    for raw in payload.words:
        word = raw.strip()
        if not word:
            continue
        row = lookup_word(language, word)
        fields = row_to_fields(language, row) if row else {}
        cards.append(Card(
            id=new_id(),
            lemma=fields.get("lemma") or word,
            surface=fields.get("surface") or word,
            language=language,
            french=fields.get("french", ""),
            category=fields.get("category", ""),
            ipa=fields.get("ipa", ""),
            phrase=fields.get("phrase", ""),
            phrase_fr=fields.get("phrase_fr", ""),
        ))
    if not cards:
        raise HTTPException(400, "ajoute au moins un mot")
    aliases = {"anthropic": "claude", "openai": "codex", "xai": "grok"}
    provider = aliases.get(settings.default_ai_provider, settings.default_ai_provider)
    deck = Deck(
        id=new_id(),
        name=payload.name.strip() or "Sans nom",
        language=language,
        language_label=label,
        target_language=target_spec["id"],
        target_label=payload.target_label.strip() or target_spec["label"],
        image_bank=settings.default_image_bank,
        ai_provider=provider,  # type: ignore[arg-type]
        source="manual",
        features=payload.features,
        cards=cards,
        stages={},
        created_at=now_iso(),
        updated_at=now_iso(),
    )
    deck.stages = new_stage_map(deck)
    from .cache import apply_deck
    apply_deck(deck)
    save_deck(deck)
    try:
        fill_missing(deck, settings)
    finally:
        save_deck(deck)
    return deck


@app.get("/api/decks/{deck_id}", response_model=Deck)
def get_deck(deck_id: str) -> Deck:
    try:
        return load_deck(deck_id)
    except FileNotFoundError:
        raise HTTPException(404, "deck introuvable")
    except (ValueError, OSError) as exc:
        raise HTTPException(503, f"deck indisponible: {exc}")


@app.delete("/api/decks/{deck_id}")
def remove_deck(deck_id: str) -> dict:
    delete_deck(deck_id)
    return {"ok": True}


def _load(deck_id: str) -> Deck:
    try:
        return load_deck(deck_id)
    except FileNotFoundError:
        raise HTTPException(404, "deck introuvable")


@app.get("/api/image-library")
def get_image_library():
    from .image_library import inventory
    return inventory()


@app.get("/api/image-library/assets")
def search_image_library(q: str = "", source: str = "", limit: int = 24):
    from .image_library import search
    return search(q, source, limit)


@app.get("/api/decks/{deck_id}/images")
def get_deck_images(deck_id: str):
    from .image_operations import deck_inventory
    return deck_inventory(_load(deck_id))


@app.post("/api/decks/{deck_id}/images/replace")
def replace_deck_images(deck_id: str, payload: ImagesReplace):
    import hashlib
    import json
    from .image_operations import replace_images
    from .image_library import record_event
    deck = _load(deck_id)
    before = deck.model_dump()
    def revision(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
    if not payload.preview and payload.expected_revision and payload.expected_revision != revision(before):
        raise HTTPException(409, "Deck changed since preview; preview the changes again.")
    try:
        result = replace_images(deck, payload, load_settings())
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    if not payload.preview:
        if _load(deck_id).model_dump() != before:
            raise HTTPException(409, "Deck changed during replacement; refresh and retry. No deck changes saved.")
        from .pipeline import sync_stages
        save_deck(sync_stages(deck))
        result["event_id"] = record_event({"deck": deck_id, "request": payload.model_dump(),
                                           "backup": result["backup"], "changed": result["changed"],
                                           "errors": result["errors"]})
    result["revision"] = revision(_load(deck_id).model_dump()) if not payload.preview else revision(before)
    return result


@app.patch("/api/decks/{deck_id}", response_model=Deck)
def patch_deck(deck_id: str, payload: DeckPatch) -> Deck:
    deck = _load(deck_id)
    try:
        if payload.name is not None:
            rename_deck(deck, payload.name)
    except EditError as exc:
        raise HTTPException(409, str(exc))
    if payload.features is not None:
        deck.features = payload.features
        from .pipeline import sync_stages
        sync_stages(deck)
    return save_deck(deck)


@app.post("/api/decks/{deck_id}/cards", response_model=Deck)
def post_cards(deck_id: str, payload: CardsAdd) -> Deck:
    deck = _load(deck_id)
    existing_ids = {card.id for card in deck.cards}
    try:
        add_words(deck, payload.words)
    except EditError as exc:
        raise HTTPException(409, str(exc))
    new_ids = [card.id for card in deck.cards if card.id not in existing_ids]
    save_deck(deck)
    if payload.enrich and new_ids:
        try:
            fill_missing(deck, load_settings(), new_ids)
        finally:
            save_deck(deck)
    return deck


@app.post("/api/decks/{deck_id}/enrich", response_model=Deck)
def post_enrich(deck_id: str, payload: CardsEnrich) -> Deck:
    deck = _load(deck_id)
    if payload.ids:
        known = {card.id for card in deck.cards}
        if any(item not in known for item in payload.ids):
            raise HTTPException(404, "carte introuvable")
    if payload.features is not None:
        deck.features = payload.features
        from .pipeline import sync_stages
        sync_stages(deck)
    deck.stage_errors = {}
    fill_missing(
        deck,
        load_settings(),
        payload.ids or None,
        payload.limit,
        concurrency=payload.concurrency,
        on_progress=save_deck,
        retry=payload.retry,
        generate_images=payload.generate_images,
        replace_images=payload.replace_images,
    )
    return save_deck(deck)


@app.patch("/api/decks/{deck_id}/cards/{card_id}", response_model=Deck)
def patch_one_card(deck_id: str, card_id: str, payload: CardPatch) -> Deck:
    deck = _load(deck_id)
    fields = payload.model_dump(exclude_unset=True)
    try:
        patch_card(deck, card_id, fields)
    except EditError as exc:
        raise HTTPException(409, str(exc))
    return save_deck(deck)


@app.delete("/api/decks/{deck_id}/cards/{card_id}", response_model=Deck)
def delete_one_card(deck_id: str, card_id: str) -> Deck:
    deck = _load(deck_id)
    try:
        delete_cards(deck, [card_id])
    except EditError as exc:
        raise HTTPException(409, str(exc))
    return save_deck(deck)


@app.post("/api/decks/{deck_id}/cards/bulk-delete", response_model=Deck)
def bulk_delete_cards(deck_id: str, payload: CardsBulkDelete) -> Deck:
    deck = _load(deck_id)
    try:
        delete_cards(deck, payload.ids)
    except EditError as exc:
        raise HTTPException(409, str(exc))
    return save_deck(deck)


@app.post("/api/decks/{deck_id}/run/{stage}", response_model=Deck)
def post_run_stage(deck_id: str, stage: str) -> Deck:
    try:
        deck = load_deck(deck_id)
    except FileNotFoundError:
        raise HTTPException(404, "deck introuvable")
    try:
        deck = run_stage(deck, stage, load_settings())
    except ProtocolError as exc:
        raise HTTPException(409, str(exc))
    return save_deck(deck)


@app.post("/api/decks/{deck_id}/run", response_model=Deck)
def post_run_all(deck_id: str) -> Deck:
    try:
        deck = load_deck(deck_id)
    except FileNotFoundError:
        raise HTTPException(404, "deck introuvable")
    try:
        deck = run_until_blocked(deck, load_settings())
    except ProtocolError as exc:
        raise HTTPException(409, str(exc))
    return save_deck(deck)


@app.get("/api/decks/{deck_id}/qa")
def get_qa(deck_id: str) -> dict:
    try:
        deck = load_deck(deck_id)
    except FileNotFoundError:
        raise HTTPException(404, "deck introuvable")
    return qa_deck(deck).model_dump()


@app.post("/api/decks/{deck_id}/export/csv")
def post_export_csv(deck_id: str) -> dict:
    try:
        deck = load_deck(deck_id)
        if deck.stages.get("qa") != "passed":
            deck = run_stage(deck, "export", load_settings())
            save_deck(deck)
            if deck.stages.get("export") != "passed":
                raise ProtocolError("QA incomplet")
        path = export_csv(deck)
        deck.stages["export"] = "passed"
        save_deck(deck)
        return {"ok": True, "path": str(path), "filename": path.name}
    except FileNotFoundError:
        raise HTTPException(404, "deck introuvable")
    except ProtocolError as exc:
        raise HTTPException(409, str(exc))


@app.post("/api/decks/{deck_id}/export/apkg")
def post_export_apkg(deck_id: str):
    try:
        deck = load_deck(deck_id)
        path = export_apkg(deck)
        deck.stages["export"] = "passed"
        save_deck(deck)
        return {"ok": True, "path": str(path), "filename": path.name}
    except FileNotFoundError:
        raise HTTPException(404, "deck introuvable")
    except ProtocolError as exc:
        raise HTTPException(409, str(exc))


@app.get("/api/anki/status")
def get_anki_status() -> dict:
    return anki_status(load_settings())


@app.get("/api/library/images/stats")
def get_library_image_stats() -> dict:
    try:
        return library_image_stats()
    except (OSError, ValueError) as exc:
        raise HTTPException(503, "Impossible de compter les images des decks") from exc


@app.get("/api/publish")
def get_publish_listing() -> dict:
    return list_release_assets(load_settings())


@app.delete("/api/publish/{asset_name:path}")
def remove_publish_asset(asset_name: str) -> dict:
    try:
        return delete_release_asset(asset_name, load_settings())
    except PublishError as exc:
        text = str(exc)
        raise HTTPException(404 if "introuvable" in text else 502, text) from exc


@app.get("/api/languages/{lang_id}/publish")
def get_language_publish(lang_id: str) -> dict:
    spec = find_language(lang_id)
    decks = [deck for deck in list_decks() if deck.language == lang_id]
    folder = next((deck.language_label for deck in decks if deck.language_label), spec["label"])
    return language_release_status(folder, load_settings())


@app.post("/api/languages/{lang_id}/publish")
def post_language_publish(lang_id: str, mode: str = "folder") -> dict:
    if mode not in ("folder", "separate"):
        raise HTTPException(400, "mode must be folder or separate")
    spec = find_language(lang_id)
    summaries = [deck for deck in list_decks() if deck.language == lang_id]
    if not summaries:
        raise HTTPException(404, "aucun deck dans cette langue")
    folder = next((deck.language_label for deck in summaries if deck.language_label), spec["label"])
    packages: list[tuple[str, Path]] = []
    skipped: list[str] = []
    try:
        for summary in summaries:
            try:
                deck = load_deck(summary.id)
                path = export_apkg(deck)
                deck.stages["export"] = "passed"
                save_deck(deck)
                packages.append((f"{deck.name}.apkg", path))
            except (ProtocolError, FileNotFoundError, ValueError, OSError):
                skipped.append(summary.name)
        if not packages:
            raise HTTPException(409, "aucun deck prêt (QA non validé)")
        result = publish_language_release(folder, packages, load_settings(), mode=mode)
        result["published"] = len(packages)
        result["skipped"] = skipped
        result["folder"] = folder
        return result
    except PublishError as exc:
        raise HTTPException(502, str(exc)) from exc


@app.delete("/api/languages/{lang_id}/publish")
def delete_language_publish(lang_id: str) -> dict:
    spec = find_language(lang_id)
    decks = [deck for deck in list_decks() if deck.language == lang_id]
    folder = next((deck.language_label for deck in decks if deck.language_label), spec["label"])
    try:
        return unpublish_language(folder, load_settings())
    except PublishError as exc:
        raise HTTPException(502, str(exc)) from exc


@app.get("/api/decks/{deck_id}/publish")
def get_publish(deck_id: str) -> dict:
    try:
        deck = _load(deck_id)
    except FileNotFoundError:
        raise HTTPException(404, "deck introuvable")
    return release_status(deck, load_settings())


@app.post("/api/decks/{deck_id}/publish")
def post_publish(deck_id: str) -> dict:
    try:
        deck = _load(deck_id)
        path = export_apkg(deck)
        deck.stages["export"] = "passed"
        save_deck(deck)
        return publish_release(deck, path, load_settings())
    except FileNotFoundError:
        raise HTTPException(404, "deck introuvable")
    except ProtocolError as exc:
        raise HTTPException(409, str(exc))
    except PublishError as exc:
        raise HTTPException(502, str(exc))


@app.delete("/api/decks/{deck_id}/publish")
def delete_publish(deck_id: str) -> dict:
    try:
        deck = _load(deck_id)
        return delete_release_asset(release_asset_name(deck), load_settings())
    except FileNotFoundError:
        raise HTTPException(404, "deck introuvable")
    except PublishError as exc:
        text = str(exc)
        raise HTTPException(404 if "introuvable" in text else 502, text) from exc


@app.post("/api/decks/{deck_id}/anki")
def post_anki(deck_id: str) -> dict:
    try:
        deck = _load(deck_id)
        path = export_apkg(deck, anki_name=anki_deck_name(deck))
        deck.stages["export"] = "passed"
        save_deck(deck)
        return send_to_anki(deck, path, load_settings())
    except FileNotFoundError:
        raise HTTPException(404, "deck introuvable")
    except ProtocolError as exc:
        raise HTTPException(409, str(exc))
    except AnkiConnectError as exc:
        raise HTTPException(502, str(exc))


MEDIA_TYPES = {
    ".mp3": "audio/mpeg",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
}


@app.get("/api/legacy-audio/{language}/{filename}")
def download_legacy_audio(language: str, filename: str):
    path = legacy_audio_path(language, filename)
    if path is None:
        raise HTTPException(404, "média introuvable")
    return FileResponse(path, media_type=MEDIA_TYPES.get(path.suffix.lower()))


@app.get("/api/cache-media/{language}/{filename}")
def download_cache_media(language: str, filename: str):
    from .cache import cache_media_path
    path = cache_media_path(language, filename)
    if path is None:
        raise HTTPException(404, "média introuvable")
    return FileResponse(path, media_type=MEDIA_TYPES.get(path.suffix.lower()))


@app.get("/api/media/{deck_id}/{filename}")
def download_media(deck_id: str, filename: str):
    path = MEDIA_DIR / deck_id / filename
    if not path.exists():
        raise HTTPException(404, "média introuvable")
    return FileResponse(path, media_type=MEDIA_TYPES.get(path.suffix.lower()))


@app.get("/api/exports/{filename}")
def download_export(filename: str):
    path = EXPORTS_DIR / filename
    if not path.exists():
        raise HTTPException(404, "fichier introuvable")
    media = "application/octet-stream" if path.suffix == ".apkg" else "text/csv"
    return FileResponse(path, filename=filename, media_type=media)


from .web import mount_frontend

mount_frontend(app, os.environ.get("LEARNING_WEB_DIR", ""))
