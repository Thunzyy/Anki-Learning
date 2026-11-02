import pytest

from app.models import Card, Deck, DeckFeatures, Settings
from app.pipeline import sync_stages
from app.protocol import apply_normalize


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    from app import cache
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(cache, "CACHE_MEDIA", tmp_path / "cache" / "media")
    cache.reset()


def _ready_card(i: int, lemma: str, french: str) -> Card:
    card = Card(
        id=f"c{i}",
        lemma=lemma,
        surface=lemma,
        language="en",
        french=french,
        category="Nom",
        ipa="/x/",
        phrase=f"I need this {lemma} right now.",
        phrase_fr=f"J'ai besoin de {french} maintenant.",
        audio_url=f"/api/media/d/{lemma}.mp3",
    )
    return apply_normalize(card)


def test_sync_stages_recovers_from_failed_when_fields_are_full():
    deck = Deck(
        id="d1",
        name="t",
        language="en",
        features=DeckFeatures(phrase=True, phonetic=True, audio=True, image=True),
        cards=[_ready_card(1, "house", "maison")],
        stages={"translate": "failed", "phonetic": "failed", "sentences": "failed", "images": "failed", "audio": "failed"},
    )
    deck.cards[0].image_url = "https://example.com/house.jpg"
    sync_stages(deck)
    assert deck.stages["translate"] == "passed"
    assert deck.stages["phonetic"] == "passed"
    assert deck.stages["sentences"] == "passed"
    assert deck.stages["images"] == "passed"
    assert deck.stages["audio"] == "passed"


def test_complete_deck_fills_until_no_leftover(monkeypatch):
    from app.complete import complete_deck

    def fake_fetch(card, _bank, _settings, deck_id="", allow_generate=False, force_generate=False, target_language="fr", generation_provider=""):
        if card.lemma in {"house", "dog"} or allow_generate:
            return f"https://example.com/{card.lemma}.jpg", "wikimedia" if not allow_generate else "generated"
        return "", ""

    monkeypatch.setattr("app.pipeline.fetch_image", fake_fetch)
    deck = Deck(
        id="complete-ok",
        name="ok",
        language="en",
        ai_provider="grok",
        features=DeckFeatures(phrase=True, phonetic=True, audio=True, image=True),
        cards=[
            _ready_card(1, "house", "maison"),
            _ready_card(2, "dog", "chien"),
            _ready_card(3, "half", "moitié"),
            _ready_card(4, "she", "elle"),
        ],
    )
    report = complete_deck(deck, Settings(default_ai_provider="grok", xai_api_key="xai-test"), persist=False)
    assert report.ok is True
    assert report.leftover == 0
    by = {card.lemma: card for card in deck.cards}
    assert by["house"].image_url
    assert by["dog"].image_url
    assert by["half"].image_url
    assert by["she"].skip_image is True
    assert not by["she"].image_url


def test_complete_deck_stops_and_explains_when_ai_cannot_generate(monkeypatch):
    from app.complete import complete_deck

    monkeypatch.setattr("app.complete.image_generation_status", lambda *a: (False, "Grok indisponible"))

    monkeypatch.setattr("app.pipeline.fetch_image", lambda *_a, **_k: ("", ""))
    deck = Deck(
        id="complete-stop",
        name="stop",
        language="en",
        ai_provider="claude",
        features=DeckFeatures(phrase=False, phonetic=False, audio=False, image=True),
        cards=[_ready_card(1, "half", "moitié")],
    )
    report = complete_deck(deck, Settings(default_ai_provider="claude"), persist=False)
    assert report.ok is False
    assert report.leftover == 1
    assert "Grok" in report.reason


def test_complete_deck_gives_up_when_text_never_fills(monkeypatch):
    """A dead text provider must stop the loop, not retry forever."""
    from app.complete import complete_deck

    text_waves = []

    def fake_fill(deck, settings, **kwargs):
        if not kwargs.get("generate_images"):
            text_waves.append(kwargs.get("limit"))
        return deck

    monkeypatch.setattr("app.complete.fill_missing", fake_fill)
    empty = Card(id="c1", lemma="autonomy", surface="autonomy", language="en", category="Nom")
    deck = Deck(
        id="complete-text-dead",
        name="text dead",
        language="en",
        ai_provider="grok",
        features=DeckFeatures(phrase=True, phonetic=True, audio=False, image=False),
        cards=[empty],
    )

    report = complete_deck(deck, Settings(default_ai_provider="grok", xai_api_key="xai-test"), persist=False)

    assert report.ok is False
    assert report.leftover == 1
    assert len(text_waves) == 2


