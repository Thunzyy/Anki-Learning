import json

import pytest

from app import image_library as lib, image_operations as ops, paths
from app.models import Card, Deck, ImagesReplace, Settings


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    monkeypatch.setattr(paths, "DECKS_DIR", tmp_path / "decks")
    monkeypatch.setattr(paths, "MEDIA_DIR", tmp_path / "media")
    paths.DECKS_DIR.mkdir()
    return tmp_path


def card(**kwargs):
    return Card(**{"id": "c", "lemma": "bank", "surface": "bank", "language": "en",
                   "french": "banque", "phrase_fr": "Je dépose mon argent à la banque.", **kwargs})


def asset_file(tmp_path, name="bank.png", content=b"image"):
    file = tmp_path / "media/d" / name
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_bytes(content)
    return f"/api/media/d/{name}"


def test_same_meaning_same_translated_scene_across_languages(tmp_path):
    original = card(image_url=asset_file(tmp_path), image_source="grok")
    lib.register(original, verified_context=True, metadata={"model": "grok-model", "prompt": "bank"})
    hit = lib.lookup(card(lemma="banco", language="pt", category="Nom"))
    assert hit["source"] == "grok"
    assert hit["metadata"]["model"] == "grok-model"
    assert lib.lookup(card(french="rive", phrase_fr="Je marche sur la rive.")) is None
    assert lib.lookup(card(phrase_fr="La banque est fermée.")) is None
    assert lib.lookup(card(), "de") is None


def test_explicit_concept_and_scene_are_language_independent(tmp_path):
    original = card(image_url=asset_file(tmp_path), image_source="gemini",
                    image_concept="bank:finance", image_scene="deposit-cash")
    lib.register(original, verified_context=True)
    translated = card(lemma="banco", language="pt", french="Bank", phrase_fr="Ich zahle Geld ein.",
                      image_concept="bank:finance", image_scene="deposit-cash")
    assert lib.lookup(translated, "de")["source"] == "gemini"
    translated.image_scene = "closed-branch"
    assert lib.lookup(translated, "de") is None


def test_legacy_provenance_is_not_invented_or_contextualized(tmp_path):
    old = card(image_url=asset_file(tmp_path), image_source="generated")
    lib.register(old)
    assert old.image_source == "generated"
    assert old.image_provenance["context_status"] == "unverified"
    assert lib.lookup(old) is None


def test_variants_keep_separate_providers_and_files(tmp_path):
    one = card(image_url=asset_file(tmp_path, "one.png", b"one"), image_source="grok")
    two = card(image_url=asset_file(tmp_path, "two.png", b"two"), image_source="gemini")
    lib.register(one, verified_context=True)
    lib.register(two, verified_context=True)
    assert one.image_asset_id != two.image_asset_id
    assert lib.lookup(card(), source="grok")["id"] == one.image_asset_id
    assert lib.lookup(card(), source="gemini")["id"] == two.image_asset_id


def test_url_hostname_not_substring_decides_source():
    assert lib.source_of("https://upload.wikimedia.org/example.png", "generated") == "wikimedia"
    assert lib.source_of("https://evil.example/wikimedia.org/a.png") == "unknown"
    assert lib.source_of("https://wikimedia.org.evil.example/a.png") == "unknown"


def test_no_unsafe_local_path(tmp_path):
    assert lib.local_path("/api/media/../../secret.png") is None
    c = card(image_url=asset_file(tmp_path), image_source="grok")
    lib.register(c, verified_context=True)
    lib.local_path(c.image_url).unlink()
    assert lib.lookup(c) is None


def test_preview_is_non_mutating_then_replace_generated_by_gemini(tmp_path):
    old = card(image_url=asset_file(tmp_path, "old.png"), image_source="grok")
    new = card(image_url=asset_file(tmp_path, "new.png", b"new"), image_source="gemini")
    lib.register(new, verified_context=True)
    deck = Deck(id="d", name="d", language="en", cards=[old])
    before = deck.model_dump()
    result = ops.replace_images(deck, ImagesReplace(source="grok", destination="gemini"), Settings())
    assert result["matched"] == 1
    assert deck.model_dump() == before
    assert not (tmp_path / "image-backups").exists()
    result = ops.replace_images(deck, ImagesReplace(source="grok", destination="gemini", preview=False), Settings())
    assert result["changed"] == 1
    assert deck.cards[0].image_source == "gemini"
    assert deck.cards[0].image_history[0]["before"]["source"] == "grok"
    assert (tmp_path / "image-backups" / result["backup"] / "d.json").exists()


