from app.catalog import load_cefr_rules, CEFR_LEVELS
from app.languages import LANGUAGES
from app.legacy import create_from_strategy, list_strategies

REQUIRED_A1 = {
    "present-simple", "present-be", "articles-def-indef", "personal-pronouns",
    "possessive-adj", "demonstratives", "negation", "yes-no-questions",
    "wh-questions", "there-is", "can-ability", "countable-intro", "plural-nouns",
    "adjective-place", "prepositions-place", "prepositions-time-basic",
    "imperatives", "like-want", "present-progressive",
}

MIN_COUNTS = {"a1": 18, "a2": 16, "b1": 16, "b2": 14, "c1": 12}


def test_cefr_skeleton_has_required_levels_and_a1_ids():
    rules = load_cefr_rules()
    assert CEFR_LEVELS == ["a1", "a2", "b1", "b2", "c1"]
    by_level: dict[str, list] = {level: [] for level in CEFR_LEVELS}
    for rule in rules:
        assert rule["id"] and rule["title_fr"] and rule["explain_fr"]
        assert rule["level"] in CEFR_LEVELS
        assert rule["category"]
        by_level[rule["level"]].append(rule["id"])
    for level, minimum in MIN_COUNTS.items():
        assert len(by_level[level]) >= minimum, level
    assert REQUIRED_A1 <= set(by_level["a1"])


def test_every_language_has_five_grammar_strategies():
    listed = list_strategies()
    grammar = [item for item in listed if item.get("kind") == "grammar"]
    for lang in LANGUAGES:
        for level in ("a1", "a2", "b1", "b2", "c1"):
            sid = f"grammar-{lang['id']}-{level}"
            hit = next(item for item in grammar if item["id"] == sid)
            assert hit["language"] == lang["id"]
            assert hit["cefr"] == level
            assert hit["available"] >= 12
            assert hit["phrase"] is True
            assert hit["audio"] is False


def test_create_grammar_italian_a1():
    deck = create_from_strategy("grammar-it-a1", limit=None, name="IT A1")
    assert deck.language == "it"
    assert deck.kind == "grammar"
    assert deck.source == "strategy:grammar-it-a1"
    assert deck.features.phrase is True
    assert deck.features.phonetic is False
    assert deck.features.image is False
    assert deck.features.audio is False
    assert len(deck.cards) >= 18
    assert all(card.french for card in deck.cards)
    # A fresh rule card has no example, unless the local cache already knows this
    # rule for it/fr — then both the sentence and its translation come back.
    assert all(not card.phrase or (card.phrase and card.phrase_fr) for card in deck.cards)
    assert all(card.lemma not in card.phrase for card in deck.cards)
    titles = {card.lemma for card in deck.cards}
    assert "Présent simple" in titles


def _rule_card(**kwargs):
    from app.models import Card
    base = dict(
        id="c1",
        lemma="Présent simple",
        surface="Présent simple",
        language="it",
        french="Action habituelle.",
        phrase="Lavoro ogni giorno in ufficio.",
        phrase_fr="Je travaille tous les jours au bureau.",
    )
    base.update(kwargs)
    return Card(**base)


def test_grammar_qa_allows_example_without_rule_title():
    from app.models import Deck, DeckFeatures
    from app.protocol import qa_deck
    deck = Deck(
        id="g",
        name="g",
        language="it",
        kind="grammar",
        features=DeckFeatures(phrase=True, phonetic=False, audio=False, image=False),
        cards=[_rule_card()],
    )
    report = qa_deck(deck)
    assert report.ok


def test_grammar_qa_requires_phrase():
    from app.models import Deck, DeckFeatures
    from app.protocol import qa_deck
    deck = Deck(
        id="g",
        name="g",
        language="it",
        kind="grammar",
        features=DeckFeatures(phrase=True, phonetic=False, audio=False, image=False),
        cards=[_rule_card(phrase="", phrase_fr="")],
    )
    report = qa_deck(deck)
    assert not report.ok


def test_vocab_qa_still_requires_lemma_in_phrase():
    from app.models import Deck, DeckFeatures
    from app.protocol import qa_deck
    deck = Deck(
        id="v",
        name="v",
        language="it",
        kind="vocab",
        features=DeckFeatures(phrase=True, phonetic=False, audio=False, image=False),
        cards=[_rule_card(
            lemma="ciao",
            surface="ciao",
            french="salut",
            phrase="Lavoro ogni giorno.",
            phrase_fr="Je travaille.",
        )],
    )
    report = qa_deck(deck)
    assert not report.ok
    assert any("mot cible" in err for err in report.errors)


def test_local_grammar_sentences_do_not_treat_title_as_noun():
    from app.models import Card, Settings
    from app.services.sentences import generate_sentences
    cards = [Card(
        id="1",
        lemma="Présent simple",
        surface="Présent simple",
        language="it",
        french="Action habituelle.",
    )]
    out = generate_sentences(cards, "local", Settings(), "Italien", "français", kind="grammar")
    assert out == {}


def test_create_deck_grammar_calls_fill_missing_without_crash(monkeypatch):
    from app.main import create_deck
    from app.models import DeckCreate, DeckFeatures, Settings
    from app.store import delete_deck
    from app import main as mainmod

    monkeypatch.setattr(mainmod, "load_settings", lambda: Settings(default_ai_provider="local"))
    deck = create_deck(DeckCreate(
        name="Grammaire A1 — Italien",
        language="it",
        language_label="Italien",
        source_id="grammar-it-a1",
        features=DeckFeatures(phrase=True, phonetic=False, audio=False, image=False),
    ))
    try:
        assert deck.kind == "grammar"
        assert len(deck.cards) >= 18
        assert all(card.french for card in deck.cards)
    finally:
        delete_deck(deck.id)
