import pytest

from app.editor import add_words
from app.models import Card, Deck, DeckFeatures, Settings
from app.pipeline import fill_missing


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    from app import cache
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(cache, "CACHE_MEDIA", tmp_path / "cache" / "media")
    cache.reset()


def _deck(*words: str, **features) -> Deck:
    cards = [
        Card(id=f"c{i}", lemma=word, surface=word, language="pt", french="")
        for i, word in enumerate(words, start=1)
    ]
    return Deck(
        id="d1",
        name="Test",
        language="pt",
        language_label="Portugais",
        target_language="fr",
        target_label="Français",
        ai_provider="local",
        image_bank="wikimedia",
        cards=cards,
        features=DeckFeatures(
            phrase=features.get("phrase", True),
            phonetic=features.get("phonetic", True),
            audio=False,
            image=features.get("image", False),
        ),
    )


def test_add_words_uses_lexicon_when_available():
    deck = _deck()
    add_words(deck, ["casa"])
    card = deck.cards[0]
    assert card.french == "maison"
    assert card.ipa == "/ˈkazɐ/"
    assert "casa" in card.phrase.lower()
    assert card.phrase_fr


def test_fill_missing_fills_empty_without_overwriting():
    deck = _deck("casa")
    deck.cards[0].french = "MAISON_CUSTOM"
    fill_missing(deck, Settings(default_ai_provider="local"))
    assert deck.cards[0].french == "MAISON_CUSTOM"
    assert deck.cards[0].ipa == "/ˈkazɐ/"
    assert "casa" in deck.cards[0].phrase.lower()


def test_fill_missing_only_updates_given_ids():
    deck = _deck("casa", "água")
    fill_missing(deck, Settings(), ids=["c1"])
    assert deck.cards[0].french == "maison"
    assert deck.cards[1].french == ""
    assert deck.cards[1].phrase == ""


