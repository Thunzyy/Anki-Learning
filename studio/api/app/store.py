from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from uuid import uuid4

from .models import Deck, Settings
from .paths import DECKS_DIR, SETTINGS_PATH, ensure_dirs
from .protocol import STAGES, feature_enabled


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _deck_path(deck_id: str):
    return DECKS_DIR / f"{deck_id}.json"


def save_deck(deck: Deck) -> Deck:
    ensure_dirs()
    deck.card_count = len(deck.cards)
    deck.updated_at = now_iso()
    path = _deck_path(deck.id)
    tmp = path.with_name(f"{path.stem}.{uuid4().hex}.tmp")
    tmp.write_text(deck.model_dump_json(indent=2), encoding="utf-8")
    last_error: OSError | None = None
    for _ in range(8):
        try:
            os.replace(tmp, path)
            return deck
        except OSError as exc:
            last_error = exc
            time.sleep(0.05)
    if tmp.exists():
        tmp.unlink(missing_ok=True)
    if last_error:
        raise last_error
    return deck


def load_deck(deck_id: str) -> Deck:
    path = _deck_path(deck_id)
    if not path.exists():
        raise FileNotFoundError(deck_id)
    raw = ""
    for attempt in range(6):
        raw = path.read_text(encoding="utf-8")
        if raw.strip().startswith("{"):
            break
        if attempt == 5:
            raise ValueError(f"deck {deck_id}: fichier JSON vide ou illisible")
        time.sleep(0.05)
    deck = Deck.model_validate_json(raw)
    deck.card_count = len(deck.cards)
    from .protocol import apply_normalize
    deck.cards = [apply_normalize(card) for card in deck.cards]
    from .pipeline import sync_stages
    return sync_stages(deck)


def list_decks() -> list[Deck]:
    ensure_dirs()
    decks: list[Deck] = []
    for path in sorted(DECKS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw["card_count"] = len(raw.get("cards") or [])
            raw["cards"] = []
            decks.append(Deck.model_validate(raw))
        except Exception:
            continue
    return decks


def library_image_stats() -> dict[str, int]:
    """Count current image URLs after the same normalization used by the editor.

    Shared URLs count once across decks/languages. History and unused library
    assets are excluded. Do not silently return an incomplete total on read errors.
    """
    from .protocol import apply_normalize

    ensure_dirs()
    urls: set[str] = set()
    illustrated = 0
    for path in DECKS_DIR.glob("*.json"):
        deck = Deck.model_validate_json(path.read_text(encoding="utf-8"))
        for card in deck.cards:
            url = apply_normalize(card).image_url.strip()
            if url:
                urls.add(url)
                illustrated += 1
    return {"images": len(urls), "illustrated_cards": illustrated}


def delete_deck(deck_id: str) -> None:
    path = _deck_path(deck_id)
    if path.exists():
        path.unlink()


def new_stage_map(deck: Deck | None = None) -> dict[str, str]:
    stages = {}
    for name in STAGES:
        if deck is not None and not feature_enabled(deck, name):
            stages[name] = "skipped"
        else:
            stages[name] = "pending"
    return stages


def load_settings() -> Settings:
    ensure_dirs()
    settings = Settings()
    if SETTINGS_PATH.exists():
        settings = Settings.model_validate_json(SETTINGS_PATH.read_text(encoding="utf-8"))
    env_map = {
        "anthropic_api_key": "ANTHROPIC_API_KEY",
        "openai_api_key": "OPENAI_API_KEY",
        "xai_api_key": "XAI_API_KEY",
        "unsplash_access_key": "UNSPLASH_ACCESS_KEY",
        "pexels_api_key": "PEXELS_API_KEY",
    }
    import os
    data = settings.model_dump()
    for field, env_name in env_map.items():
        if not data[field] and os.getenv(env_name):
            data[field] = os.getenv(env_name, "")
    settings = Settings(**data)
    from .services.images import DEFAULT_IMAGE_PROMPT, LEGACY_IMAGE_PROMPTS
    prompt = (settings.image_prompt or "").strip()
    if not prompt or prompt in LEGACY_IMAGE_PROMPTS:
        settings.image_prompt = DEFAULT_IMAGE_PROMPT
    return settings


def save_settings(settings: Settings) -> Settings:
    ensure_dirs()
    SETTINGS_PATH.write_text(settings.model_dump_json(indent=2), encoding="utf-8")
    return settings


def new_id() -> str:
    return uuid4().hex[:12]
