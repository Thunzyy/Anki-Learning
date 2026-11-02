"""Persistent, provenance-aware illustrations. Unknown context is never a match.

Context matching is deliberately conservative: a shared concept+scene identifier,
or an identical translated meaning AND example in the same target language.
No embeddings/visual understanding are implied by these deterministic keys.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from . import paths
from .models import Card
from .protocol import fold
from .store import now_iso

PROVIDERS = {"grok", "gemini", "openai", "wikimedia", "unsplash", "pexels", "generated", "unknown", "manual"}


def source_of(url: str, source: str = "") -> str:
    host = (urlparse(url or "").hostname or "").lower()
    for provider, domains in {
        "wikimedia": ("wikimedia.org", "wikipedia.org"),
        "unsplash": ("unsplash.com",), "pexels": ("pexels.com",),
    }.items():
        if any(host == domain or host.endswith("." + domain) for domain in domains):
            return provider
    value = (source or "").strip().lower()
    value = {"xai": "grok", "google": "gemini"}.get(value, value)
    return value if value in PROVIDERS else "unknown"


@contextmanager
def database():
    paths.DATA_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(paths.DATA_DIR / "image-library.sqlite3", timeout=30)
    db.row_factory = sqlite3.Row
    try:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS assets (
                id TEXT PRIMARY KEY, url TEXT NOT NULL, source TEXT NOT NULL,
                metadata TEXT NOT NULL, retired INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS contexts (
                context TEXT NOT NULL, asset TEXT NOT NULL,
                PRIMARY KEY(context, asset)
            );
            CREATE TABLE IF NOT EXISTS policy (source TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY, created TEXT NOT NULL, data TEXT NOT NULL
            );
        """)
        yield db
        db.commit()
    finally:
        db.close()


def local_path(url: str) -> Path | None:
    for prefix, root in (("/api/media/", paths.MEDIA_DIR),
                         ("/api/cache-media/", paths.DATA_DIR / "cache/media")):
        if url.startswith(prefix):
            candidate = (root / url[len(prefix):]).resolve()
            if candidate.is_relative_to(root.resolve()) and candidate.is_file():
                return candidate
    return None


def context_key(card: Card, target: str = "fr") -> str:
    if card.image_concept.strip() and card.image_scene.strip():
        # Explicit IDs are shared across source AND translation languages.
        parts = ["scene", card.image_concept.strip(), card.image_scene.strip()]
    elif card.french.strip() and card.phrase_fr.strip():
        parts = ["example", target.lower(), fold(card.french), fold(card.phrase_fr)]
    else:
        return ""
    return json.dumps(parts, ensure_ascii=False)


def blocked(source: str) -> bool:
    with database() as db:
        return db.execute("SELECT 1 FROM policy WHERE source=?", (source,)).fetchone() is not None


def register(card: Card, target: str = "fr", *, verified_context: bool = False,
             metadata: dict | None = None, replace_context: bool = False) -> str:
    if replace_context and not verified_context:
        raise ValueError("Replacing an image context requires explicit verification")
    url = card.image_url.strip()
    if not url:
        return ""
    source = source_of(url, card.image_source)
    path = local_path(url)
    # Source is part of the identity: conflicting provenance must not be overwritten.
    digest = hashlib.sha256(path.read_bytes() if path else url.encode()).hexdigest()
    asset_id = hashlib.sha256(f"{source}:{digest}".encode()).hexdigest()
    details = {"sha256": digest if path else "", "source": source,
               "original_url": url, "recorded_at": now_iso(),
               "context_status": "verified" if verified_context else "unverified",
               "word": card.lemma, "meaning": card.french, "language": card.language,
               "example_translation": card.phrase_fr, "target_language": target,
               **(metadata or {})}
    key = context_key(card, target) if verified_context else ""
    if replace_context and not key:
        raise ValueError("Replacing an image context requires a context key")
    with database() as db:
        retired = db.execute("SELECT 1 FROM policy WHERE source=?", (source,)).fetchone() is not None
        db.execute("INSERT OR IGNORE INTO assets(id,url,source,metadata,retired) VALUES(?,?,?,?,?)",
                   (asset_id, url, source, json.dumps(details, ensure_ascii=False), int(retired)))
        if key and not retired:
            if replace_context:
                # A reviewed correction supersedes this association only. The
                # asset remains available for every other verified context.
                previous = [row[0] for row in db.execute(
                    "SELECT asset FROM contexts WHERE context=? AND asset<>? ORDER BY asset",
                    (key, asset_id))]
                if previous:
                    db.execute("DELETE FROM contexts WHERE context=? AND asset<>?", (key, asset_id))
                    event = {"type": "context_replacement", "context": key,
                             "revoked_assets": previous, "replacement_asset": asset_id,
                             "reason": (metadata or {}).get("origin", "explicit_registration"),
                             "review": (metadata or {}).get("review", {}),
                             "scene_manifest": (metadata or {}).get("scene_manifest", "")}
                    db.execute("INSERT INTO events VALUES(?,?,?)",
                               (uuid4().hex, now_iso(), json.dumps(event, ensure_ascii=False)))
            db.execute("INSERT OR IGNORE INTO contexts VALUES(?,?)", (key, asset_id))
            existing = json.loads(db.execute("SELECT metadata FROM assets WHERE id=?", (asset_id,)).fetchone()[0])
            existing["context_status"] = "verified"
            existing["last_context_validation"] = {"at": now_iso(), "context": key,
                                                   "basis": (metadata or {}).get("origin", "explicit_registration")}
            db.execute("UPDATE assets SET metadata=? WHERE id=?", (json.dumps(existing, ensure_ascii=False), asset_id))
        row = db.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
    card.image_asset_id = asset_id
    card.image_source = source
    card.image_provenance = json.loads(row["metadata"])
    return asset_id