def test_fill_missing_skips_disabled_features(monkeypatch):
    calls = []

    def boom(*_args, **_kwargs):
        calls.append("image")
        raise AssertionError("image disabled")

    monkeypatch.setattr("app.pipeline.fetch_image", boom)
    monkeypatch.setattr("app.pipeline.generate_audio", boom)
    monkeypatch.setattr("app.pipeline.generate_ipa", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ipa")))
    monkeypatch.setattr("app.pipeline.generate_sentences", lambda *a, **k: (_ for _ in ()).throw(AssertionError("phrase")))

    deck = _deck("casa", phrase=False, phonetic=False, image=False)
    fill_missing(deck, Settings())
    assert deck.cards[0].french == "maison"
    assert deck.cards[0].ipa == ""
    assert deck.cards[0].phrase == ""
    assert calls == []


def test_fill_missing_uses_ai_gloss_when_not_in_lexicon(monkeypatch):
    monkeypatch.setattr(
        "app.pipeline.generate_glosses",
        lambda cards, *_args, **_kwargs: {
            cards[0].lemma.casefold(): {"french": "bonjour", "category": "Nom"}
        },
    )
    monkeypatch.setattr("app.pipeline.generate_ipa", lambda cards, *_a, **_k: {cards[0].lemma.casefold(): "/bɔ̃.ʒuʁ/"})
    monkeypatch.setattr(
        "app.pipeline.generate_sentences",
        lambda cards, *_a, **_k: {
            cards[0].lemma.casefold(): {
                "sentence": "Eu disse xyzzy para o amigo agora.",
                "translation": "J'ai dit xyzzy à l'ami maintenant.",
            }
        },
    )
    deck = _deck("xyzzy")
    deck.language = "it"
    deck.cards[0].language = "it"
    fill_missing(deck, Settings())
    card = deck.cards[0]
    assert card.french == "bonjour"
    assert card.ipa == "/bɔ̃.ʒuʁ/"
    assert "xyzzy" in card.phrase
    assert card.phrase_fr


def test_sentence_prompt_reuses_cached_canonical_scene():
    from app.services.sentences import SYSTEM_PROMPT, _user_payload

    card = Card(
        id="pt1",
        lemma="casa",
        surface="casa",
        language="pt",
        french="maison",
        phrase_fr="La famille rentre à la maison avant le dîner.",
    )
    payload = _user_payload([card], "portugais", "vocab")

    assert "La famille rentre à la maison avant le dîner." in payload
    assert "scene_fr" in payload
    assert "même situation" in SYSTEM_PROMPT.lower()


def test_fill_missing_applies_scene_after_translation(monkeypatch):
    from app import cache

    cache.remember_scenes([Card(
        id="en1",
        lemma="house",
        surface="house",
        language="en",
        french="maison",
        phrase="The family returns to the house before dinner.",
        phrase_fr="La famille rentre à la maison avant le dîner.",
        category="Nom",
    )])
    monkeypatch.setattr(
        "app.pipeline.generate_glosses",
        lambda cards, *_a, **_k: {
            cards[0].lemma.casefold(): {"french": "maison", "category": "Nom"}
        },
    )

    def fake_sentences(cards, *_args, **_kwargs):
        assert cards[0].phrase_fr == "La famille rentre à la maison avant le dîner."
        return {
            cards[0].lemma.casefold(): {
                "sentence": "A família volta para casa antes do jantar.",
                "translation": cards[0].phrase_fr,
            }
        }

    monkeypatch.setattr("app.pipeline.generate_sentences", fake_sentences)
    monkeypatch.setattr("app.pipeline.generate_ipa", lambda *_a, **_k: {})
    deck = _deck("casa", phrase=True, phonetic=False, image=False)
    deck.cards[0].french = ""

    fill_missing(deck, Settings(default_ai_provider="grok"))

    assert deck.cards[0].phrase == "A família volta para casa antes do jantar."
    assert deck.cards[0].phrase_fr == "La famille rentre à la maison avant le dîner."


def test_fill_missing_limit_only_fills_next_incomplete_cards(monkeypatch):
    seen: list[list[str]] = []

    def fake_glosses(cards, *_args, **_kwargs):
        seen.append([card.lemma for card in cards])
        return {card.lemma.casefold(): {"french": f"fr-{card.lemma}", "category": "Nom"} for card in cards}

    monkeypatch.setattr("app.pipeline.generate_glosses", fake_glosses)
    monkeypatch.setattr("app.pipeline.generate_ipa", lambda cards, *_a, **_k: {c.lemma.casefold(): "/x/" for c in cards})
    monkeypatch.setattr("app.pipeline.generate_sentences", lambda *_a, **_k: {})

    deck = _deck("uno", "due", "tre", phrase=False, phonetic=False, image=False)
    deck.language = "it"
    for card in deck.cards:
        card.language = "it"
    fill_missing(deck, Settings(), limit=2)
    assert seen == [["uno", "due"]]
    assert deck.cards[0].french == "fr-uno"
    assert deck.cards[1].french == "fr-due"
    assert deck.cards[2].french == ""
    assert deck.stages["translate"] == "pending"


def test_fill_missing_records_ai_error_on_cards_and_stage(monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("grok 429 rate limit")

    monkeypatch.setattr("app.pipeline.generate_glosses", boom)
    deck = _deck("xyzzy", phrase=False, phonetic=False, image=False)
    deck.language = "it"
    deck.cards[0].language = "it"
    fill_missing(deck, Settings())
    assert deck.stages["translate"] == "failed"
    assert any("429" in err for err in deck.stage_errors.get("translate") or [])
    assert any("429" in err for err in deck.cards[0].errors)
    assert deck.cards[0].french == ""


def test_sync_stages_unmarks_false_passed_when_fields_empty():
    from app.pipeline import sync_stages

    deck = _deck("e", "non", phrase=True, phonetic=True, image=False)
    deck.stages = {
        "ingest": "passed",
        "normalize": "passed",
        "translate": "passed",
        "phonetic": "passed",
        "sentences": "pending",
    }
    sync_stages(deck)
    assert deck.stages["translate"] == "pending"
    assert deck.stages["phonetic"] == "pending"
    assert deck.stages["sentences"] == "pending"


def test_create_from_strategy_spoken_does_not_fake_translate_passed():
    from app.legacy import create_from_strategy

    deck = create_from_strategy("spoken-it", limit=3, name="IT 3")
    assert len(deck.cards) == 3
    assert all(not card.french for card in deck.cards)
    assert deck.stages["translate"] == "pending"
    assert deck.stages["ingest"] == "passed"


def test_fill_missing_uses_settings_default_ai_not_stale_deck_local(monkeypatch):
    seen: list[str] = []

    def fake_glosses(cards, provider, *_args, **_kwargs):
        seen.append(provider)
        return {cards[0].lemma.casefold(): {"french": "essais", "category": "Nom"}}

    monkeypatch.setattr("app.pipeline.generate_glosses", fake_glosses)
    monkeypatch.setattr(
        "app.pipeline.generate_ipa",
        lambda cards, provider, *_a, **_k: {cards[0].lemma.casefold(): "/ˈtɛsts/"},
    )
    monkeypatch.setattr("app.pipeline.generate_sentences", lambda *_a, **_k: {})

    deck = _deck("tests", phrase=False, phonetic=True, image=False)
    deck.ai_provider = "local"
    fill_missing(deck, Settings(default_ai_provider="grok"))
    assert seen == ["grok"]
    assert deck.cards[0].french == "essais"
    assert deck.cards[0].ipa == "/ˈtɛsts/"


def test_enrich_enables_phrase_when_only_sentences_are_empty(monkeypatch):
    from app.main import post_enrich
    from app.models import CardsEnrich
    from app.store import delete_deck, new_stage_map, now_iso, save_deck
    from app import main as mainmod

    monkeypatch.setattr(mainmod, "load_settings", lambda: Settings(default_ai_provider="local"))
    deck = Deck(
        id="enrich-phrase-test",
        name="NGSL-like",
        language="en",
        language_label="Anglais",
        features=DeckFeatures(phrase=False, phonetic=True, audio=False, image=False),
        cards=[Card(
            id="c1",
            lemma="be",
            surface="be",
            language="en",
            french="être",
            ipa="/bi/",
            phrase="",
            phrase_fr="",
        )],
        created_at=now_iso(),
        updated_at=now_iso(),
    )
    deck.stages = new_stage_map(deck)
    save_deck(deck)
    try:
        out = post_enrich(
            "enrich-phrase-test",
            CardsEnrich(
                ids=[],
                limit=5,
                features=DeckFeatures(phrase=True, phonetic=True, audio=False, image=False),
            ),
        )
        assert out.features.phrase is True
        assert out.cards[0].phrase
        assert out.cards[0].phrase_fr
        assert "be" in out.cards[0].phrase.lower()
    finally:
        delete_deck("enrich-phrase-test")


def test_enrich_applies_audio_without_forcing_phrase(monkeypatch):
    from app.main import post_enrich
    from app.models import CardsEnrich
    from app.store import delete_deck, new_stage_map, now_iso, save_deck
    from app import main as mainmod

    monkeypatch.setattr(mainmod, "load_settings", lambda: Settings(default_ai_provider="local"))
    monkeypatch.setattr(
        "app.pipeline.generate_audio",
        lambda card, deck_id, language: f"/api/media/{deck_id}/{card.lemma}.mp3",
    )
    deck = Deck(
        id="enrich-audio-test",
        name="Audio only",
        language="en",
        language_label="Anglais",
        features=DeckFeatures(phrase=False, phonetic=True, audio=False, image=False),
        cards=[Card(
            id="c1",
            lemma="be",
            surface="be",
            language="en",
            french="être",
            ipa="/bi/",
            phrase="",
            phrase_fr="",
        )],
        created_at=now_iso(),
        updated_at=now_iso(),
    )
    deck.stages = new_stage_map(deck)
    save_deck(deck)
    try:
        out = post_enrich(
            "enrich-audio-test",
            CardsEnrich(
                ids=[],
                limit=5,
                features=DeckFeatures(phrase=False, phonetic=True, audio=True, image=False),
            ),
        )
        assert out.features.audio is True
        assert out.features.phrase is False
        assert out.cards[0].audio_url.endswith("/be.mp3")
        assert out.cards[0].phrase == ""
    finally:
        delete_deck("enrich-audio-test")


def test_patch_deck_updates_features():
    from app.main import patch_deck
    from app.models import DeckPatch
    from app.store import delete_deck, new_stage_map, now_iso, save_deck

    deck = Deck(
        id="patch-features-test",
        name="Flags",
        language="en",
        features=DeckFeatures(phrase=False, phonetic=True, audio=False, image=False),
        cards=[Card(id="c1", lemma="be", surface="be", language="en", french="être")],
        created_at=now_iso(),
        updated_at=now_iso(),
    )
    deck.stages = new_stage_map(deck)
    save_deck(deck)
    try:
        out = patch_deck(
            "patch-features-test",
            DeckPatch(features=DeckFeatures(phrase=True, phonetic=True, audio=True, image=False)),
        )
        assert out.features.audio is True
        assert out.features.phrase is True
        assert out.stages["audio"] in {"pending", "failed"}
        assert out.stages["sentences"] in {"pending", "failed"}
    finally:
        delete_deck("patch-features-test")


def test_fill_missing_runs_sentence_chunks_in_parallel(monkeypatch):
    import threading
    import time

    from app.pipeline import fill_missing

    monkeypatch.setattr("app.pipeline.AI_CHUNK", 2)
    seen: list[list[str]] = []
    lock = threading.Lock()
    active = 0
    peak = 0

    def fake_sentences(cards, *_args, **_kwargs):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.12)
        with lock:
            active -= 1
            seen.append([card.lemma for card in cards])
        return {
            card.lemma.casefold(): {
                "sentence": f"I need this {card.lemma} right now.",
                "translation": f"fr {card.lemma}",
            }
            for card in cards
        }

    monkeypatch.setattr("app.pipeline.generate_sentences", fake_sentences)
    words = ["one", "two", "three", "four", "five", "six", "seven", "eight"]
    deck = _deck(*words, phrase=True, phonetic=False, image=False)
    deck.language = "en"
    for card in deck.cards:
        card.language = "en"
        card.french = f"fr-{card.lemma}"
    started = time.perf_counter()
    fill_missing(deck, Settings(default_ai_provider="local"), concurrency=4)
    elapsed = time.perf_counter() - started
    assert peak >= 3
    assert elapsed < 0.4
    assert all(card.phrase for card in deck.cards)
    assert sum(len(batch) for batch in seen) == 8


def test_fill_missing_generates_audio_for_whole_batch(monkeypatch):
    from app.pipeline import fill_missing

    monkeypatch.setattr(
        "app.pipeline.generate_audio",
        lambda card, deck_id, language: f"/api/media/{deck_id}/{card.lemma}.mp3",
    )
    deck = _deck("alpha", "beta", "gamma", "delta", phrase=False, phonetic=False, image=False)
    deck.features.audio = True
    for card in deck.cards:
        card.french = f"fr-{card.lemma}"
    fill_missing(deck, Settings(), concurrency=4)
    assert all(card.audio_url.endswith(f"{card.lemma}.mp3") for card in deck.cards)


def test_fill_missing_reports_progress_after_each_wave(monkeypatch):
    from app.pipeline import fill_missing

    monkeypatch.setattr("app.pipeline.AI_CHUNK", 2)
    ticks: list[int] = []

    def fake_sentences(cards, *_args, **_kwargs):
        return {
            card.lemma.casefold(): {
                "sentence": f"I need this {card.lemma} right now.",
                "translation": f"fr {card.lemma}",
            }
            for card in cards
        }

    monkeypatch.setattr("app.pipeline.generate_sentences", fake_sentences)
    deck = _deck("one", "two", "three", "four", "five", "six", "seven", "eight", phrase=True, phonetic=False, image=False)
    deck.language = "en"
    for card in deck.cards:
        card.language = "en"
        card.french = f"fr-{card.lemma}"

    def on_progress(current):
        ticks.append(sum(1 for card in current.cards if card.phrase))

    fill_missing(deck, Settings(), concurrency=2, on_progress=on_progress)
    assert ticks
    assert ticks[-1] == 8
    assert len(ticks) >= 2
    assert ticks[0] < ticks[-1]


def test_fill_missing_drops_stale_pipeline_errors_after_qa():
    deck = _deck("casa", phrase=True, phonetic=True, image=False)
    card = deck.cards[0]
    card.french = "maison"
    card.ipa = "/ˈkazɐ/"
    card.phrase = "A casa é grande e bonita hoje."
    card.phrase_fr = "La maison est grande et belle aujourd'hui."
    card.errors = ["_sentences() takes 2 positional arguments but 3 were given"]
    deck.stage_errors = {"sentences": [card.errors[0]]}
    fill_missing(deck, Settings())
    assert card.errors == []
    assert "takes 2" not in " ".join(deck.stage_errors.get("sentences") or [])


def test_fill_missing_does_not_overwrite_invalid_phrase_without_retry():
    deck = _deck("casa", phrase=True, phonetic=False, image=False)
    card = deck.cards[0]
    card.french = "maison"
    card.phrase = "O gato dorme no sofá agora."
    card.phrase_fr = "Le chat dort sur le canapé."
    card.errors = ["casa: la phrase doit contenir le mot cible"]
    fill_missing(deck, Settings())
    assert card.phrase == "O gato dorme no sofá agora."


def test_fill_missing_retry_regenerates_invalid_phrase(monkeypatch):
    monkeypatch.setattr(
        "app.pipeline.generate_sentences",
        lambda cards, *_a, **_k: {
            cards[0].lemma.casefold(): {
                "sentence": "Eu moro nesta casa agora mesmo.",
                "translation": "J'habite dans cette maison maintenant.",
            }
        },
    )
    deck = _deck("casa", phrase=True, phonetic=False, image=False)
    card = deck.cards[0]
    card.french = "maison"
    card.phrase = "O gato dorme no sofá agora."
    card.phrase_fr = "Le chat dort sur le canapé."
    card.errors = ["casa: la phrase doit contenir le mot cible"]
    fill_missing(deck, Settings(), retry=True)
    assert "casa" in card.phrase.lower()
    assert not any("mot cible" in err for err in card.errors)


def test_enrich_retry_targets_error_cards_when_ids_empty(monkeypatch):
    from app.main import post_enrich
    from app.models import CardsEnrich
    from app.store import delete_deck, new_stage_map, now_iso, save_deck
    from app import main as mainmod

    monkeypatch.setattr(mainmod, "load_settings", lambda: Settings(default_ai_provider="local"))
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
        id="enrich-retry-test",
        name="Retry",
        language="en",
        language_label="Anglais",
        features=DeckFeatures(phrase=True, phonetic=False, audio=False, image=False),
        cards=[
            Card(
                id="ok",
                lemma="house",
                surface="house",
                language="en",
                french="maison",
                phrase="I need this house right now.",
                phrase_fr="J'ai besoin de cette maison maintenant.",
            ),
            Card(
                id="bad",
                lemma="window",
                surface="window",
                language="en",
                french="fenêtre",
                phrase="The cat sleeps on the sofa now.",
                phrase_fr="Le chat dort sur le canapé.",
                errors=["window: la phrase doit contenir le mot cible"],
            ),
        ],
        created_at=now_iso(),
        updated_at=now_iso(),
    )
    deck.stages = new_stage_map(deck)
    save_deck(deck)
    try:
        out = post_enrich(
            "enrich-retry-test",
            CardsEnrich(ids=[], limit=10, retry=True),
        )
        by_id = {card.id: card for card in out.cards}
        assert by_id["ok"].phrase == "I need this house right now."
        assert "window" in by_id["bad"].phrase.lower()
        assert by_id["bad"].errors == []
    finally:
        delete_deck("enrich-retry-test")