def test_unmatched_and_generation_failure_keep_original(tmp_path, monkeypatch):
    old = card(image_url=asset_file(tmp_path), image_source="wikimedia")
    deck = Deck(id="d", name="d", language="en", cards=[old])
    before = old.model_dump()
    request = ImagesReplace(destination="gemini", preview=False)
    result = ops.replace_images(deck, request, Settings())
    assert result["changed"] == 0 and result["errors"]
    assert old.model_dump() == before
    def fail(*args, **kwargs):
        raise RuntimeError("unavailable")
    monkeypatch.setattr("app.services.images.fetch_image", fail)
    request.destination = "grok"
    request.generate_missing = True
    result = ops.replace_images(deck, request, Settings())
    assert result["errors"][0]["message"] == "unavailable"
    assert old.model_dump() == before


def test_remove_source_filter_preserves_other_cards_and_audio(tmp_path):
    deck = Deck(id="d", name="d", language="en", cards=[
        card(image_url="https://upload.wikimedia.org/a.jpg", audio_url="sound.mp3"),
        card(id="other", image_url=asset_file(tmp_path), image_source="grok"),
    ])
    result = ops.replace_images(deck, ImagesReplace(source="wikimedia", destination="remove", preview=False), Settings())
    assert result["changed"] == 1
    assert deck.cards[0].image_url == "" and deck.cards[0].audio_url == "sound.mp3"
    assert deck.cards[1].image_source == "grok"


def test_migration_removes_cached_wikimedia_and_retains_audit_and_unknowns(tmp_path):
    url = asset_file(tmp_path, "wiki.png")
    unknown = asset_file(tmp_path, "unknown.png", b"unknown")
    deck = Deck(id="d", name="d", language="en", cards=[
        card(image_url=url, image_source="wikimedia"),
        card(id="u", image_url=unknown, image_source="generated"),
    ])
    file = paths.DECKS_DIR / "d.json"
    file.write_text(deck.model_dump_json(), encoding="utf-8")
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "en__fr.json").write_text(json.dumps({"bank": {"image_url": url, "image_source": "wikimedia", "ipa": "/b/"}}))
    (cache / "images.json").write_text(json.dumps({"concepts": {"Q1": {"url": url, "source": "wikimedia"}}, "aliases": {"bank": "Q1"}}))
    before = file.read_bytes()
    assert ops.migrate(remove_source="wikimedia")["removed"] == 1
    assert file.read_bytes() == before
    report = ops.migrate(remove_source="wikimedia", apply=True)
    assert report["cache_references_to_remove"] == 2
    assert report["cache_images_to_retire"] == 1
    updated = json.loads(file.read_text(encoding="utf-8"))
    assert report["quarantined"] == 1
    assert updated["cards"][0]["image_url"] == ""
    assert updated["cards"][0]["image_history"][0]["before"]["source"] == "wikimedia"
    assert updated["cards"][1]["image_source"] == "generated"
    assert lib.local_path(unknown).is_file()
    assert lib.blocked("wikimedia")
    assert json.loads((cache / "images.json").read_text())["concepts"] == {}
    assert json.loads((cache / "en__fr.json").read_text())["bank"] == {"ipa": "/b/"}
    assert ops.migrate(remove_source="wikimedia", apply=True)["removed"] == 0


def test_manifest_import_context_and_provider(tmp_path):
    image = tmp_path / "external.png"
    image.write_bytes(b"external")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps([{"path": "external.png", "source": "gemini", "model": "test",
                                     "concept": "bank:finance", "scene": "deposit-cash"}]))
    result = lib.import_manifest(manifest)
    assert result["imported"] == 1
    hit = lib.lookup(card(image_concept="bank:finance", image_scene="deposit-cash"))
    assert hit["source"] == "gemini"
    assert lib.local_path(hit["url"]).read_bytes() == b"external"
    assert lib.import_manifest(manifest)["assets"] == result["assets"]


def test_skip_and_missing_context_are_not_reused(tmp_path):
    c = card(image_url=asset_file(tmp_path), image_source="grok")
    lib.register(c, verified_context=True)
    assert lib.lookup(card(skip_image=True)) is None
    assert lib.lookup(card(phrase_fr="")) is None


def test_apply_card_ignores_unverified_v1_alias(tmp_path, monkeypatch):
    from app import cache
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(cache, "CACHE_MEDIA", tmp_path / "cache/media")
    cache.reset()
    cache.put("en", "fr", "bank", {"image_url": asset_file(tmp_path), "image_source": "generated"})
    c = card()
    cache.apply_card(c, "en", "fr")
    assert c.image_url == ""


