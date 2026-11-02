import copy
import hashlib
import json
from pathlib import Path

import pytest

from app import image_library, paths, scene_harmonization as scenes
from app.models import Card


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    monkeypatch.setattr(paths, "DECKS_DIR", tmp_path / "decks")
    monkeypatch.setattr(paths, "MEDIA_DIR", tmp_path / "media")
    paths.DECKS_DIR.mkdir()
    (paths.MEDIA_DIR / "d").mkdir(parents=True)
    image = paths.MEDIA_DIR / "d" / "doctor.jpg"
    image.write_bytes(b"reviewed-image-bytes")
    manifest = {"version": 1, "decks": []}
    for lang, word, phrase in [("en", "doctor", "The doctor gives medicine to a patient."),
                               ("pt", "médico", "O médico dá um remédio ao paciente.")]:
        row = {"id": lang + "1", "lemma": word, "surface": word, "language": lang,
               "phrase": "old", "phrase_fr": "old", "audio_url": "/word.mp3", "custom": "preserve"}
        tail = {**row, "id": lang + "2", "lemma": "tail", "errors": ["existing"]}
        deck = {"id": lang, "name": lang, "language": lang, "cards": [row, tail]}
        path = paths.DECKS_DIR / (lang + ".json")
        path.write_text(json.dumps(deck), encoding="utf-8")
        manifest["decks"].append({"id": lang, "expected_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "updates": [{
            "id": lang + "1", "fields": {"phrase": phrase, "phrase_fr": "Le médecin donne un médicament à un patient.",
                "french": "médecin", "category": "Nom", "image_url": "/api/media/d/doctor.jpg", "image_source": "generated",
                "image_concept": "doctor:medical", "image_scene": "give-medicine", "skip_image": False},
            "image_review": {"compatible": True, "method": "test-review", "sha256": hashlib.sha256(image.read_bytes()).hexdigest()}}]})
    return manifest


def test_preview_does_not_change_decks_or_create_registry(fixture):
    before = {p: p.read_bytes() for p in paths.DECKS_DIR.glob("*.json")}
    result = scenes.apply_manifest(fixture)
    assert result == {"decks": 2, "cards": 2, "illustrated": 2, "applied": False}
    assert all(p.read_bytes() == raw for p, raw in before.items())
    assert not (paths.DATA_DIR / "image-library.sqlite3").exists()


def test_apply_retains_tail_unknown_fields_word_audio_and_provenance(fixture):
    before = json.loads((paths.DECKS_DIR / "en.json").read_text())
    result = scenes.apply_manifest(fixture, apply=True)
    after = json.loads((paths.DECKS_DIR / "en.json").read_text())
    assert result["applied"]
    assert after["cards"][1] == before["cards"][1]
    c = after["cards"][0]
    assert c["audio_url"] == "/word.mp3" and c["custom"] == "preserve"
    assert c["image_source"] == "generated"
    assert c["image_history"][0]["before_context"]["phrase"] == "old"
    assert c["image_provenance"]["review"]["method"] == "test-review"
    translated = Card(id="es", lemma="médico", surface="médico", language="es", category="Nom",
                      image_concept="doctor:medical", image_scene="give-medicine")
    assert image_library.lookup(translated)["id"] == c["image_asset_id"]


def seed_context_conflict(manifest):
    fields = manifest["decks"][0]["updates"][0]["fields"]
    correct = Card(id="correct", lemma="doctor", surface="doctor", language="en", **fields)
    # The chosen replacement predates the incompatible asset in the registry.
    correct_id = image_library.register(correct, verified_context=True)
    (paths.MEDIA_DIR / "d" / "wrong.jpg").write_bytes(b"unrelated-family-image")
    wrong = correct.model_copy(update={"id": "wrong", "image_url": "/api/media/d/wrong.jpg"})
    wrong_id = image_library.register(wrong, verified_context=True)
    other = wrong.model_copy(update={"id": "other", "image_scene": "family-at-home"})
    image_library.register(other, verified_context=True)
    assert image_library.lookup(correct)["id"] == wrong_id
    return correct, correct_id, wrong_id, other


