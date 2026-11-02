from __future__ import annotations

import json
import re
import shutil
import threading
from pathlib import Path

from .models import Card, Deck
from .paths import DATA_DIR, MEDIA_DIR
from .protocol import fold
from .services.audio import safe_name

CACHE_DIR = DATA_DIR / "cache"
CACHE_MEDIA = CACHE_DIR / "media"

_lock = threading.RLock()
_mem: dict[str, dict[str, dict]] = {}
_scenes_mem: dict | None = None
_images_mem: dict | None = None


def reset() -> None:
    global _scenes_mem, _images_mem
    with _lock:
        _mem.clear()
        _scenes_mem = None
        _images_mem = None

_TEXT_FIELDS = ("french", "ipa", "phrase", "phrase_fr", "category")
_SENSE_SPLIT = re.compile(r"\s*[,;/]\s*")


def _file(language: str, target: str) -> Path:
    lang = (language or "und").strip() or "und"
    dest = (target or "fr").strip() or "fr"
    return CACHE_DIR / f"{lang}__{dest}.json"


def _ensure() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_MEDIA.mkdir(parents=True, exist_ok=True)


def _load(language: str, target: str) -> dict[str, dict]:
    key = f"{language}__{target}"
    cached = _mem.get(key)
    if cached is not None:
        return cached
    path = _file(language, target)
    data: dict[str, dict] = {}
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data = {str(name): dict(value) for name, value in raw.items() if isinstance(value, dict)}
        except json.JSONDecodeError:
            data = {}
    _mem[key] = data
    return data


def _save(language: str, target: str, data: dict[str, dict]) -> None:
    _ensure()
    path = _file(language, target)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def get(language: str, target: str, lemma: str) -> dict | None:
    key = fold(lemma)
    if not key:
        return None
    with _lock:
        hit = _load(language, target).get(key)
        return dict(hit) if hit else None


def put(language: str, target: str, lemma: str, fields: dict) -> None:
    key = fold(lemma)
    if not key:
        return
    with _lock:
        data = _load(language, target)
        row = dict(data.get(key) or {})
        for name, value in fields.items():
            text = (value or "").strip() if isinstance(value, str) else value
            if text:
                row[name] = text
        data[key] = row
        _save(language, target, data)


def apply_card(card: Card, language: str, target: str) -> bool:
    hit = get(language, target, card.lemma or card.surface) or {}
    changed = False
    same_sense = not card.french.strip() or fold(card.french) == fold(hit.get("french", ""))
    same_phrase = not card.phrase.strip() or fold(card.phrase) == fold(hit.get("phrase", ""))
    for name in _TEXT_FIELDS:
        if name in {"phrase", "phrase_fr", "category"} and not same_sense:
            continue
        if name == "phrase_fr" and not same_phrase:
            continue
        current = getattr(card, name, "") or ""
        incoming = (hit.get(name) or "").strip()
        if incoming and not current.strip():
            setattr(card, name, incoming)
            changed = True
    if target == "fr" and not card.phrase.strip() and not card.phrase_fr.strip() and card.french.strip():
        scene = lookup_scene(card.french)
        if scene.get("phrase_fr"):
            card.phrase_fr = scene["phrase_fr"]
            changed = True
    audio = (hit.get("audio") or "").strip()
    if audio and not (card.audio_url or "").strip():
        card.audio_url = f"/api/cache-media/{language}/{audio}"
        changed = True
    if not (card.image_url or "").strip():
        from .image_library import lookup, assign
        asset = lookup(card, target)
        if asset:
            assign(card, asset, "contextual_cache")
            changed = True
    return changed


def apply_deck(deck: Deck) -> int:
    target = deck.target_language or "fr"
    filled = 0
    for card in deck.cards:
        if apply_card(card, deck.language, target):
            filled += 1
    return filled


def _store_audio(language: str, lemma: str, audio_url: str) -> str:
    if not audio_url:
        return ""
    name = safe_name(lemma) + ".mp3"
    if audio_url.startswith("/api/cache-media/"):
        return Path(audio_url).name
    dest_dir = CACHE_MEDIA / language
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / name
    src = _resolve_audio(audio_url)
    if src and src.exists() and src != dest:
        if not dest.exists() or src.stat().st_mtime > dest.stat().st_mtime:
            shutil.copy2(src, dest)
    return name if dest.exists() else ""


