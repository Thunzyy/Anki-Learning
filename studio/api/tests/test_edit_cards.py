import pytest

from app.editor import EditError, add_words, delete_cards, patch_card, rename_deck
from app.models import Card, Deck, DeckFeatures
from app.protocol import fold


def _deck(*words: str) -> Deck:
    cards = [
        Card(id=f"c{i}", lemma=word, surface=word, language="it", french="")
        for i, word in enumerate(words, start=1)
    ]
    return Deck(
        id="d1",
        name="Test",
        language="it",
        language_label="Italien",
        cards=cards,
        features=DeckFeatures(phrase=True, phonetic=True, audio=False, image=False),
    )


def test_add_words_appends_unique_lemmas():
    deck = _deck("ciao")
    add_words(deck, ["grazie", "  acqua  ", ""])
    assert [card.lemma for card in deck.cards] == ["ciao", "grazie", "acqua"]


def test_add_words_skips_duplicates_in_a_list():
    deck = _deck("ciao")
    add_words(deck, ["Ciao", "grazie"])
    assert [card.lemma for card in deck.cards] == ["ciao", "grazie"]


def test_add_words_rejects_when_all_duplicates():
    deck = _deck("ciao")
    with pytest.raises(EditError, match="aucun mot"):
        add_words(deck, ["Ciao"])


def test_patch_card_updates_fields():
    deck = _deck("ciao")
    patch_card(deck, "c1", {"french": "salut", "ipa": "/ˈtʃa.o/", "phrase": "Ciao amico mio."})
    card = deck.cards[0]
    assert card.french == "salut"
    assert card.ipa == "/ˈtʃa.o/"
    assert card.phrase == "Ciao amico mio."


def test_patch_card_rejects_unknown_id():
    deck = _deck("ciao")
    with pytest.raises(EditError, match="introuvable"):
        patch_card(deck, "nope", {"french": "x"})


def test_patch_card_rejects_duplicate_lemma():
    deck = _deck("ciao", "grazie")
    with pytest.raises(EditError, match="doublon"):
        patch_card(deck, "c2", {"lemma": "CIAO", "surface": "CIAO"})


def test_delete_one_card():
    deck = _deck("ciao", "grazie")
    delete_cards(deck, ["c1"])
    assert [card.lemma for card in deck.cards] == ["grazie"]


def test_bulk_delete_cards():
    deck = _deck("ciao", "grazie", "acqua")
    delete_cards(deck, ["c1", "c3"])
    assert [card.lemma for card in deck.cards] == ["grazie"]


def test_rename_deck():
    deck = _deck("ciao")
    rename_deck(deck, "  Italien A1  ")
    assert deck.name == "Italien A1"


def test_fold_used_for_duplicates():
    assert fold("Ciao") == fold("ciao")