def lookup(card: Card, target: str = "fr", source: str = "") -> dict | None:
    if card.skip_image or card.category.casefold() == "grammaire":
        return None
    key = context_key(card, target)
    if not key:
        return None
    with database() as db:
        rows = db.execute("""SELECT a.* FROM assets a JOIN contexts c ON c.asset=a.id
            WHERE c.context=? AND a.retired=0
            AND NOT EXISTS(SELECT 1 FROM policy p WHERE p.source=a.source)
            ORDER BY a.rowid DESC""", (key,)).fetchall()
    for row in rows:
        if source and row["source"] != source:
            continue
        if row["url"].startswith("/api/") and not local_path(row["url"]):
            continue
        return {**dict(row), "metadata": json.loads(row["metadata"])}
    return None


def snapshot(card: Card) -> dict:
    return {"url": card.image_url, "source": source_of(card.image_url, card.image_source),
            "asset_id": card.image_asset_id, "provenance": card.image_provenance}


def assign(card: Card, asset: dict | None, reason: str) -> None:
    before = snapshot(card)
    card.image_url = asset["url"] if asset else ""
    card.image_source = asset["source"] if asset else ""
    card.image_asset_id = asset["id"] if asset else ""
    card.image_provenance = asset["metadata"] if asset else {}
    after = snapshot(card)
    if before != after:
        card.image_history.append({"at": now_iso(), "reason": reason, "before": before, "after": after})


def record_event(data: dict) -> str:
    event_id = uuid4().hex
    with database() as db:
        db.execute("INSERT INTO events VALUES(?,?,?)", (event_id, now_iso(), json.dumps(data, ensure_ascii=False)))
    return event_id


def inventory() -> dict:
    with database() as db:
        counts = [dict(row) for row in db.execute(
            "SELECT source, retired, count(*) AS count FROM assets GROUP BY source,retired")]
        policies = [row[0] for row in db.execute("SELECT source FROM policy")]
        contexts = db.execute("SELECT count(DISTINCT asset) FROM contexts").fetchone()[0]
    return {"sources": counts, "blocked_sources": policies, "contextualized_assets": contexts}


def search(query: str = "", source: str = "", limit: int = 24) -> list[dict]:
    # Names and historic labels help humans find candidates; they never grant reuse.
    with database() as db:
        rows = db.execute("""SELECT a.* FROM assets a WHERE retired=0
            AND NOT EXISTS(SELECT 1 FROM policy p WHERE p.source=a.source)
            AND (?='' OR source=?) AND (?='' OR metadata LIKE ? OR url LIKE ?)
            ORDER BY rowid DESC LIMIT ?""",
            (source, source, query, f"%{query}%", f"%{query}%", min(max(limit, 1), 100))).fetchall()
    return [{**dict(row), "metadata": json.loads(row["metadata"])} for row in rows
            if not row["url"].startswith("/api/") or local_path(row["url"])]


def import_manifest(manifest: Path) -> dict:
    """Import local CLI output with explicit provenance; never infer from filenames."""
    import shutil
    rows = json.loads(manifest.read_text(encoding="utf-8-sig"))
    if not isinstance(rows, list):
        raise ValueError("Manifest must be a JSON array")
    prepared = []
    for row in rows:
        source = row.get("source", "").lower()
        if source not in PROVIDERS:
            raise ValueError("Each image needs an explicit supported source")
        path = (manifest.parent / row["path"]).resolve()
        if not path.is_file() or path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
            raise ValueError(f"Invalid image file: {path.name}")
        card = Card(id="import", lemma=row.get("word", ""), surface=row.get("word", ""),
                    language=row.get("language", "und"), french=row.get("meaning", ""),
                    phrase_fr=row.get("example_translation", ""), category=row.get("category", ""),
                    image_concept=row.get("concept", ""), image_scene=row.get("scene", ""),
                    image_source=source)
        target = row.get("target_language", "fr")
        if not context_key(card, target):
            raise ValueError("Supply concept+scene IDs or meaning+example_translation with target_language")
        prepared.append((row, path, card, target))
    assets = []
    for row, path, card, target in prepared:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        dest = paths.DATA_DIR / "cache/media/library" / (digest + path.suffix.lower())
        dest.parent.mkdir(parents=True, exist_ok=True)
        if path != dest and not dest.exists():
            shutil.copy2(path, dest)
        card.image_url = f"/api/cache-media/library/{dest.name}"
        assets.append(register(card, target, verified_context=True, metadata={
            "model": row.get("model", ""), "prompt": row.get("prompt", ""),
            "origin": "user_manifest", "original_file": path.name,
            "created_at": row.get("created_at", ""),
        }))
    return {"imported": len(assets), "assets": assets}