def _resolve_audio(url: str) -> Path | None:
    if url.startswith("/api/cache-media/"):
        rest = url[len("/api/cache-media/"):]
        lang, _, name = rest.partition("/")
        path = CACHE_MEDIA / lang / name
        return path if path.is_file() else None
    if url.startswith("/api/media/"):
        rest = url[len("/api/media/"):]
        deck_id, _, name = rest.partition("/")
        path = MEDIA_DIR / deck_id / name
        return path if path.is_file() else None
    if url.startswith("/api/legacy-audio/"):
        rest = url[len("/api/legacy-audio/"):]
        lang, _, name = rest.partition("/")
        from .legacy import AUDIO_DIRS
        folder = AUDIO_DIRS.get(lang)
        if folder:
            path = folder / name
            return path if path.is_file() else None
    return None


def remember_deck(deck: Deck) -> int:
    target = deck.target_language or "fr"
    language = deck.language
    stored = 0
    images = 0
    with _lock:
        data = _load(language, target)
        for card in deck.cards:
            key = fold(card.lemma or card.surface)
            if not key:
                continue
            row = dict(data.get(key) or {})
            before = dict(row)
            for name in _TEXT_FIELDS:
                value = (getattr(card, name, "") or "").strip()
                if value:
                    row[name] = value
            audio_name = _store_audio(language, card.lemma or card.surface, card.audio_url or "")
            if audio_name:
                row["audio"] = audio_name
            # V1 aliases are retained for historical inspection only. Do not
            # overwrite their shared concept files with new scene-specific media.
            if not card.image_asset_id and remember_image(card, flush=False):
                images += 1
                url, source = lookup_image(card.lemma, card.french, language)
                if url:
                    row["image_url"] = url
                    if source:
                        row["image_source"] = source
            elif _usable_cached_image(card.image_url):
                row["image_url"] = card.image_url.strip()
                if card.image_source:
                    row["image_source"] = card.image_source
            if row != before:
                data[key] = row
                stored += 1
        if stored:
            _save(language, target, data)
        if images:
            _save_images_index(_load_images_index())
    if target == "fr":
        remember_scenes(deck.cards)
    return stored


def warm_from_decks() -> int:
    from .paths import DECKS_DIR

    total = 0
    if not DECKS_DIR.is_dir():
        return 0
    for path in DECKS_DIR.glob("*.json"):
        try:
            deck = Deck.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        total += remember_deck(deck)
    return total


def cache_media_path(language: str, filename: str) -> Path | None:
    name = Path(filename).name
    if not name or name.startswith("."):
        return None
    path = CACHE_MEDIA / language / name
    return path if path.is_file() else None


def _usable_cached_image(url: str) -> bool:
    text = (url or "").strip()
    if not text:
        return False
    is_local = text.startswith("/api/cache-media/") or text.startswith("/api/media/")
    return bool(is_local and _resolve_image_file(text))


def _sense_aliases(translation: str) -> list[str]:
    full = fold(translation)
    aliases: list[str] = []
    for value in [full, *[fold(part) for part in _SENSE_SPLIT.split(translation or "")]]:
        if value and value not in aliases:
            aliases.append(value)
    return aliases


def _scenes_index_path() -> Path:
    return CACHE_DIR / "scenes.json"


def _load_scenes_index() -> dict:
    global _scenes_mem
    if _scenes_mem is not None:
        return _scenes_mem
    path = _scenes_index_path()
    if not path.exists():
        raw = {}
    else:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            raw = {}
    if not isinstance(raw, dict):
        raw = {}
    _scenes_mem = {
        "scenes": dict(raw.get("scenes") or {}),
        "aliases": dict(raw.get("aliases") or {}),
    }
    return _scenes_mem


