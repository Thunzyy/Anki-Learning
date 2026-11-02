import json

import pytest

from app.models import Card, Deck, Settings
from app.store import load_deck, save_deck


def _deck() -> Deck:
    return Deck(
        id="atomic-deck",
        name="Atomic",
        language="en",
        cards=[Card(id="c1", lemma="house", surface="house", language="en", french="maison")],
    )


def test_save_deck_replaces_file_without_leaving_empty_json(tmp_path, monkeypatch):
    from app import store

    monkeypatch.setattr(store, "DECKS_DIR", tmp_path)
    save_deck(_deck())
    path = tmp_path / "atomic-deck.json"
    raw = path.read_text(encoding="utf-8")
    assert raw.strip().startswith("{")
    payload = json.loads(raw)
    assert payload["cards"][0]["lemma"] == "house"
    assert list(tmp_path.glob("*.tmp")) == []
    loaded = load_deck("atomic-deck")
    assert loaded.cards[0].lemma == "house"


def test_load_deck_rejects_persistently_empty_file(tmp_path, monkeypatch):
    from app import store

    monkeypatch.setattr(store, "DECKS_DIR", tmp_path)
    (tmp_path / "atomic-deck.json").write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="vide|JSON|json"):
        load_deck("atomic-deck")


def test_load_deck_accepts_grok_as_image_bank(tmp_path, monkeypatch):
    from app import store

    monkeypatch.setattr(store, "DECKS_DIR", tmp_path)
    payload = json.loads(_deck().model_dump_json())
    payload["image_bank"] = "grok"
    (tmp_path / "atomic-deck.json").write_text(json.dumps(payload), encoding="utf-8")

    assert load_deck("atomic-deck").image_bank == "grok"


def test_image_concurrency_setting_is_bounded():
    assert Settings(image_concurrency=64).image_concurrency == 64
    with pytest.raises(ValueError):
        Settings(image_concurrency=0)
    with pytest.raises(ValueError):
        Settings(image_concurrency=65)


@pytest.mark.parametrize("word", ["può", "è"])
def test_loading_reviewed_function_verb_keeps_pos_and_disk_bytes(tmp_path, monkeypatch, word):
    from app import store

    monkeypatch.setattr(store, "DECKS_DIR", tmp_path)
    deck = Deck(id="reviewed-verb", name="Reviewed Italian", language="it", cards=[
        Card(id="verb-1", lemma=word, surface=word, language="it", category="Verbe",
             french="peut" if word == "può" else "est", skip_image=True, audio_url="/original.mp3")])
    path = tmp_path / "reviewed-verb.json"
    path.write_text(deck.model_dump_json(), encoding="utf-8")
    original = path.read_bytes()

    loaded = load_deck(deck.id).cards[0]
    assert loaded.category == "Verbe"
    assert loaded.skip_image is True and not loaded.image_url
    assert loaded.audio_url == "/original.mp3"
    assert path.read_bytes() == original