def test_fill_missing_mass_images_do_not_call_ai_generate(monkeypatch):
    seen: list[bool] = []

    def fake_fetch(_card, _bank, _settings, deck_id="", allow_generate=False, force_generate=False, target_language="fr", generation_provider=""):
        seen.append(allow_generate)
        return "", ""

    monkeypatch.setattr("app.pipeline.fetch_image", fake_fetch)
    words = tuple(f"w{i}" for i in range(12))
    deck = _deck(*words, phrase=False, phonetic=False, image=True)
    for card in deck.cards:
        card.french = f"fr-{card.lemma}"
        card.category = "Nom"
    fill_missing(deck, Settings())
    assert seen
    assert all(flag is False for flag in seen)


def test_fill_missing_small_image_batch_may_generate(monkeypatch):
    seen: list[bool] = []

    def fake_fetch(_card, _bank, _settings, deck_id="", allow_generate=False, force_generate=False, target_language="fr", generation_provider=""):
        seen.append(allow_generate)
        return "/api/media/d1/x.jpg", "generated"

    monkeypatch.setattr("app.pipeline.fetch_image", fake_fetch)
    deck = _deck("half", "few", "more", phrase=False, phonetic=False, image=True)
    for card in deck.cards:
        card.french = f"fr-{card.lemma}"
        card.category = "Nom"
    fill_missing(deck, Settings(default_ai_provider="grok", xai_api_key="xai-test"), generate_images=True)
    assert seen
    assert all(flag is True for flag in seen)