def _save_scenes_index(data: dict) -> None:
    _ensure()
    path = _scenes_index_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def remember_scenes(cards: list[Card]) -> int:
    changed_count = 0
    with _lock:
        data = _load_scenes_index()
        dirty = False
        for card in cards:
            phrase_fr = (card.phrase_fr or "").strip()
            aliases = _sense_aliases(card.french)
            if not phrase_fr or not aliases or (card.category or "").casefold() == "grammaire":
                continue
            scene_id = next(
                (data["aliases"][alias] for alias in aliases if alias in data["aliases"]),
                f"sense:{aliases[0]}",
            )
            current = dict(data["scenes"].get(scene_id) or {})
            changed = not current.get("phrase_fr")
            if changed:
                current.update({
                    "id": scene_id,
                    "phrase_fr": phrase_fr,
                    "category": (card.category or "").strip(),
                })
            labels = list(current.get("labels") or [])
            aliases_changed = False
            for alias in aliases:
                if alias not in labels:
                    labels.append(alias)
                    aliases_changed = True
                if data["aliases"].get(alias) != scene_id:
                    data["aliases"][alias] = scene_id
                    aliases_changed = True
            if not changed and not aliases_changed:
                continue
            current["labels"] = labels
            data["scenes"][scene_id] = current
            dirty = True
            changed_count += 1
        if dirty:
            _save_scenes_index(data)
    return changed_count


def lookup_scene(translation: str) -> dict:
    aliases = _sense_aliases(translation)
    if not aliases:
        return {}
    with _lock:
        data = _load_scenes_index()
    for alias in aliases:
        scene_id = data["aliases"].get(alias)
        if scene_id and scene_id in data["scenes"]:
            return dict(data["scenes"][scene_id])
    return {}


def _images_index_path() -> Path:
    return CACHE_DIR / "images.json"


def _normalize_images_index(raw: dict) -> dict:
    if isinstance(raw.get("concepts"), dict) and isinstance(raw.get("aliases"), dict):
        return {"concepts": dict(raw["concepts"]), "aliases": dict(raw["aliases"])}
    concepts: dict[str, dict] = {}
    aliases: dict[str, str] = {}
    for key, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        cid = str(entry.get("id") or f"legacy:{fold(key)}")
        concepts[cid] = {**entry, "id": cid}
        aliases[fold(key)] = cid
        for lemma in entry.get("lemmas") or []:
            if fold(lemma):
                aliases[fold(lemma)] = cid
    return {"concepts": concepts, "aliases": aliases}


def _read_images_index() -> dict:
    path = _images_index_path()
    if not path.exists():
        return {"concepts": {}, "aliases": {}}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"concepts": {}, "aliases": {}}
    if not isinstance(raw, dict):
        return {"concepts": {}, "aliases": {}}
    return _normalize_images_index(raw)


def _load_images_index() -> dict:
    global _images_mem
    with _lock:
        if _images_mem is None:
            _images_mem = _read_images_index()
        return _images_mem


def _merge_images_index(base: dict, extra: dict) -> dict:
    """Fold our entries into whatever another process wrote while we held ours."""
    concepts = {cid: dict(entry) for cid, entry in base["concepts"].items()}
    for cid, entry in extra["concepts"].items():
        merged = concepts.get(cid) or {}
        labels = {**(merged.get("labels") or {}), **(entry.get("labels") or {})}
        merged.update(entry)
        if labels:
            merged["labels"] = labels
        concepts[cid] = merged
    return {"concepts": concepts, "aliases": {**base["aliases"], **extra["aliases"]}}


def _save_images_index(data: dict) -> None:
    import time
    from uuid import uuid4

    global _images_mem
    _ensure()
    path = _images_index_path()
    merged = _merge_images_index(_read_images_index(), data)
    tmp = path.with_name(f"images.{uuid4().hex}.tmp")
    tmp.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    last_error: OSError | None = None
    for attempt in range(12):
        try:
            tmp.replace(path)
            _images_mem = merged
            return
        except OSError as exc:
            last_error = exc
            time.sleep(0.1 * (attempt + 1))
    tmp.unlink(missing_ok=True)
    if last_error:
        raise last_error


