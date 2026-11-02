from pathlib import Path

from app.models import Card, Deck, DeckFeatures, Settings
from app.pipeline import fill_missing
from app.protocol import fold


def test_cache_put_and_get(tmp_path, monkeypatch):
    from app import cache

    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    cache.reset()
    cache.put("en", "fr", "hello", {"french": "salut", "ipa": "/həˈləʊ/", "phrase": "Say hello now please.", "phrase_fr": "Dis salut maintenant."})
    hit = cache.get("en", "fr", "HELLO")
    assert hit is not None
    assert hit["french"] == "salut"
    assert hit["ipa"].startswith("/")
    assert fold("hello") in (tmp_path / "en__fr.json").read_text(encoding="utf-8")


def test_apply_cache_fills_empty_fields_only(tmp_path, monkeypatch):
    from app import cache

    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    cache.reset()
    cache.put("en", "fr", "be", {
        "french": "être",
        "ipa": "/bi/",
        "phrase": "I need this be right now.",
        "phrase_fr": "J'en ai besoin maintenant.",
        "category": "Verbe",
    })
    card = Card(id="c1", lemma="be", surface="be", language="en", french="EXISTE", ipa="", phrase="", phrase_fr="")
    deck = Deck(id="d", name="t", language="en", target_language="fr", cards=[card])
    filled = cache.apply_deck(deck)
    assert filled == 1
    assert deck.cards[0].french == "EXISTE"
    assert deck.cards[0].ipa == "/bi/"
    assert deck.cards[0].phrase == ""  # A different supplied meaning must not inherit a cached scene.


def test_fill_missing_uses_cache_and_skips_ai(tmp_path, monkeypatch):
    from app import cache

    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    cache.reset()
    cache.put("en", "fr", "water", {
        "french": "eau",
        "ipa": "/ˈwɔː.tə/",
        "phrase": "I need this water right now.",
        "phrase_fr": "J'ai besoin de cette eau.",
    })

    def boom(*_a, **_k):
        raise AssertionError("AI should not run for a cached word")

    monkeypatch.setattr("app.pipeline.generate_sentences", boom)
    monkeypatch.setattr("app.pipeline.generate_glosses", boom)
    monkeypatch.setattr("app.pipeline.generate_ipa", boom)
    deck = Deck(
        id="d-cache",
        name="t",
        language="en",
        target_language="fr",
        target_label="Français",
        features=DeckFeatures(phrase=True, phonetic=True, audio=False, image=False),
        cards=[Card(id="c1", lemma="water", surface="water", language="en")],
    )
    fill_missing(deck, Settings(default_ai_provider="local"))
    assert deck.cards[0].french == "eau"
    assert deck.cards[0].ipa.startswith("/")
    assert "water" in deck.cards[0].phrase.lower()


def test_fill_missing_writes_new_words_to_cache(tmp_path, monkeypatch):
    from app import cache

    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    cache.reset()
    monkeypatch.setattr(
        "app.pipeline.generate_sentences",
        lambda cards, *_a, **_k: {
            card.lemma.casefold(): {
                "sentence": f"I need this {card.lemma} right now.",
                "translation": f"fr {card.lemma}",
            }
            for card in cards
        },
    )
    deck = Deck(
        id="d-write",
        name="t",
        language="en",
        target_language="fr",
        features=DeckFeatures(phrase=True, phonetic=False, audio=False, image=False),
        cards=[Card(id="c1", lemma="storm", surface="storm", language="en", french="tempête")],
    )
    fill_missing(deck, Settings())
    hit = cache.get("en", "fr", "storm")
    assert hit is not None
    assert hit["french"] == "tempête"
    assert "storm" in (hit.get("phrase") or "").lower()