def test_fill_missing_explains_when_ai_cannot_generate_images(monkeypatch):
    monkeypatch.setattr("app.pipeline.fetch_image", lambda *_a, **_k: ("", ""))
    deck = _deck("half", phrase=False, phonetic=False, image=True)
    deck.cards[0].french = "moitié"
    deck.cards[0].category = "Nom"
    fill_missing(deck, Settings(default_ai_provider="claude"), generate_images=True)
    notes = " ".join(deck.stage_errors.get("images") or [])
    assert notes
    assert "Grok" in notes or "image" in notes.lower()


def test_fill_missing_replace_images_overwrites_wikimedia(monkeypatch):
    seen: list[tuple[str, bool]] = []

    def fake_fetch(card, _bank, _settings, deck_id="", allow_generate=False, force_generate=False, target_language="fr", generation_provider=""):
        seen.append((card.lemma, force_generate))
        return f"/api/media/d/{card.lemma}.jpg", "generated"

    monkeypatch.setattr("app.pipeline.fetch_image", fake_fetch)
    deck = _deck("house", "she", phrase=False, phonetic=False, image=True)
    deck.cards[0].french = "maison"
    deck.cards[0].category = "Nom"
    deck.cards[0].image_url = "https://example.com/ranch-house.jpg"
    deck.cards[0].image_source = "wikimedia"
    deck.cards[1].french = "elle"
    deck.cards[1].category = "Grammaire"
    deck.cards[1].skip_image = True
    fill_missing(
        deck,
        Settings(default_ai_provider="grok", xai_api_key="xai-test"),
        generate_images=True,
        replace_images=True,
    )
    by = {card.lemma: card for card in deck.cards}
    assert by["house"].image_source == "generated"
    assert by["house"].image_url == "/api/media/d/house.jpg"
    assert by["she"].image_url == ""
    assert seen == [("house", True)]