def _resolve_image_file(url: str) -> Path | None:
    raw = (url or "").strip()
    if raw.startswith("/api/media/"):
        rest = raw[len("/api/media/"):]
        deck_id, _, name = rest.partition("/")
        path = MEDIA_DIR / deck_id / name
        return path if path.is_file() else None
    if raw.startswith("/api/cache-media/"):
        rest = raw[len("/api/cache-media/"):]
        folder, _, name = rest.partition("/")
        path = CACHE_MEDIA / folder / name
        return path if path.is_file() else None
    return None


def _concept_url(entry: dict) -> tuple[str, str]:
    from .image_library import source_of, blocked
    if blocked(source_of(entry.get("url", ""), entry.get("source", ""))):
        return "", ""
    file_name = (entry.get("file") or "").strip()
    folder = (entry.get("folder") or "concepts").strip() or "concepts"
    if file_name:
        for candidate in (CACHE_MEDIA / folder / file_name, CACHE_MEDIA / "sense" / file_name):
            if candidate.is_file():
                return f"/api/cache-media/{candidate.parent.name}/{file_name}", (entry.get("source") or "").strip()
    url = (entry.get("url") or "").strip()
    if _usable_cached_image(url) and _resolve_image_file(url):
        return url, (entry.get("source") or "").strip()
    return "", ""


def remember_image(
    card: Card, extra_labels: dict[str, str] | None = None, flush: bool = True
) -> bool:
    if card.skip_image or (card.category or "").casefold() == "grammaire":
        return False
    url = (card.image_url or "").strip()
    source = (card.image_source or "").strip()
    from .image_library import source_of, blocked
    if source_of(url, source) in {"wikimedia", "unsplash", "pexels"} or blocked(source_of(url, source)):
        return False
    generated = (
        source == "generated"
        or url.startswith("/api/media/")
        or url.startswith("/api/cache-media/sense/")
        or url.startswith("/api/cache-media/concepts/")
    )
    if not generated:
        return False
    if not url or not _usable_cached_image(url):
        return False
    sense_aliases = _sense_aliases(card.french)
    concept_id = (card.image_concept or "").strip() or f"gen:{(sense_aliases or [fold(card.lemma)])[0]}"
    if not concept_id:
        return False
    labels: dict[str, str] = {}
    for value in (extra_labels or {}).values():
        key = fold(value)
        if key:
            labels[key] = concept_id
    for value in (card.lemma, card.french, card.surface):
        key = fold(value)
        if key:
            labels[key] = concept_id
    for key in sense_aliases:
        labels[key] = concept_id
    entry: dict = {
        "id": concept_id,
        "source": source or "generated",
        "url": url,
        "folder": "concepts",
    }
    src = _resolve_image_file(url)
    if src:
        ext = src.suffix.lower() or ".jpg"
        dest_dir = CACHE_MEDIA / "concepts"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / (safe_name(concept_id) + ext)
        if src != dest:
            shutil.copy2(src, dest)
        if dest.is_file():
            entry["file"] = dest.name
            entry["url"] = f"/api/cache-media/concepts/{dest.name}"
    with _lock:
        data = _load_images_index()
        current = dict(data["concepts"].get(concept_id) or {})
        current.update(entry)
        known = dict(current.get("labels") or {})
        for key in labels:
            known[key] = key
        current["labels"] = known
        data["concepts"][concept_id] = current
        for key in labels:
            data["aliases"][key] = concept_id
        if flush:
            _save_images_index(data)
    return True


def lookup_image(
    lemma: str,
    translation: str = "",
    language: str = "",
    target: str = "fr",
    concept_id: str = "",
) -> tuple[str, str]:
    hit = get(language, target, lemma) if language else None
    if hit:
        url = (hit.get("image_url") or "").strip()
        if _usable_cached_image(url):
            return url, (hit.get("image_source") or "").strip()
    with _lock:
        data = _load_images_index()
    aliases = data.get("aliases") or {}
    concepts = data.get("concepts") or {}
    cid = (concept_id or "").strip()
    if cid not in concepts:
        cid = ""
        needles = [*_sense_aliases(translation), fold(lemma), fold(concept_id)]
        for alias in needles:
            if alias and alias in aliases:
                cid = aliases[alias]
                break
    if not cid:
        return "", ""
    return _concept_url(concepts.get(cid) or {})