def test_manual_url_change_clears_stale_provider_and_preserves_history(tmp_path):
    from app.editor import patch_card
    c = card(image_url=asset_file(tmp_path), image_source="grok")
    deck = Deck(id="d", name="d", language="en", cards=[c])
    patch_card(deck, "c", {"image_url": "https://example.com/new.png"})
    assert c.image_source == "unknown"
    assert c.image_history[0]["before"]["source"] == "grok"


def test_manual_validation_makes_historical_image_reusable(tmp_path):
    from app.editor import patch_card
    c = card(image_url=asset_file(tmp_path), image_source="generated")
    deck = Deck(id="d", name="d", language="en", cards=[c])
    lib.register(c)
    assert lib.lookup(c) is None
    patch_card(deck, "c", {"image_context_verified": True})
    assert lib.lookup(card(lemma="banco", language="pt"))["id"] == c.image_asset_id
    patch_card(deck, "c", {"phrase_fr": "La banque est fermée."})
    assert c.image_url == ""
    assert c.image_history[-1]["reason"] == "context_changed"


def test_generated_prompt_is_registered_and_file_versions_survive(tmp_path, monkeypatch):
    from app.services.images import fetch_image
    count = []
    def generate(c, settings, deck_id):
        count.append(1)
        return asset_file(tmp_path, f"new-{len(count)}.png", str(len(count)).encode()), "grok"
    monkeypatch.setattr("app.services.images._generate", generate)
    c = card()
    fetch_image(c, "grok", Settings(), "d", allow_generate=True)
    old = c.image_url
    assert lib.lookup(c)["metadata"]["context_basis"] == "generation_prompt"
    fetch_image(c, "grok", Settings(), "d", allow_generate=True, force_generate=True)
    assert c.image_url != old and lib.local_path(old).is_file()
    assert c.image_history[-1]["before"]["url"] == old


def test_retired_source_cannot_return_through_import_or_manual_edit(tmp_path):
    from app.editor import patch_card, EditError
    c = card(image_url=asset_file(tmp_path), image_source="wikimedia")
    lib.register(c, verified_context=True)
    ops.migrate(remove_source="wikimedia", apply=True)
    assert lib.lookup(c) is None
    assert lib.local_path(c.image_url) is None
    deck = Deck(id="d", name="d", language="en", cards=[card()])
    with pytest.raises(EditError):
        patch_card(deck, "c", {"image_url": "https://upload.wikimedia.org/a.jpg"})


def test_api_preview_apply_and_inventory(tmp_path, monkeypatch):
    from app import main, store
    from fastapi.testclient import TestClient
    monkeypatch.setattr(store, "DECKS_DIR", paths.DECKS_DIR)
    deck = Deck(id="d", name="d", language="en", cards=[card(image_url="https://upload.wikimedia.org/a.png")])
    store.save_deck(deck)
    client = TestClient(main.app)
    data = {"source": "wikimedia", "destination": "remove"}
    preview = client.post("/api/decks/d/images/replace", json=data)
    assert preview.status_code == 200 and preview.json()["total"] == 1
    assert store.load_deck("d").cards[0].image_url
    response = client.post("/api/decks/d/images/replace", json={**data, "preview": False})
    assert response.status_code == 200 and response.json()["changed"] == 1
    assert not store.load_deck("d").cards[0].image_url
    assert client.get("/api/decks/d/images").json()["history"]
    assert client.post("/api/decks/d/images/replace", json={**data, "ids": ["missing"]}).status_code == 422
    stale = client.post("/api/decks/d/images/replace", json={**data, "preview": False,
                        "expected_revision": preview.json()["revision"]})
    assert stale.status_code == 409


def test_other_target_language_is_forwarded_during_replacement(tmp_path, monkeypatch):
    observed = []
    def fetch(c, bank, settings, deck_id, **kwargs):
        observed.append(kwargs["target_language"])
        return asset_file(tmp_path), "grok"
    monkeypatch.setattr("app.services.images.fetch_image", fetch)
    deck = Deck(id="d", name="d", language="pt", target_language="de", cards=[card()])
    result = ops.replace_images(deck, ImagesReplace(destination="grok", generate_missing=True, preview=False), Settings())
    assert result["changed"] == 1 and observed == ["de"]


def test_existing_phrase_cannot_inherit_another_cached_translation(tmp_path, monkeypatch):
    from app import cache
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(cache, "CACHE_MEDIA", tmp_path / "cache/media")
    cache.reset()
    cache.put("en", "fr", "bank", {"french": "banque", "phrase": "I deposit money.",
                                     "phrase_fr": "Je dépose mon argent."})
    c = card(phrase="The bank is closed today.", phrase_fr="")
    cache.apply_card(c, "en", "fr")
    assert c.phrase_fr == ""