def test_image_provider_is_independent_from_text_provider():
    from app.pipeline import _image_provider
    deck = _deck("house", phrase=False, phonetic=False, image=True)
    for bank, text, expected in [("codex", "grok", "codex"), ("openai", "claude", "codex"),
                                 ("grok", "codex", "grok"), ("wikimedia", "codex", "codex")]:
        deck.image_bank = bank
        assert _image_provider(deck, Settings(default_ai_provider=text)) == expected


def test_pipeline_forwards_codex_image_provider(monkeypatch):
    from app.pipeline import _images
    observed = []
    def fetch(c, bank, settings, deck_id, **kwargs):
        observed.append((bank, kwargs))
        return "/api/media/d/codex.png", "openai"
    monkeypatch.setattr("app.pipeline.fetch_image", fetch)
    deck = _deck("house", phrase=False, phonetic=False, image=True)
    deck.image_bank = "codex"
    deck.cards[0].category = "Nom"
    _images(deck, Settings(default_ai_provider="grok"), allow_generate=True, replace_images=True)
    assert observed[0][0] == "codex"
    assert observed[0][1]["generation_provider"] == "codex"
    assert observed[0][1]["force_generate"] is True
    assert deck.cards[0].image_source == "openai"


def test_audio_quota_failure_is_preserved_in_stage_errors(monkeypatch):
    def unavailable(*args, **kwargs):
        raise RuntimeError("TTS quota exhausted; retry later")
    monkeypatch.setattr("app.pipeline.generate_audio", unavailable)
    deck = _deck("house", phrase=False, phonetic=False, image=False)
    deck.features.audio = True
    deck.cards[0].french = "maison"
    fill_missing(deck, Settings(default_ai_provider="local"))
    assert not deck.cards[0].audio_url
    assert "TTS quota exhausted" in " ".join(deck.stage_errors["audio"])
    assert deck.stages["audio"] == "failed"


def test_complete_reports_audio_failure_cause(monkeypatch):
    from app.complete import complete_deck
    def unavailable(*args, **kwargs):
        raise RuntimeError("TTS quota exhausted; retry later")
    monkeypatch.setattr("app.pipeline.generate_audio", unavailable)
    deck = _deck("house", phrase=False, phonetic=False, image=False)
    deck.features.audio = True
    deck.cards[0].french = "maison"
    result = complete_deck(deck, Settings(default_ai_provider="local"), persist=False, max_batches=2)
    assert not result.ok and "TTS quota exhausted" in result.reason