def test_image_cache_hits_any_language_via_concept_labels(tmp_path, monkeypatch):
    from app import cache

    media = tmp_path / "media"
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(cache, "CACHE_MEDIA", tmp_path / "cache" / "media")
    monkeypatch.setattr(cache, "MEDIA_DIR", media)
    cache.reset()
    src = media / "deck-en" / "house.jpg"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"HOUSEJPG")
    english = Card(
        id="en1",
        lemma="house",
        surface="house",
        language="en",
        french="maison",
        image_url="/api/media/deck-en/house.jpg",
        image_source="generated",
        image_concept="Q3947",
    )
    assert cache.remember_image(
        english,
        extra_labels={"en": "house", "it": "casa", "de": "Haus", "fr": "maison"},
    )
    url_it, source = cache.lookup_image("casa", "Haus", "it", target="de")
    assert source == "generated"
    assert url_it.startswith("/api/cache-media/")
    path = cache.cache_media_path(url_it.split("/")[-2], url_it.rsplit("/", 1)[-1])
    assert path is not None
    assert path.read_bytes() == b"HOUSEJPG"
    url_de, _ = cache.lookup_image("Haus", "casa", "de", target="it")
    assert url_de == url_it


def test_generated_image_is_reused_by_french_sense(tmp_path, monkeypatch):
    from app import cache

    media = tmp_path / "media"
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(cache, "CACHE_MEDIA", tmp_path / "cache" / "media")
    monkeypatch.setattr(cache, "MEDIA_DIR", media)
    cache.reset()
    src = media / "deck-en" / "half.jpg"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"FAKEJPG")
    english = Card(
        id="en1",
        lemma="half",
        surface="half",
        language="en",
        french="moitié",
        image_url="/api/media/deck-en/half.jpg",
        image_source="generated",
    )
    assert cache.remember_image(english) is True
    url, source = cache.lookup_image("metade", "moitié", "pt")
    assert source == "generated"
    assert url.startswith("/api/cache-media/")
    path = cache.cache_media_path(url.split("/")[-2], url.rsplit("/", 1)[-1])
    assert path is not None
    assert path.read_bytes() == b"FAKEJPG"

    portuguese = Card(
        id="pt1",
        lemma="metade",
        surface="metade",
        language="pt",
        french="moitié",
    )
    assert cache.apply_card(portuguese, "pt", "fr") is False
    assert portuguese.image_url == ""
    assert portuguese.image_source == ""


def test_remember_deck_writes_the_image_index_once(tmp_path, monkeypatch):
    """Saving per card re-read and rewrote the whole index, which stalls on big decks."""
    from app import cache

    media = tmp_path / "media"
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(cache, "CACHE_MEDIA", tmp_path / "cache" / "media")
    monkeypatch.setattr(cache, "MEDIA_DIR", media)
    cache.reset()
    saves = []
    real_save = cache._save_images_index
    monkeypatch.setattr(
        cache,
        "_save_images_index",
        lambda data: (saves.append(1), real_save(data))[1],
    )
    cards = []
    for index in range(6):
        src = media / "deck-en" / f"w{index}.jpg"
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_bytes(b"JPG")
        cards.append(Card(
            id=f"c{index}",
            lemma=f"word{index}",
            surface=f"word{index}",
            language="en",
            french=f"mot{index}",
            image_url=f"/api/media/deck-en/w{index}.jpg",
            image_source="generated",
        ))

    cache.remember_deck(Deck(id="deck-en", name="EN", language="en", target_language="fr", cards=cards))

    assert len(saves) == 1
    assert cache.lookup_image("palavra3", "mot3", "pt")[1] == "generated"


def test_generated_image_cache_disambiguates_same_lemma_by_translation(tmp_path, monkeypatch):
    from app import cache

    media = tmp_path / "media"
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(cache, "CACHE_MEDIA", tmp_path / "cache" / "media")
    monkeypatch.setattr(cache, "MEDIA_DIR", media)
    cache.reset()
    for deck_id, payload in (
        ("deck-en", ("en1", "gift", "en", "cadeau", b"GIFT")),
        ("deck-de", ("de1", "Gift", "de", "poison", b"POISON")),
    ):
        card_id, lemma, language, french, content = payload
        src = media / deck_id / f"{lemma.lower()}.jpg"
        src.parent.mkdir(parents=True)
        src.write_bytes(content)
        assert cache.remember_image(Card(
            id=card_id,
            lemma=lemma,
            surface=lemma,
            language=language,
            french=french,
            image_url=f"/api/media/{deck_id}/{src.name}",
            image_source="generated",
        ))

    gift_url, _ = cache.lookup_image("gift", "cadeau", "en")
    poison_url, _ = cache.lookup_image("Gift", "poison", "de")
    gift_path = cache.cache_media_path(gift_url.split("/")[-2], gift_url.rsplit("/", 1)[-1])
    poison_path = cache.cache_media_path(poison_url.split("/")[-2], poison_url.rsplit("/", 1)[-1])

    assert gift_path is not None and gift_path.read_bytes() == b"GIFT"
    assert poison_path is not None and poison_path.read_bytes() == b"POISON"