def test_complete_deck_replace_images_uses_ai_only(monkeypatch):
    from app.complete import complete_deck

    def fake_fetch(card, _bank, _settings, deck_id="", allow_generate=False, force_generate=False, target_language="fr", generation_provider=""):
        assert force_generate is True
        return f"/api/media/d/{card.lemma}.jpg", "generated"

    monkeypatch.setattr("app.pipeline.fetch_image", fake_fetch)
    house = _ready_card(1, "house", "maison")
    house.image_url = "https://example.com/ranch-house.jpg"
    house.image_source = "wikimedia"
    dog = _ready_card(2, "dog", "chien")
    dog.image_url = "/api/media/d/dog.jpg"
    dog.image_source = "generated"
    half = _ready_card(3, "half", "moitié")
    she = _ready_card(4, "she", "elle")
    deck = Deck(
        id="replace-ai",
        name="replace",
        language="en",
        ai_provider="grok",
        features=DeckFeatures(phrase=True, phonetic=True, audio=True, image=True),
        cards=[house, dog, half, she],
    )
    report = complete_deck(
        deck,
        Settings(default_ai_provider="grok", xai_api_key="xai-test"),
        persist=False,
        replace_images=True,
    )
    assert report.ok is True
    by = {card.lemma: card for card in deck.cards}
    assert by["house"].image_source == "generated"
    assert by["house"].image_url == "/api/media/d/house.jpg"
    assert by["dog"].image_url == "/api/media/d/dog.jpg"
    assert by["half"].image_source == "generated"
    assert by["she"].skip_image is True
    assert not by["she"].image_url


def test_complete_deck_uses_requested_image_concurrency(monkeypatch):
    from app.complete import complete_deck

    seen: list[int] = []

    def fake_fill(deck, _settings, *, ids, limit, generate_images, replace_images, concurrency):
        seen.append(concurrency)
        for card in deck.cards:
            if card.id in ids:
                card.image_url = f"/api/media/{deck.id}/{card.lemma}.jpg"
                card.image_source = "generated"
        return deck

    monkeypatch.setattr("app.complete.fill_missing", fake_fill)
    deck = Deck(
        id="parallel-images",
        name="parallel",
        language="en",
        ai_provider="grok",
        features=DeckFeatures(phrase=True, phonetic=True, audio=True, image=True),
        cards=[
            _ready_card(1, "house", "maison"),
            _ready_card(2, "dog", "chien"),
            _ready_card(3, "tree", "arbre"),
            _ready_card(4, "book", "livre"),
        ],
    )

    report = complete_deck(
        deck,
        Settings(default_ai_provider="grok", xai_api_key="xai-test"),
        image_batch=4,
        persist=False,
    )

    assert report.ok is True
    assert seen == [4]


def test_complete_deck_uses_image_concurrency_setting_by_default(monkeypatch):
    from app.complete import complete_deck

    seen: list[int] = []

    def fake_fill(deck, _settings, *, ids, limit, generate_images, replace_images, concurrency):
        seen.append(concurrency)
        for card in deck.cards:
            if card.id in ids:
                card.image_url = f"/api/media/{deck.id}/{card.lemma}.jpg"
                card.image_source = "generated"
        return deck

    monkeypatch.setattr("app.complete.fill_missing", fake_fill)
    deck = Deck(
        id="settings-parallel-images",
        name="parallel settings",
        language="en",
        ai_provider="grok",
        features=DeckFeatures(phrase=True, phonetic=True, audio=True, image=True),
        cards=[_ready_card(i, f"word{i}", f"mot{i}") for i in range(1, 9)],
    )

    report = complete_deck(
        deck,
        Settings(default_ai_provider="grok", xai_api_key="xai-test", image_concurrency=8),
        persist=False,
    )

    assert report.ok is True
    assert seen == [8]
