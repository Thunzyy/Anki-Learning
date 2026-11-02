from app.legacy import create_from_strategy, list_strategies


def test_list_strategies_includes_ngsl_with_sizes():
    strategies = list_strategies()
    ngsl = next(item for item in strategies if item["id"] == "ngsl")
    assert ngsl["language"] == "en"
    assert ngsl["label"].startswith("NGSL")
    assert 1000 in ngsl["sizes"]
    assert ngsl["available"] >= 2000


def test_create_from_strategy_respects_limit():
    deck = create_from_strategy("ngsl", limit=3, name="NGSL 3")
    assert deck.language == "en"
    assert deck.source == "strategy:ngsl"
    assert len(deck.cards) == 3
    assert all(card.french for card in deck.cards)


def test_create_from_strategy_rejects_unknown():
    try:
        create_from_strategy("nope", limit=10, name="x")
    except ValueError as exc:
        assert "stratégie" in str(exc).lower() or "inconnue" in str(exc).lower()
    else:
        raise AssertionError("expected ValueError")


def test_every_ui_language_has_spoken_frequency():
    from app.languages import LANGUAGES

    spoken = {item["language"] for item in list_strategies() if item["id"].startswith("spoken-")}
    missing = [item["id"] for item in LANGUAGES if item["id"] not in spoken]
    assert missing == [], f"missing spoken lists: {missing}"


def test_spoken_italian_is_most_common_first():
    spoken = next(item for item in list_strategies() if item["id"] == "spoken-it")
    assert spoken["available"] >= 1000
    assert 500 in spoken["sizes"]
    deck = create_from_strategy("spoken-it", limit=5, name="IT 5")
    assert [card.lemma for card in deck.cards] == ["e", "non", "che", "di", "la"]


def test_create_from_strategy_accepts_any_count_and_all_words():
    custom = create_from_strategy("ngsl", limit=7, name="NGSL 7")
    assert len(custom.cards) == 7
    assert custom.cards[0].lemma.lower() in {"the", "be", "and"}

    full = create_from_strategy("ngsl", limit=None, name="NGSL all")
    listed = next(item for item in list_strategies() if item["id"] == "ngsl")
    assert len(full.cards) == listed["available"]
    assert len(full.cards) > 7
