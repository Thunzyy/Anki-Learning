from pathlib import Path

from app.legacy import build_legacy_deck, cards_from_rows, import_legacy_decks, parse_sound
from app.models import Deck
from app.store import list_decks, load_deck, save_deck


def _pt_rows():
    return [
        {
            "Portuguese": "casa",
            "Pronunciation": "/ˈkazɐ/",
            "French": "maison",
            "Category": "Nom",
            "Rank": "#1",
            "Phrase": "Eu voltei para casa cedo.",
            "Traduction": "Je suis rentré à la maison tôt.",
            "Audio": "[sound:po_casa.mp3]",
        },
        {
            "Portuguese": "que",
            "Pronunciation": "/ki/",
            "French": "que, quoi",
            "Category": "Grammaire",
            "Rank": "#2",
            "Phrase": "O que você quer comer?",
            "Traduction": "Qu'est-ce que tu veux manger ?",
            "Audio": "[sound:po_que.mp3]",
        },
    ]


def test_parse_sound_extracts_filename():
    assert parse_sound("[sound:po_casa.mp3]") == "po_casa.mp3"
    assert parse_sound("") == ""


def test_cards_from_rows_maps_translation_phrase_and_audio():
    cards = cards_from_rows("pt", _pt_rows(), deck_id="legacy-pt-test", with_audio=True)
    assert [card.lemma for card in cards] == ["casa", "que"]
    assert cards[0].french == "maison"
    assert cards[0].ipa == "/ˈkazɐ/"
    assert "casa" in cards[0].phrase
    assert cards[0].phrase_fr.startswith("Je suis")
    assert cards[0].audio_url.endswith("po_casa.mp3")
    assert cards[1].skip_image is True


def test_cards_from_rows_respects_limit():
    cards = cards_from_rows("pt", _pt_rows(), deck_id="legacy-pt-test", limit=1)
    assert len(cards) == 1
    assert cards[0].lemma == "casa"


def test_build_legacy_deck_sets_features_and_stable_id():
    spec = {
        "id": "legacy-pt-test",
        "name": "Brazilian Portuguese Test",
        "language": "pt",
        "language_label": "Portugais (Brésil)",
        "phrase": True,
        "audio": True,
        "image": False,
    }
    deck = build_legacy_deck(spec, _pt_rows())
    assert deck.id == "legacy-pt-test"
    assert deck.source == "legacy"
    assert deck.features.phrase is True
    assert deck.features.image is False
    assert deck.features.audio is True
    assert len(deck.cards) == 2
    assert deck.stages["images"] == "skipped"
    assert deck.stages["translate"] == "passed"


def test_import_legacy_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr("app.store.DECKS_DIR", tmp_path)
    spec = {
        "id": "legacy-pt-test",
        "name": "Brazilian Portuguese Test",
        "language": "pt",
        "language_label": "Portugais (Brésil)",
        "rows": _pt_rows(),
        "phrase": True,
        "audio": False,
        "image": False,
    }
    first = import_legacy_decks([spec])
    mtime = (tmp_path / "legacy-pt-test.json").stat().st_mtime
    second = import_legacy_decks([spec])
    assert [deck.id for deck in first] == ["legacy-pt-test"]
    assert second == []
    assert len(list(tmp_path.glob("*.json"))) == 1
    assert (tmp_path / "legacy-pt-test.json").stat().st_mtime == mtime
    loaded = load_deck("legacy-pt-test")
    assert loaded.cards[0].french == "maison"


def test_list_decks_omits_card_payloads(tmp_path, monkeypatch):
    monkeypatch.setattr("app.store.DECKS_DIR", tmp_path)
    deck = build_legacy_deck(
        {
            "id": "legacy-pt-test",
            "name": "Test",
            "language": "pt",
            "language_label": "Portugais",
            "phrase": True,
            "audio": False,
            "image": False,
        },
        _pt_rows(),
    )
    save_deck(deck)
    listed = list_decks()
    assert listed[0].card_count == 2
    assert listed[0].cards == []
    assert load_deck("legacy-pt-test").cards[0].lemma == "casa"
