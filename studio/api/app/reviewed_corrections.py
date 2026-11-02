"""Offline, revision-guarded maintenance for independently reviewed corrections.

Stop all API/deck/registry writers before applying or rolling back. Preview is
read-only. Reviews bind to proposed text and image contexts independently.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sqlite3
from pathlib import Path
from contextlib import closing
from uuid import uuid4

from . import image_library as library, paths
from .models import Card
from .protocol import validate_card_image
from .store import now_iso

TEXT_FIELDS = {"french", "category", "phrase", "phrase_fr", "ipa"}
IMAGE_FIELDS = {"image_concept", "image_scene", "image_url", "image_source", "skip_image"}
FIELDS = TEXT_FIELDS | IMAGE_FIELDS
WORD_FIELDS = {"lemma", "surface"}
CONTEXT_FIELDS = ("language", "lemma", "surface", "french", "category", "phrase", "phrase_fr", "image_concept", "image_scene")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hash_card(card: dict) -> str:
    return sha(json.dumps(card, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode())


def context_hash(card: dict) -> str:
    return hash_card({k: card.get(k, "") for k in CONTEXT_FIELDS})


def text_hash(card: dict) -> str:
    return hash_card({k: card.get(k, "") for k in ("language", "lemma", "surface", "french", "category", "phrase", "phrase_fr", "ipa")})


def reviewed_source_copy(old: dict, new: dict, update: dict, cache: dict) -> None:
    """Verify a root-approved deterministic copy of an already reviewed scene.

    This is inherited source evidence, never a fabricated independent review of
    the destination. Exact gloss/POS and destination phonetics must also pass.
    """
    import unicodedata
    def load(value):
        path = Path(value).resolve()
        if not path.is_relative_to(paths.DATA_DIR.resolve()):
            raise ValueError("Source-copy evidence must be inside data directory")
        if path not in cache:
            raw = path.read_bytes(); cache[path] = (sha(raw), json.loads(raw))
        return cache[path]
    def norm(value):
        return " ".join(unicodedata.normalize("NFC", value or "").casefold().split())
    copy = update["reviewed_source_copy"]
    digest, plan = load(copy["plan"])
    if digest != copy.get("plan_sha256") or plan.get("status") != "staged_pending_root_rule_review":
        raise ValueError("Source-copy plan changed")
    rule_digest, rule = load(copy["root_rule"])
    if not (rule_digest == copy.get("root_rule_sha256") and rule.get("approved") is True
            and rule.get("reviewer") == "root" and rule.get("method") == "reviewed_source_copy"
            and str(rule.get("reason", "")).strip()):
        raise ValueError("Source-copy rule awaits root review")
    for field in ("planner_path", "compiler_path"):
        script = Path(copy[field]).resolve()
        if not script.is_relative_to(paths.DATA_DIR.resolve()) or rule.get("script_sha256", {}).get(str(script)) != sha(script.read_bytes()):
            raise ValueError("Source-copy producer differs from root-reviewed code")
    if plan["destination"]["id"] != old["id"] or plan["destination"]["before_sha256"] != hash_card(old):
        raise ValueError("Source-copy destination changed")
    if update.get("word_correction") or update["fields"] != plan["fields"]:
        raise ValueError("Source-copy fields differ from reviewed plan")
    allowed = {"french", "category", "phrase", "phrase_fr", "image_concept", "image_scene", "image_url", "image_source", "skip_image"}
    if set(update["fields"]) - allowed:
        raise ValueError("Source-copy may not change phonetics, audio or identity")
    source_path = paths.DECKS_DIR / (plan["source_deck"] + ".json")
    source_digest, source_deck = load(source_path)
    if source_digest != plan["source_deck_sha256"]:
        raise ValueError("Source-copy canonical deck changed")
    matches = [c for c in source_deck["cards"] if c["id"] in plan["source_ids"]]
    if len(matches) != 1 or len(plan["source_ids"]) != 1:
        raise ValueError("Ambiguous canonical source")
    source = matches[0]
    if not re.fullmatch(r"legacy-(?:es|pt)-1000", plan["source_deck"]) or plan["destination"]["deck"] not in {
        plan["source_deck"].rsplit("-", 1)[0] + "-" + size for size in ("2000", "3000", "4000", "full")
    }:
        raise ValueError("Source-copy is limited to reviewed 1000-card sibling editions")
    dest_digest, dest_deck = load(paths.DECKS_DIR / (plan["destination"]["deck"] + ".json"))
    if dest_digest != plan["destination_deck_sha256"]:
        raise ValueError("Source-copy destination deck changed")
    word_key = lambda row: tuple(norm(row.get(k)) for k in ("language", "lemma", "surface"))
    if any(sum(word_key(c) == word_key(old) for c in deck["cards"]) != 1 for deck in (source_deck, dest_deck)):
        raise ValueError("Source-copy duplicate word requires semantic disambiguation")
    if hash_card(source) != plan["source_card_sha256"]:
        raise ValueError("Source-copy canonical card changed")
    if any(norm(source.get(k)) != norm(old.get(k)) for k in ("language", "lemma", "surface", "french", "category")):
        raise ValueError("Source-copy word, meaning or POS differs")
    if any(new.get(k) != source.get(k) for k in allowed):
        raise ValueError("Source-copy does not reproduce canonical scene")
    proof = plan["source_proof"]
    audit_digest, audit = load(proof["audit_report"])
    if audit_digest != proof["audit_report_sha256"]:
        raise ValueError("Source-copy audit changed")
    index_key = ("audit-index", proof["audit_report"])
    if index_key not in cache:
        cache[index_key] = {(r["deck"], r["id"]): r for r in audit["cards"]}
    a = cache[index_key].get((plan["source_deck"], source["id"]), {})
    dest_audit = cache[index_key].get((plan["destination"]["deck"], old["id"]), {})
    if not (a.get("card_hash") == hash_card(source) and a.get("text_status") == "compatible"
            and a.get("text_review_scope") == "complete_text" and a.get("phonetic_status") == "compatible"
            and dest_audit.get("card_hash") == hash_card(old) and dest_audit.get("phonetic_status") == "compatible"):
        raise ValueError("Source-copy full source audit or destination phonetics unverified")
    manifest_digest, manifest = load(proof["historical_manifest"])
    if manifest_digest != proof["historical_manifest_sha256"]:
        raise ValueError("Source-copy historical proof changed")
    updates = [u for d in manifest["decks"] if d["id"] == plan["source_deck"] for u in d["updates"] if u["id"] == source["id"]]
    if len(updates) != 1 or updates[0] != proof["historical_update"]:
        raise ValueError("Source-copy historical update not bound")
    u = updates[0]
    if any(source.get(k) != v for k, v in u["fields"].items()) or u.get("image_review", {}).get("linguistic_review", {}).get("approved") is not True:
        raise ValueError("Source-copy historical content no longer approved")
    if source.get("image_url"):
        image = library.local_path(source["image_url"])
        digest = sha(image.read_bytes()) if image else None
        if not (digest and digest == proof.get("image_sha256") == a.get("image_sha256") == u["image_review"].get("sha256")
                and a.get("image_status") == "compatible" and a.get("pixel_status") == "visually_described"
                and u["image_review"].get("compatible") is True):
            raise ValueError("Source-copy contextual pixel approval missing")
        context = library.context_key(Card.model_validate(source))
        registry = paths.DATA_DIR / "image-library.sqlite3"
        with closing(sqlite3.connect(f"{registry.resolve().as_uri()}?mode=ro", uri=True)) as db:
            row = db.execute("SELECT a.metadata FROM assets a JOIN contexts c ON c.asset=a.id WHERE c.context=? AND a.id=? AND a.retired=0 AND NOT EXISTS(SELECT 1 FROM policy p WHERE p.source=a.source)", (context, source.get("image_asset_id", ""))).fetchone()
        if not row or json.loads(row[0]).get("sha256") != digest:
            raise ValueError("Source-copy canonical image mapping no longer verified")
    elif not source.get("skip_image"):
        raise ValueError("Source-copy missing image requires explicit exclusion")


def reviewed_imported_token(old: dict, update: dict) -> dict:
    """Repair a demonstrated foreign token, with exact independent/root gates.

    The default mode requires a correct token already in the source example.
    Contaminated examples need the separate, explicitly root-reviewed mode and
    two reviews bound to all eight before/after text fields.
    """
    c = update["word_correction"]
    evidence = c.get("confirmed_import_error_evidence", {})
    language = old.get("language")
    result = {k: c.get(k) for k in WORD_FIELDS}
    mode = c.get("evidence_mode", "existing_example")
    if mode not in {"existing_example", "root_reviewed_contaminated_import"}:
        raise ValueError("Unknown imported-token evidence mode")
    if not (c.get("same_intended_sense") is True and c.get("correct_target_language") == language
            and evidence.get("intended_language") == language
            and evidence.get("foreign_token") == old.get("surface")
            and evidence.get("french_gloss") == old.get("french") and old.get("french", "").strip()
            and evidence.get("example") == old.get("phrase") and old.get("phrase", "").strip()
            and str(c.get("reason", "")).strip()):
        raise ValueError("Imported-token repair needs original meaning and target-language evidence")
    for k, value in result.items():
        if c.get("before_" + k) != old.get(k) or not isinstance(value, str) or not value.strip():
            raise ValueError("Imported-token before/after mismatch")
    contains = lambda value: re.search(r"(?<!\w)" + re.escape(value) + r"(?!\w)", old["phrase"], re.IGNORECASE)
    new = {**old, **update["fields"], **result}
    if mode == "existing_example":
        if result["surface"] == old.get("surface") or contains(old["surface"]) or not contains(result["surface"]):
            raise ValueError("Existing example must demonstrate the imported-token error")
        if new.get("french") != old.get("french"):
            raise ValueError("Imported-token repair must preserve the demonstrated meaning")
    elif evidence.get("french_example") != old.get("phrase_fr") or not old.get("phrase_fr", "").strip():
        raise ValueError("Contaminated import needs exact original translated-example evidence")
    before_hash = hash_card({k: old[k] for k in WORD_FIELDS})
    reviewers = set()
    for key in ("author_review", "independent_review"):
        review = c.get(key, {})
        if not (review.get("approved") is True and review.get("same_intended_sense") is True
                and review.get("correct_target_language") == language
                and review.get("before_sha256") == before_hash and review.get("after_sha256") == hash_card(result)
                and isinstance(review.get("reviewer"), str) and review["reviewer"].strip()
                and isinstance(review.get("evidence"), str) and review["evidence"].strip()):
            raise ValueError("Two exact imported-token reviews required")
        reviewers.add(review["reviewer"])
        if mode == "root_reviewed_contaminated_import" and not (
            review.get("before_content_sha256") == text_hash(old)
            and review.get("after_content_sha256") == text_hash(new)
        ):
            raise ValueError("Contaminated import requires dual full-content review hashes")
    if len(reviewers) != 2:
        raise ValueError("Imported-token reviews must be independent")
    root = c.get("root_review", {})
    if not (root.get("approved") is True and root.get("reviewer") == "root"
            and root.get("before_sha256") == text_hash(old) and root.get("after_sha256") == text_hash(new)
            and str(root.get("reason", "")).strip() and str(root.get("evidence", "")).strip()):
        raise ValueError("Imported-token correction awaits explicit root review")
    if mode == "root_reviewed_contaminated_import" and root.get("evidence_mode") != mode:
        raise ValueError("Root must explicitly approve contaminated-import repair mode")
    if old.get("audio_url"):
        audio = c.get("audio_review", {})
        if not (audio.get("mode") == "preservation_only" and audio.get("audible_validation") is False
                and audio.get("possible_obsolete") is True and "pronunciation_unchanged" not in audio
                and audio.get("expected_audio_url") == old["audio_url"] and audio.get("expected_ipa") == old.get("ipa", "")
                and str(audio.get("reason", "")).strip()):
            raise ValueError("Imported-token audio must remain preserved and explicitly unverified")
    return result


def reviewed_orthography(old: dict, update: dict) -> dict:
    """Word identity changes require two explicit, exact orthographic reviews.

    This is intended-word evidence, not a claim that an audio file was listened
    to. Audio bytes/URL remain untouched; IPA modifications have their own gate.
    """
    correction = update.get("word_correction")
    if correction is None:
        return {}
    if isinstance(correction, dict) and correction.get("kind") == "imported_token_correction":
        return reviewed_imported_token(old, update)
    if not isinstance(correction, dict) or correction.get("same_lexeme") is not True or correction.get("kind") != "orthography":
        raise ValueError("Only reviewed same-lexeme orthography is allowed")
    if not str(correction.get("reason", "")).strip():
        raise ValueError("Orthography review reason required")
    result = {}
    for field in WORD_FIELDS:
        if correction.get("before_" + field) != old.get(field):
            raise ValueError("Stale orthography before value")
        after = correction.get(field)
        if not isinstance(after, str) or not after.strip():
            raise ValueError("Blank corrected word")
        result[field] = after
    proof_hash = hash_card(result)
    before_hash = hash_card({k: old.get(k) for k in WORD_FIELDS})
    reviewers = set()
    for key in ("author_review", "independent_review"):
        review = correction.get(key, {})
        if not (review.get("approved") is True and review.get("same_lexeme") is True
                and review.get("before_sha256") == before_hash and review.get("after_sha256") == proof_hash
                and isinstance(review.get("reviewer"), str) and review["reviewer"].strip()
                and isinstance(review.get("evidence"), str) and review["evidence"].strip()):
            raise ValueError("Two exact orthography reviews required")
        reviewers.add(review["reviewer"])
    if len(reviewers) != 2:
        raise ValueError("Orthography reviews must be independent")
    if old.get("audio_url"):
        audio = correction.get("audio_review", {})
        if not (audio.get("mode") == "preservation_only" and audio.get("pronunciation_unchanged") is True
                and audio.get("expected_audio_url") == old["audio_url"]
                and audio.get("expected_ipa") == old.get("ipa", "") and str(audio.get("reason", "")).strip()):
            raise ValueError("Orthography requires explicit preservation of intended audio")
    return result


def registry_hash(path: Path) -> str | None:
    """Logical digest includes WAL state; stable across SQLite backup/restore."""
    if not path.exists():
        return None
    with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as db:
        return sha("\n".join(db.iterdump()).encode())


def _backup_db(source: Path, destination: Path):
    with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(destination)) as dst:
        src.backup(dst)


def _write(path: Path, raw: bytes):
    temp = path.with_name(path.name + "." + uuid4().hex + ".pending")
    try:
        with temp.open("wb") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _json_write(path: Path, value):
    _write(path, json.dumps(value, ensure_ascii=False, indent=2).encode())


def prepare(manifest: dict) -> list[dict]:
    if manifest.get("version") != 1 or not isinstance(manifest.get("decks"), list):
        raise ValueError("Unsupported correction manifest")
    changes, seen = [], set()
    evidence_cache = {}
    registry = paths.DATA_DIR / "image-library.sqlite3"
    blocked = set()
    if registry.exists():
        with closing(sqlite3.connect(f"{registry.resolve().as_uri()}?mode=ro", uri=True)) as db:
            blocked = {r[0] for r in db.execute("SELECT source FROM policy")}
    for spec in manifest["decks"]:
        name = spec["id"]
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", name) or name in seen:
            raise ValueError("Invalid or duplicate deck")
        seen.add(name)
        path = paths.DECKS_DIR / (name + ".json")
        raw = path.read_bytes()
        if sha(raw) != spec["expected_sha256"]:
            raise ValueError("Stale deck revision: " + name)
        deck = json.loads(raw)
        cards = {c["id"]: c for c in deck["cards"]}
        if len(cards) != len(deck["cards"]):
            raise ValueError("Duplicate source card IDs")
        updates, ids = [], set()
        for u in spec["updates"]:
            cid = u["id"]
            if cid in ids or cid not in cards:
                raise ValueError("Unknown or duplicate update")
            ids.add(cid)
            old = cards[cid]
            if u.get("before_sha256") != hash_card(old):
                raise ValueError("Stale card revision: " + cid)
            operation = u.get("operation", "correct")
            if operation == "quarantine_invalid_import":
                q = u.get("quarantine_review", {})
                if u.get("fields") or u.get("word_correction") or not (
                    q.get("approved") is True and q.get("reviewer") == "root"
                    and q.get("kind") == "invalid_import" and q.get("before_sha256") == hash_card(old)
                    and q.get("expected_lemma") == old.get("lemma") and q.get("expected_surface") == old.get("surface")
                    and isinstance(q.get("reason"), str) and q["reason"].strip()
                    and isinstance(q.get("evidence"), str) and q["evidence"].strip()
                ):
                    raise ValueError("Quarantine requires exact root-reviewed invalid-import evidence")
                updates.append({"id": cid, "old": copy.deepcopy(old), "review": u, "operation": operation})
                continue
            if operation != "correct":
                raise ValueError("Unknown correction operation")
            fields = u["fields"]
            if not isinstance(fields, dict) or (not fields and not u.get("word_correction")) or set(fields) - FIELDS:
                raise ValueError("Forbidden or empty correction fields")
            spelling = reviewed_orthography(old, u)
            new = {**copy.deepcopy(old), **fields, **spelling}
            card = Card.model_validate(new)
            image_errors = validate_card_image(card)
            if image_errors:
                raise ValueError("Image violates existing card policy: " + "; ".join(image_errors))
            actual = {k for k in fields | spelling if old.get(k) != new[k]}
            if u.get("reviewed_source_copy"):
                reviewed_source_copy(old, new, u, evidence_cache)
            elif actual & (TEXT_FIELDS | WORD_FIELDS):
                review = u.get("text_review", {})
                if not (review.get("approved") is True and review.get("independent") is True
                        and str(review.get("reviewer", "")).strip()
                        and review.get("after_sha256") == text_hash(new)):
                    raise ValueError("Independent text review does not bind to proposed card")
            if "ipa" in actual:
                r = u.get("ipa_review", {})
                if not (r.get("before") == old.get("ipa", "") and r.get("after") == new["ipa"]
                        and r.get("audio_url") == old.get("audio_url", "") and str(r.get("reason", "")).strip()):
                    raise ValueError("Explicit IPA/audio preservation review required")
            required = {"french", "category"}
            features = deck.get("features", {})
            if features.get("phrase", True):
                required |= {"phrase", "phrase_fr"}
            if features.get("phonetic", True):
                required.add("ipa")
            if any(not str(new.get(k, "")).strip() for k in required):
                raise ValueError("Blank required content: " + cid)
            revalidate = bool(actual & (IMAGE_FIELDS | WORD_FIELDS | (TEXT_FIELDS - {"ipa"})))
            if card.image_url and revalidate:
                r = u.get("image_review", {})
                # Both API routes expose exactly a folder and a filename.
                # A deeper file can exist locally while its URL returns 404.
                if not re.fullmatch(r"/api/(?:cache-media|media)/[^/\\?#]+/[^/\\?#]+", card.image_url):
                    raise ValueError("Image URL does not match served media route")
                image = library.local_path(card.image_url)
                if not (image and r.get("sha256") == sha(image.read_bytes()) and r.get("compatible") is True
                        and str(r.get("method", "")).strip() and r.get("context_sha256") == context_hash(new)):
                    raise ValueError("Image review does not bind to corrected context and pixels")
                if library.source_of(card.image_url, card.image_source) in blocked:
                    raise ValueError("Blocked image source")
                if not library.context_key(card, deck.get("target_language", "fr")):
                    raise ValueError("Verified image needs a shared scene or translated example")
                if card.skip_image:
                    raise ValueError("skip_image requires no image URL")
            if not card.image_url and features.get("image", True) and not card.skip_image:
                raise ValueError("Removing an image requires explicit skip_image")
            updates.append({"id": cid, "old": copy.deepcopy(old), "new": new, "review": u, "revalidate": revalidate})
            cards[cid].update({**fields, **spelling})
        quarantined = {u["id"] for u in updates if u.get("operation") == "quarantine_invalid_import"}
        if quarantined:
            deck["cards"] = [c for c in deck["cards"] if c["id"] not in quarantined]
            deck["card_count"] = len(deck["cards"])
        if updates:
            changes.append({"path": path, "raw": raw, "deck": deck, "updates": updates})
    # Compare proposed rows with ALL unchanged decks, not only this manifest.
    proposed = {c["path"]: c["deck"] for c in changes}
    scenes = {}
    for path in paths.DECKS_DIR.glob("*.json"):
        deck = proposed[path] if path in proposed else json.loads(path.read_bytes())
        for row in deck["cards"]:
            key = (row.get("image_concept"), row.get("image_scene"))
            if not all(key) or not row.get("image_url"):
                continue
            image = library.local_path(row["image_url"])
            if image is None:
                raise ValueError("Shared scene image is not local")
            digest = sha(image.read_bytes())
            if key in scenes and scenes[key] != digest:
                raise ValueError("Shared scene has conflicting images; version or harmonize scene")
            scenes[key] = digest
    return changes


def apply_manifest(manifest: dict, *, apply: bool = False) -> dict:
    changes = prepare(manifest)
    result = {"decks": len(changes), "cards": sum(len(c["updates"]) for c in changes), "applied": False}
    if not apply or not changes:
        return result
    registry = paths.DATA_DIR / "image-library.sqlite3"
    lock = paths.DATA_DIR / ".reviewed-corrections.lock"
    with lock.open("x"):
        pass
    backup = paths.DATA_DIR / "correction-backups" / uuid4().hex
    written = []
    try:
        backup.mkdir(parents=True)
        before_registry = registry_hash(registry)
        if before_registry is not None:
            _backup_db(registry, backup / registry.name)
            if registry_hash(backup / registry.name) != before_registry:
                raise ValueError("Registry changed during backup")
        for c in changes:
            (backup / c["path"].name).write_bytes(c["raw"])
        _json_write(backup / "manifest.json", manifest)
        quarantine = [{"deck": c["path"].stem, "id": u["id"], "card": u["old"],
                       "card_sha256": hash_card(u["old"]), "review": u["review"]["quarantine_review"],
                       "original_index": next(i for i, row in enumerate(json.loads(c["raw"])["cards"]) if row["id"] == u["id"])}
                      for c in changes for u in c["updates"] if u.get("operation") == "quarantine_invalid_import"]
        if quarantine:
            _json_write(backup / "quarantine.json", quarantine)
        journal = {"status": "prepared", "at": now_iso(), "before_registry": before_registry,
                   "before": {c["path"].name: sha(c["raw"]) for c in changes}, "after": {}}
        if quarantine:
            journal["quarantine_sha256"] = sha((backup / "quarantine.json").read_bytes())
        _json_write(backup / "journal.json", journal)
        try:
            if registry_hash(registry) != before_registry:
                raise ValueError("Registry changed before apply")
            for c in changes:
                for u in c["updates"]:
                    if u.get("operation") == "quarantine_invalid_import":
                        continue
                    row = next(r for r in c["deck"]["cards"] if r["id"] == u["id"])
                    if u["revalidate"]:
                        card = Card.model_validate(row)
                        if card.image_url:
                            image = library.local_path(card.image_url)
                            if image is None or sha(image.read_bytes()) != u["review"]["image_review"]["sha256"]:
                                raise ValueError("Image changed during application")
                            library.register(card, target=c["deck"].get("target_language", "fr"), verified_context=True,
                                             replace_context=True, metadata={"origin": "reviewed_corrections", "review": u["review"]["image_review"], "scene_manifest": str(backup / "manifest.json")})
                            for k in ("image_asset_id", "image_source", "image_provenance"):
                                row[k] = getattr(card, k)
                        else:
                            row.update(image_asset_id="", image_source="", image_provenance={})
                        row["image_history"] = [*u["old"].get("image_history", []), {
                            "at": now_iso(), "reason": "reviewed_corrections", "backup": backup.name,
                            "before": library.snapshot(Card.model_validate(u["old"])),
                            "after": library.snapshot(Card.model_validate(row)), "review": u["review"].get("image_review", {})}]
                c["deck"]["updated_at"] = now_iso()
                c["deck"]["stages"] = {**c["deck"].get("stages", {}), "qa": "pending", "export": "pending"}
                c["after"] = json.dumps(c["deck"], ensure_ascii=False, indent=2).encode()
                journal["after"][c["path"].name] = sha(c["after"])
            for c in changes:
                if c["path"].read_bytes() != c["raw"]:
                    raise ValueError("Concurrent deck edit")
            journal.update(status="applying", after_registry=registry_hash(registry))
            _json_write(backup / "journal.json", journal)
            for c in changes:
                _write(c["path"], c["after"])
                written.append(c)
            journal["status"] = "complete"
            _json_write(backup / "journal.json", journal)
            return {**result, "applied": True, "backup": str(backup)}
        except Exception:
            for c in written:
                _write(c["path"], c["raw"])
            if before_registry is not None:
                _backup_db(backup / registry.name, registry)
            elif registry.exists():
                registry.unlink()
            journal["status"] = "rolled_back"
            _json_write(backup / "journal.json", journal)
            raise
    finally:
        lock.unlink(missing_ok=True)


def rollback(backup: Path) -> dict:
    backup = backup.resolve()
    root = (paths.DATA_DIR / "correction-backups").resolve()
    if backup.parent != root:
        raise ValueError("Invalid backup directory")
    journal = json.loads((backup / "journal.json").read_text(encoding="utf-8"))
    if journal["status"] != "complete":
        raise ValueError("Only complete applications may be rolled back")
    registry = paths.DATA_DIR / "image-library.sqlite3"
    lock = paths.DATA_DIR / ".reviewed-corrections.lock"
    with lock.open("x"):
        pass
    try:
        if journal.get("quarantine_sha256") and sha((backup / "quarantine.json").read_bytes()) != journal["quarantine_sha256"]:
            raise ValueError("Quarantine archive changed")
        if registry_hash(registry) != journal["after_registry"]:
            raise ValueError("Registry changed after application")
        restore = []
        for name, before in journal["before"].items():
            if Path(name).name != name or not re.fullmatch(r"[A-Za-z0-9_-]+\.json", name):
                raise ValueError("Invalid journal path")
            path = paths.DECKS_DIR / name
            raw = (backup / name).read_bytes()
            if sha(raw) != before or sha(path.read_bytes()) != journal["after"][name]:
                raise ValueError("Deck or backup changed after application")
            restore.append((path, raw))
        if journal["before_registry"] is not None and registry_hash(backup / registry.name) != journal["before_registry"]:
            raise ValueError("Registry backup changed")
        # Retain the applied state too, so an interrupted rollback can recover
        # without leaving decks and the registry in opposite revisions.
        forward = {p: p.read_bytes() for p, _ in restore}
        forward_registry = backup / "registry-before-rollback.sqlite3"
        if registry.exists():
            _backup_db(registry, forward_registry)
        written = []
        try:
            for path, raw in restore:
                _write(path, raw)
                written.append(path)
            if journal["before_registry"] is not None:
                _backup_db(backup / registry.name, registry)
            else:
                registry.unlink(missing_ok=True)
            journal["status"] = "rolled_back"
            _json_write(backup / "journal.json", journal)
        except Exception:
            for path in written:
                _write(path, forward[path])
            if forward_registry.exists():
                _backup_db(forward_registry, registry)
            journal["status"] = "complete"
            _json_write(backup / "journal.json", journal)
            raise
        return {"rolled_back": True, "decks": len(restore)}
    finally:
        lock.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, nargs="?")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--rollback", type=Path)
    args = parser.parse_args()
    if args.rollback:
        if args.manifest or args.apply:
            parser.error("rollback cannot be combined with a manifest or apply")
        result = rollback(args.rollback)
    else:
        if not args.manifest:
            parser.error("manifest required")
        result = apply_manifest(json.loads(args.manifest.read_text(encoding="utf-8")), apply=args.apply)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