def test_scene_cache_reuses_same_situation_across_languages(tmp_path, monkeypatch):
    from app import cache

    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path / "cache")
    cache.reset()
    english = Card(
        id="en1",
        lemma="house",
        surface="house",
        language="en",
        french="maison",
        phrase="The family returns to the house before dinner.",
        phrase_fr="La famille rentre à la maison avant le dîner.",
        category="Nom",
    )
    assert cache.remember_scenes([english]) == 1

    portuguese = Card(
        id="pt1",
        lemma="casa",
        surface="casa",
        language="pt",
        french="maison",
        category="Nom",
    )
    assert cache.apply_card(portuguese, "pt", "fr") is True
    assert portuguese.phrase == ""
    assert portuguese.phrase_fr == "La famille rentre à la maison avant le dîner."


def test_fetch_image_uses_context_cache_before_network(tmp_path, monkeypatch):
    from app import image_library, paths
    from app.services.images import fetch_image
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    monkeypatch.setattr(paths, "MEDIA_DIR", tmp_path / "media")
    src = paths.MEDIA_DIR / "deck-en" / "half.jpg"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"FAKEJPG")
    english = Card(id="en1", lemma="half", surface="half", language="en", french="moitié",
                   phrase_fr="Je coupe la pomme en deux.", image_url="/api/media/deck-en/half.jpg",
                   image_source="grok")
    image_library.register(english, verified_context=True)
    def boom(*_a, **_k):
        raise AssertionError("network should not run when context is cached")
    monkeypatch.setattr("app.services.images.httpx.Client", boom)
    portuguese = Card(id="pt1", lemma="metade", surface="metade", language="pt", french="moitié",
                      phrase_fr=english.phrase_fr)
    url, source = fetch_image(portuguese, "grok", Settings(), deck_id="pt-deck")
    assert source == "grok"
    assert url == english.image_url


def test_lookup_image_ignores_dangling_local_cache_url(tmp_path, monkeypatch):
    from app import cache

    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(cache, "CACHE_MEDIA", tmp_path / "cache" / "media")
    cache.reset()
    cache._save_images_index({
        "concepts": {
            "gen:dire": {
                "id": "gen:dire",
                "source": "generated",
                "file": "missing.jpg",
                "url": "/api/cache-media/concepts/missing.jpg",
                "folder": "concepts",
            }
        },
        "aliases": {"say": "gen:dire", "dire": "gen:dire"},
    })

    assert cache.lookup_image("say", "dire", "en") == ("", "")


def test_apply_card_ignores_dangling_word_cache_image(tmp_path, monkeypatch):
    from app import cache

    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(cache, "CACHE_MEDIA", tmp_path / "cache" / "media")
    cache.reset()
    cache.put("en", "fr", "say", {
        "french": "dire",
        "image_url": "/api/cache-media/concepts/missing.jpg",
        "image_source": "generated",
    })
    card = Card(id="c1", lemma="say", surface="say", language="en")

    cache.apply_card(card, "en", "fr")

    assert card.french == "dire"
    assert card.image_url == ""


def test_create_from_strategy_hydrates_cached_words(tmp_path, monkeypatch):
    from app import cache
    from app.legacy import create_from_strategy

    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    cache.reset()
    cache.put("it", "fr", "e", {"french": "et", "phrase": "e non che di la qui.", "phrase_fr": "et pas que."})
    deck = create_from_strategy("spoken-it", limit=5, name="IT cache")
    first = next(card for card in deck.cards if card.lemma == "e")
    assert first.french == "et"
    assert first.phrase.startswith("e non")