def test_force_grok_can_replace_a_retired_wikimedia_bank(tmp_path, monkeypatch):
    from app.services.images import fetch_image
    with lib.database() as db:
        db.execute("INSERT INTO policy VALUES('wikimedia')")
    monkeypatch.setattr("app.services.images._generate", lambda *_: (asset_file(tmp_path), "grok"))
    c = card()
    assert fetch_image(c, "wikimedia", Settings(), "d", force_generate=True, allow_generate=True)[1] == "grok"


def test_openai_import_remains_distinct_from_unknown_and_grok(tmp_path):
    c = card(image_url=asset_file(tmp_path), image_source="openai")
    lib.register(c, verified_context=True, metadata={"origin": "image_gen"})
    assert lib.lookup(card(), source="grok") is None
    assert lib.lookup(card(), source="openai")["metadata"]["origin"] == "image_gen"
    assert ImagesReplace(destination="openai").destination == "openai"


def test_explicit_stock_assignment_is_preserved_by_completion(tmp_path):
    from app.pipeline import card_needs_fill, _keep_existing_image
    from app.complete import _needs_image
    from app.models import DeckFeatures
    c = card(image_url="https://images.unsplash.com/finance.jpg", image_source="unsplash")
    lib.register(c)
    deck = Deck(id="d", name="d", language="en", cards=[c],
                features=DeckFeatures(phonetic=False, phrase=False, audio=False))
    assert not card_needs_fill(deck, c)
    assert _keep_existing_image(c, False)
    assert not _needs_image(c)
    assert _needs_image(c, True)


@pytest.mark.parametrize("destination", ["codex", "openai"])
def test_codex_replacement_routes_generation_and_keeps_openai_provenance(tmp_path, monkeypatch, destination):
    calls = []
    def fetch(c, bank, settings, deck_id, **kwargs):
        calls.append((bank, kwargs))
        return asset_file(tmp_path, "codex.png"), "openai"
    monkeypatch.setattr("app.services.images.fetch_image", fetch)
    deck = Deck(id="codex-replace", name="d", language="pt", cards=[card()])
    result = ops.replace_images(deck, ImagesReplace(destination=destination, generate_missing=True, preview=False), Settings())
    assert result["changed"] == 1 and not result["errors"]
    assert deck.cards[0].image_source == "openai"
    assert calls[0][0] == "openai"
    assert calls[0][1]["generation_provider"] == "codex"
    assert calls[0][1]["allow_generate"] is True
    assert calls[0][1]["strict_source"] is True


def test_codex_replacement_reuses_openai_context_without_generation(tmp_path, monkeypatch):
    original = card(image_url=asset_file(tmp_path), image_source="openai")
    lib.register(original, verified_context=True)
    monkeypatch.setattr("app.services.images.fetch_image", lambda *a, **k: pytest.fail("must reuse"))
    deck = Deck(id="reuse", name="d", language="pt", cards=[card()])
    result = ops.replace_images(deck, ImagesReplace(destination="codex", generate_missing=True, preview=False), Settings())
    assert result["changed"] == 1 and deck.cards[0].image_source == "openai"


def test_complete_reuses_verified_asset_when_image_provider_offline(tmp_path, monkeypatch):
    from app.complete import complete_deck
    from app.models import DeckFeatures
    lib.register(card(image_url=asset_file(tmp_path), image_source="openai"), verified_context=True)
    monkeypatch.setattr("app.complete.image_generation_status", lambda *a: pytest.fail("reuse needs no login"))
    deck = Deck(id="offline-reuse", name="d", language="en", image_bank="codex",
                features=DeckFeatures(phrase=False, phonetic=False, audio=False, image=True), cards=[card()])
    report = complete_deck(deck, Settings(), persist=False)
    assert report.ok and deck.cards[0].image_source == "openai"


@pytest.mark.parametrize("bank,text,expected", [("codex", "grok", "codex"), ("openai", "claude", "codex"), ("grok", "codex", "grok"), ("wikimedia", "codex", "codex")])
def test_meta_reports_image_bank_readiness_not_text_provider(monkeypatch, bank, text, expected):
    from app import main
    calls = []
    monkeypatch.setattr(main, "load_settings", lambda: Settings(default_image_bank=bank, default_ai_provider=text))
    def status(provider, settings):
        calls.append(provider)
        return True, ""
    monkeypatch.setattr(main, "image_generation_status", status)
    result = main.meta()
    assert calls == [expected]
    assert result["image_generation"]["provider"] == expected
    assert any(item["id"] == "codex" for item in result["image_banks"])
