from __future__ import annotations

import json
import shutil
from collections import Counter
from pathlib import Path
from uuid import uuid4

from . import image_library as library, paths
from .models import Card, Deck, ImagesReplace, Settings
from .store import now_iso


def backup_deck(deck: Deck) -> str:
    folder = paths.DATA_DIR / "image-backups" / uuid4().hex
    folder.mkdir(parents=True, exist_ok=False)
    (folder / f"{deck.id}.json").write_text(deck.model_dump_json(indent=2), encoding="utf-8")
    return folder.name


def deck_inventory(deck: Deck) -> dict:
    return {"sources": dict(Counter(library.source_of(c.image_url, c.image_source) if c.image_url else "missing"
                                    for c in deck.cards)),
            "history": [{"card_id": c.id, "word": c.surface, "events": c.image_history}
                        for c in deck.cards if c.image_history]}


def selected_cards(deck: Deck, request: ImagesReplace) -> list[Card]:
    if request.source not in library.PROVIDERS | {"all", "missing"}:
        raise ValueError("Unknown source filter")
    if set(request.ids) - {c.id for c in deck.cards}:
        raise ValueError("Unknown card ID")
    return [c for c in deck.cards
            if (not request.ids or c.id in request.ids)
            and (request.destination == "remove" or not c.skip_image and c.category.casefold() != "grammaire")
            and (request.source == "all" or
                 (library.source_of(c.image_url, c.image_source) if c.image_url else "missing") == request.source)
            and (request.destination != "remove" or c.image_url)]


def replace_images(deck: Deck, request: ImagesReplace, settings: Settings) -> dict:
    from .services.images import fetch_image
    selected = selected_cards(deck, request)
    destination = "openai" if request.destination == "codex" else request.destination
    if destination not in {"library", "remove"} and library.blocked(destination):
        raise ValueError(f"Source disabled: {destination}")
    preview = []
    for card in selected:
        asset = library.lookup(card, deck.target_language,
                               "" if destination == "library" else destination) if destination != "remove" else None
        preview.append({"id": card.id, "word": card.surface, "before": library.snapshot(card),
                        "replacement": asset, "action": "remove" if destination == "remove" else
                        "reuse" if asset else "generate" if request.generate_missing and destination in
                        {"grok", "openai", "wikimedia", "unsplash", "pexels"} else "unmatched"})
    result = {"total": len(selected), "matched": sum(p["action"] == "reuse" for p in preview),
              "unmatched": sum(p["action"] == "unmatched" for p in preview),
              "items": preview, "changed": 0, "errors": [], "backup": ""}
    if request.preview or not selected:
        return result
    result["backup"] = backup_deck(deck)
    by_id = {c.id: c for c in selected}
    for item in preview[:request.limit]:
        card = by_id[item["id"]]
        try:
            asset = item["replacement"]
            if item["action"] == "generate":
                candidate = card.model_copy(deep=True)
                candidate.image_url = ""
                candidate.image_source = ""
                candidate.image_asset_id = ""
                candidate.image_provenance = {}
                url, source = fetch_image(candidate, destination, settings, deck.id,
                                          allow_generate=destination in {"grok", "openai"}, strict_source=True,
                                          generation_provider="codex" if destination == "openai" else destination,
                                          target_language=deck.target_language)
                if not url:
                    raise ValueError("No image returned by the selected provider")
                if library.source_of(url, source) != destination:
                    raise ValueError("Provider returned a different source")
                asset = {"id": candidate.image_asset_id, "url": url, "source": source,
                         "metadata": candidate.image_provenance}
            if asset or destination == "remove":
                before = library.snapshot(card)
                library.assign(card, asset, f"replace:{request.source}->{destination}")
                result["changed"] += before != library.snapshot(card)
            else:
                result["errors"].append({"id": card.id, "message": "No verified contextual match; existing image kept"})
        except Exception as exc:
            result["errors"].append({"id": card.id, "message": str(exc)})
    return result