def test_reviewed_replacement_revokes_only_conflicting_context_and_audits(fixture):
    correct, correct_id, wrong_id, other = seed_context_conflict(fixture)
    result = scenes.apply_manifest(fixture, apply=True)
    assert image_library.lookup(correct)["id"] == correct_id
    assert image_library.lookup(other)["id"] == wrong_id
    with image_library.database() as db:
        assert db.execute("SELECT retired FROM assets WHERE id=?", (wrong_id,)).fetchone()[0] == 0
        assert [r[0] for r in db.execute("SELECT asset FROM contexts WHERE context=?",
                                       (image_library.context_key(correct),))] == [correct_id]
        events = [json.loads(r[0]) for r in db.execute("SELECT data FROM events")]
    assert len(events) == 1  # The second language registers the same reviewed mapping.
    event = events[0]
    assert event["type"] == "context_replacement"
    assert event["context"] == image_library.context_key(correct)
    assert event["revoked_assets"] == [wrong_id]
    assert event["replacement_asset"] == correct_id
    assert event["review"]["method"] == "test-review"
    assert event["reason"] == "scene_harmonization"
    assert event["scene_manifest"] == str(Path(result["backup"]) / "manifest.json")


def test_replacement_preview_preserves_existing_registry_and_contexts(fixture):
    correct, _, wrong_id, _ = seed_context_conflict(fixture)
    before = {p: p.read_bytes() for p in paths.DATA_DIR.rglob("*") if p.is_file()}
    scenes.apply_manifest(fixture)
    after = {p: p.read_bytes() for p in paths.DATA_DIR.rglob("*") if p.is_file()}
    assert after == before
    assert image_library.lookup(correct)["id"] == wrong_id


def test_failed_replacement_restores_registry_mappings_and_audit(fixture, monkeypatch):
    correct, _, wrong_id, other = seed_context_conflict(fixture)
    before = {p: p.read_bytes() for p in paths.DECKS_DIR.glob("*.json")}
    replace = scenes.os.replace

    def fail_second(src, dst):
        if str(src).endswith(".pending") and dst.name == "pt.json":
            raise OSError("injected replacement failure")
        return replace(src, dst)

    monkeypatch.setattr(scenes.os, "replace", fail_second)
    with pytest.raises(OSError, match="injected replacement"):
        scenes.apply_manifest(fixture, apply=True)
    assert all(p.read_bytes() == raw for p, raw in before.items())
    assert image_library.lookup(correct)["id"] == wrong_id
    assert image_library.lookup(other)["id"] == wrong_id
    with image_library.database() as db:
        assert db.execute("SELECT count(*) FROM events").fetchone()[0] == 0


def test_context_replacement_requires_explicit_verification(fixture):
    fields = fixture["decks"][0]["updates"][0]["fields"]
    card = Card(id="unverified", lemma="doctor", surface="doctor", language="en", **fields)
    with pytest.raises(ValueError, match="explicit verification"):
        image_library.register(card, replace_context=True)
    assert not (paths.DATA_DIR / "image-library.sqlite3").exists()


def test_reject_stale_deck_before_mutation(fixture):
    (paths.DECKS_DIR / "pt.json").write_text("{}")
    before = (paths.DECKS_DIR / "en.json").read_bytes()
    with pytest.raises(ValueError, match="Deck changed"):
        scenes.apply_manifest(fixture, apply=True)
    assert (paths.DECKS_DIR / "en.json").read_bytes() == before
    assert not (paths.DATA_DIR / "image-library.sqlite3").exists()


@pytest.mark.parametrize("failure", ["image_digest", "unreviewed", "missing_word", "duplicate", "forbidden", "path"])
def test_reject_invalid_manifest(fixture, failure):
    spec = fixture["decks"][0]
    update = spec["updates"][0]
    if failure == "image_digest": update["image_review"]["sha256"] = "wrong"
    if failure == "unreviewed": update["image_review"]["compatible"] = False
    if failure == "missing_word": update["fields"]["phrase"] = "The bank is near the station."
    if failure == "duplicate": spec["updates"].append(copy.deepcopy(update))
    if failure == "forbidden": update["fields"]["lemma"] = "changed"
    if failure == "path": spec["id"] = "../escape"
    with pytest.raises(ValueError): scenes.apply_manifest(fixture, apply=True)
    assert not (paths.DATA_DIR / "image-library.sqlite3").exists()


