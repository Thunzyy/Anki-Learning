"""Apply reviewed multilingual scene manifests to local decks.

This is an offline maintenance command: stop the API writer before --apply.
Preview validates every card, input revision and image digest without writing.
The complete original JSON and registry are retained in a journaled backup.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import unicodedata
from pathlib import Path
from uuid import uuid4

from . import image_library as library, paths
from .models import Card
from .protocol import is_function_word, validate_card_sentence, validate_card_image
from .store import now_iso

FIELDS = {"french", "category", "phrase", "phrase_fr", "image_concept", "image_scene",
          "image_url", "image_source", "skip_image"}
WORD_FIELDS = {"lemma", "surface"}


def _unaccent(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", value)
                   if unicodedata.category(c) != "Mn").casefold()


def reviewed_spelling(original: dict, update: dict) -> dict:
    """Only explicit, revision-guarded diacritic repairs may alter word forms."""
    correction = update.get("word_correction")
    if correction is None:
        return {}
    if not isinstance(correction, dict) or not correction.get("reason", "").strip():
        raise ValueError("Spelling correction requires a review reason")
    result = {}
    for field in WORD_FIELDS:
        before, after = correction.get("before_" + field), correction.get(field)
        if not isinstance(before, str) or before != original.get(field) or not isinstance(after, str) or not after.strip():
            raise ValueError("Spelling correction does not match original word")
        if _unaccent(before) != _unaccent(after):
            raise ValueError("Only diacritic spelling corrections are permitted")
        result[field] = after
    if original.get("audio_url") and any(result[k] != original.get(k) for k in WORD_FIELDS):
        review = correction.get("audio_review")
        if not (isinstance(review, dict)
                and review.get("status") == "orthography_only_ipa_verified"
                and review.get("pronunciation_unchanged") is True
                and review.get("expected_audio_url") == original["audio_url"]
                and isinstance(original.get("ipa"), str) and original["ipa"].strip()
                and review.get("expected_ipa") == original["ipa"]
                and isinstance(review.get("reason"), str) and review["reason"].strip()):
            raise ValueError("Review word audio before changing its spelling")
    return result


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def prepare(manifest: dict) -> list[dict]:
    """Validate all changes before mutating any deck or registering contexts."""
    if manifest.get("version") != 1:
        raise ValueError("Unsupported scene manifest version")
    changes = []
    seen_decks: set[str] = set()
    scenes: dict[tuple[str, str], str] = {}
    for spec in manifest["decks"]:
        deck_id = spec["id"]
        if not re.fullmatch(r"[A-Za-z0-9_-]+", deck_id) or deck_id in seen_decks:
            raise ValueError("Invalid or duplicate deck ID")
        seen_decks.add(deck_id)
        path = paths.DECKS_DIR / f"{deck_id}.json"
        raw = path.read_bytes()
        if sha(raw) != spec["expected_sha256"]:
            raise ValueError(f"Deck changed since review: {deck_id}")
        deck = json.loads(raw)
        cards = {c["id"]: c for c in deck["cards"]}
        if len(cards) != len(deck["cards"]):
            raise ValueError(f"Duplicate source card IDs: {deck_id}")
        seen_cards: set[str] = set()
        updates = []
        for update in spec["updates"]:
            card_id = update["id"]
            if card_id not in cards or card_id in seen_cards:
                raise ValueError(f"Unknown or duplicate card: {card_id}")
            seen_cards.add(card_id)
            fields = update["fields"]
            if set(fields) - FIELDS:
                raise ValueError(f"Forbidden card fields: {card_id}")
            spelling = reviewed_spelling(cards[card_id], update)
            card = Card.model_validate({**cards[card_id], **fields, **spelling})
            if is_function_word(card.lemma, card.language):
                card.skip_image = True
                card.category = "Grammaire"
            errors = validate_card_sentence(card) + validate_card_image(card)
            if not card.french.strip() or not card.image_concept.strip() or not card.image_scene.strip():
                errors.append("Missing meaning or shared scene identity")
            if errors:
                raise ValueError(f"Invalid scene card {card_id}: {'; '.join(errors)}")
            evidence = update.get("image_review") or {}
            if card.image_url:
                image = library.local_path(card.image_url)
                if image is None or evidence.get("sha256") != sha(image.read_bytes()):
                    raise ValueError(f"Image changed or missing: {card_id}")
                if evidence.get("compatible") is not True or not evidence.get("method"):
                    raise ValueError(f"Image has not passed contextual review: {card_id}")
                source = library.source_of(card.image_url, card.image_source)
                dbpath = paths.DATA_DIR / "image-library.sqlite3"
                if dbpath.exists():
                    with sqlite3.connect(f"{dbpath.as_uri()}?mode=ro", uri=True) as db:
                        if db.execute("SELECT 1 FROM policy WHERE source=?", (source,)).fetchone():
                            raise ValueError(f"Blocked image source: {source}")
                key = (card.image_concept, card.image_scene)
                if key in scenes and scenes[key] != evidence["sha256"]:
                    raise ValueError("A shared scene must use the same image in every language")
                scenes[key] = evidence["sha256"]
            if spelling:
                evidence = {**evidence, "word_correction": update["word_correction"]}
            updates.append((card_id, card, evidence))
        changes.append({"path": path, "raw": raw, "deck": deck, "updates": updates})
    return changes


def apply_manifest(manifest: dict, *, apply: bool = False) -> dict:
    changes = prepare(manifest)
    result = {"decks": len(changes), "cards": sum(len(c["updates"]) for c in changes),
              "illustrated": sum(bool(card.image_url) for c in changes for _, card, _ in c["updates"]),
              "applied": False}
    if not apply:
        return result
    backup = paths.DATA_DIR / "scene-backups" / uuid4().hex
    backup.mkdir(parents=True)
    for change in changes:
        (backup / change["path"].name).write_bytes(change["raw"])
    registry = paths.DATA_DIR / "image-library.sqlite3"
    registry_existed = registry.exists()
    if registry_existed:
        with sqlite3.connect(registry) as src, sqlite3.connect(backup / registry.name) as dst:
            src.backup(dst)
    (backup / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    journal = backup / "journal.json"
    def log(status):
        journal.write_text(json.dumps({"status": status, "at": now_iso(), **result}), encoding="utf-8")
    log("prepared")
    written = []
    staged = []
    try:
        for change in changes:
            by_id = {c["id"]: c for c in change["deck"]["cards"]}
            for card_id, card, evidence in change["updates"]:
                old = Card.model_validate(by_id[card_id])
                if card.image_url:
                    library.register(card, verified_context=True, replace_context=True, metadata={
                        "origin": "scene_harmonization", "review": evidence,
                        "scene_manifest": str(backup / "manifest.json")})
                else:
                    card.image_asset_id = ""
                    card.image_provenance = {}
                    card.image_source = ""
                card.image_history.append({"at": now_iso(), "reason": "scene_harmonization",
                    "before": library.snapshot(old), "after": library.snapshot(card),
                    "before_context": {k: getattr(old, k) for k in ("phrase", "phrase_fr", "image_concept", "image_scene")},
                    **({"word_correction": evidence["word_correction"]} if "word_correction" in evidence else {}),
                    "backup": backup.name})
                # Preserve unknown fields and unchanged audio/IPA/word identity.
                row = by_id[card_id]
                for field in FIELDS | {"image_asset_id", "image_provenance", "image_history"}:
                    row[field] = getattr(card, field)
                if "word_correction" in evidence:
                    for field in WORD_FIELDS:
                        row[field] = getattr(card, field)
                row["errors"] = []
            change["deck"]["updated_at"] = now_iso()
            change["deck"]["stages"] = {**change["deck"].get("stages", {}), "qa": "pending", "export": "pending"}
            temp = change["path"].with_name(change["path"].name + f".{backup.name}.pending")
            temp.write_text(json.dumps(change["deck"], ensure_ascii=False, indent=2), encoding="utf-8")
            staged.append(temp)
        # Check all revisions again, before replacing the first file.
        for change in changes:
            if change["path"].read_bytes() != change["raw"]:
                raise ValueError("Concurrent deck edit detected; no reviewed update applied")
        for change, temp in zip(changes, staged):
            os.replace(temp, change["path"])
            written.append(change)
        result.update(applied=True, backup=str(backup))
        log("complete")
        return result
    except Exception:
        for change in written:
            restore = change["path"].with_suffix(".restore")
            restore.write_bytes(change["raw"])
            os.replace(restore, change["path"])
        if registry_existed:
            with sqlite3.connect(backup / registry.name) as src, sqlite3.connect(registry) as dst:
                src.backup(dst)
        elif registry.exists():
            registry.unlink()
        log("rolled_back")
        raise
    finally:
        for temp in staged:
            temp.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    print(json.dumps(apply_manifest(manifest, apply=args.apply), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