def migrate(*, remove_source: str = "", apply: bool = False) -> dict:
    """Offline migration: index legacy provenance, optionally retire a provider.

    Back up JSON before modification. Quarantine identifiable local media, retain
    tombstones, and persist a policy so stale caches cannot reintroduce the source.
    Run with the app and other writers stopped.
    """
    if remove_source and remove_source not in library.PROVIDERS:
        raise ValueError("Unknown source")
    deck_files = sorted(paths.DECKS_DIR.glob("*.json"))
    decks = [(p, Deck.model_validate_json(p.read_text(encoding="utf-8"))) for p in deck_files]
    cache_dir = paths.DATA_DIR / "cache"
    cache_files = sorted(cache_dir.glob("*.json"))
    cache_docs = [(p, json.loads(p.read_text(encoding="utf-8"))) for p in cache_files]
    report = {"decks": len(decks), "cards": 0, "removed": 0, "sources": {}, "backup": "", "quarantined": 0}
    source_counts = Counter()
    candidates = []
    for _, deck in decks:
        for card in deck.cards:
            if card.image_url:
                source_counts[library.source_of(card.image_url, card.image_source)] += 1
                candidates.append(card)
    deck_reference_count = len(candidates)
    for _, data in cache_docs:
        for entry in (data.get("concepts") or {}).values():
            url = entry.get("url") or ""
            if not url and entry.get("file"):
                url = f"/api/cache-media/{entry.get('folder', 'concepts')}/{entry['file']}"
            if url:
                candidates.append(Card(id="legacy", lemma="", surface="", language="und",
                                       image_url=url, image_source=entry.get("source", "")))
        for row in data.values():
            if isinstance(row, dict) and row.get("image_url"):
                candidates.append(Card(id="legacy", lemma="", surface="", language="und",
                                       image_url=row["image_url"], image_source=row.get("image_source", "")))
    cache_matches = [c.image_url for c in candidates[deck_reference_count:]
                     if library.source_of(c.image_url, c.image_source) == remove_source]
    report["cache_references_to_remove"] = len(cache_matches)
    report["cache_images_to_retire"] = len(set(cache_matches))
    # Include imported assets that have never been assigned to a deck.
    if (paths.DATA_DIR / "image-library.sqlite3").exists():
        with library.database() as db:
            for entry in db.execute("SELECT url,source FROM assets"):
                candidates.append(Card(id="library", lemma="", surface="", language="und",
                                       image_url=entry["url"], image_source=entry["source"]))
    report["cards"] = sum(source_counts.values())
    report["sources"] = dict(source_counts)
    report["removed"] = sum(1 for _, d in decks for c in d.cards if c.image_url
                            and library.source_of(c.image_url, c.image_source) == remove_source)
    if not apply:
        return report
    folder = paths.DATA_DIR / "image-backups" / ("migration-" + uuid4().hex)
    folder.mkdir(parents=True)
    report["backup"] = str(folder)
    for path, _ in [*decks, *cache_docs]:
        dest = folder / path.relative_to(paths.DATA_DIR)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
    if (paths.DATA_DIR / "image-library.sqlite3").exists():
        import sqlite3
        with library.database() as db, sqlite3.connect(folder / "image-library.sqlite3") as dest:
            db.backup(dest)
    known = {}
    removable_files = set()
    retained_files = set()
    for card in candidates:
        key = (card.image_url, library.source_of(card.image_url, card.image_source))
        path = library.local_path(card.image_url)
        if path:
            (removable_files if key[1] == remove_source else retained_files).add(path)
        if key not in known:
            library.register(card, metadata={"origin": "legacy_migration", "context_status": "unverified"})
            known[key] = (card.image_asset_id, card.image_source, card.image_provenance)
        else:
            card.image_asset_id, card.image_source, card.image_provenance = known[key]
    if remove_source:
        with library.database() as db:
            db.execute("INSERT OR IGNORE INTO policy VALUES(?)", (remove_source,))
            db.execute("UPDATE assets SET retired=1 WHERE source=?", (remove_source,))
        for _, deck in decks:
            for card in deck.cards:
                if card.image_url and library.source_of(card.image_url, card.image_source) == remove_source:
                    library.assign(card, None, f"retire:{remove_source}")
        for path, data in cache_docs:
            if "concepts" in data:
                removed = {key for key, e in data["concepts"].items()
                           if library.source_of(e.get("url", ""), e.get("source", "")) == remove_source}
                data["concepts"] = {k: v for k, v in data["concepts"].items() if k not in removed}
                data["aliases"] = {k: v for k, v in data.get("aliases", {}).items() if v not in removed}
            for row in data.values():
                if isinstance(row, dict) and row.get("image_url") and library.source_of(
                        row["image_url"], row.get("image_source", "")) == remove_source:
                    row.pop("image_url", None)
                    row.pop("image_source", None)
            _write_json(path, data)
        for path in removable_files - retained_files:
            dest = folder / "quarantine" / path.relative_to(paths.DATA_DIR)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(dest))
            report["quarantined"] += 1
    for path, deck in decks:
        _write_json(path, deck.model_dump())
    report["event_id"] = library.record_event({"action": "migration", "at": now_iso(), **report})
    _write_json(folder / "report.json", report)
    from .cache import reset
    reset()
    return report


def _write_json(path: Path, data: dict):
    tmp = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Illustration provenance, import and migration (offline)")
    parser.add_argument("--import-manifest", type=Path)
    parser.add_argument("--remove-source", default="")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.import_manifest:
        result = library.import_manifest(args.import_manifest)
    else:
        result = migrate(remove_source=args.remove_source, apply=args.apply)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