def test_failed_second_write_restores_first_and_registry(fixture, monkeypatch):
    before = {p: p.read_bytes() for p in paths.DECKS_DIR.glob("*.json")}
    replace = scenes.os.replace
    def fail_second(src, dst):
        if str(src).endswith(".pending") and dst.name == "pt.json":
            raise OSError("injected storage failure")
        return replace(src, dst)
    monkeypatch.setattr(scenes.os, "replace", fail_second)
    with pytest.raises(OSError, match="injected"):
        scenes.apply_manifest(fixture, apply=True)
    assert all(p.read_bytes() == raw for p, raw in before.items())
    assert not (paths.DATA_DIR / "image-library.sqlite3").exists()
    journals = list((paths.DATA_DIR / "scene-backups").glob("*/journal.json"))
    assert json.loads(journals[0].read_text())["status"] == "rolled_back"


def test_reviewed_spelling_is_applied_and_audited(fixture):
    spec = fixture["decks"][1]
    path = paths.DECKS_DIR / "pt.json"
    deck = json.loads(path.read_text())
    deck["cards"][0].update(lemma="medico", surface="medico", audio_url="")
    path.write_text(json.dumps(deck))
    spec["expected_sha256"] = scenes.sha(path.read_bytes())
    spec["updates"][0]["word_correction"] = {
        "before_lemma": "medico", "before_surface": "medico", "lemma": "médico",
        "surface": "médico", "reason": "Portuguese noun requires an acute accent."}
    scenes.apply_manifest(fixture, apply=True)
    card = json.loads(path.read_text())["cards"][0]
    assert card["lemma"] == card["surface"] == "médico"
    assert card["image_history"][-1]["word_correction"]["before_lemma"] == "medico"


@pytest.mark.parametrize("failure", ["stale", "meaning", "audio", "reason"])
def test_reject_unreviewed_word_changes(failure):
    original = {"lemma": "aqui", "surface": "aqui", "audio_url": ""}
    correction = {"before_lemma": "aqui", "before_surface": "aqui", "lemma": "aquí",
                  "surface": "aquí", "reason": "Spanish adverb requires an accent."}
    if failure == "stale": correction["before_lemma"] = "alli"
    if failure == "meaning": correction["lemma"] = "allí"
    if failure == "audio": original["audio_url"] = "/word.mp3"
    if failure == "reason": correction["reason"] = ""
    with pytest.raises(ValueError):
        scenes.reviewed_spelling(original, {"word_correction": correction})


@pytest.mark.parametrize("failure", [None, "url", "ipa", "empty_ipa", "status", "reason", "pronunciation", "malformed"])
def test_audio_preserved_for_explicit_orthography_only_review(failure):
    original = {"lemma": "voce", "surface": "voce", "audio_url": "/word.mp3", "ipa": "/voˈse/"}
    correction = {"before_lemma": "voce", "before_surface": "voce", "lemma": "você",
                  "surface": "você", "reason": "Restore the accent in the same Portuguese pronoun.",
                  "audio_review": {"status": "orthography_only_ipa_verified", "pronunciation_unchanged": True,
                                   "expected_audio_url": "/word.mp3", "expected_ipa": "/voˈse/",
                                   "reason": "Existing IPA already represents você; audio is retained, not acoustically certified."}}
    review = correction["audio_review"]
    if failure == "url": review["expected_audio_url"] = "/other.mp3"
    if failure == "ipa": review["expected_ipa"] = "/other/"
    if failure == "empty_ipa": original["ipa"] = review["expected_ipa"] = ""
    if failure == "status": review["status"] = "pending"
    if failure == "reason": review["reason"] = " "
    if failure == "pronunciation": review["pronunciation_unchanged"] = False
    if failure == "malformed": correction["audio_review"] = "approved"
    if failure:
        with pytest.raises(ValueError, match="Review word audio"):
            scenes.reviewed_spelling(original, {"word_correction": correction})
    else:
        assert scenes.reviewed_spelling(original, {"word_correction": correction}) == {"lemma": "você", "surface": "você"}
        assert original["audio_url"] == "/word.mp3" and original["ipa"] == "/voˈse/"
