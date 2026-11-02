import pytest

from app.models import Card, Deck
from app.lexicon import lookup_word
from app.protocol import (
    STAGES,
    ProtocolError,
    apply_normalize,
    can_run,
    is_function_word,
    qa_deck,
    validate_card_image,
    validate_card_sentence,
    validate_normalize,
)


def _card(**kwargs) -> Card:
    data = {
        "id": "c1",
        "lemma": "casa",
        "surface": "casa",
        "language": "pt",
        "french": "maison",
        "category": "Nom",
        "ipa": "/ˈkazɐ/",
        "phrase": "Minha casa fica perto do parque.",
        "phrase_fr": "Ma maison est près du parc.",
        "image_url": "https://example.com/casa.jpg",
        "image_source": "wikimedia",
        "skip_image": False,
        "flags": [],
        "errors": [],
    }
    data.update(kwargs)
    return Card(**data)


def _deck(cards=None, stages=None) -> Deck:
    return Deck(
        id="d1",
        name="Test",
        language="pt",
        image_bank="wikimedia",
        ai_provider="anthropic",
        cards=cards or [_card()],
        stages=stages or {name: "pending" for name in STAGES},
    )


def test_cannot_run_sentences_before_translate():
    deck = _deck(stages={
        "ingest": "passed",
        "normalize": "passed",
        "translate": "pending",
        "phonetic": "pending",
        "sentences": "pending",
        "images": "pending",
        "qa": "pending",
        "export": "pending",
    })
    allowed, reason = can_run(deck, "sentences")
    assert allowed is False
    assert "translate" in reason


def test_can_run_next_stage_when_previous_passed():
    deck = _deck(stages={
        "ingest": "passed",
        "normalize": "passed",
        "translate": "pending",
        "phonetic": "pending",
        "sentences": "pending",
        "images": "pending",
        "qa": "pending",
        "export": "pending",
    })
    allowed, reason = can_run(deck, "translate")
    assert allowed is True
    assert reason == ""


def test_sentence_must_contain_target_word():
    errors = validate_card_sentence(_card(
        lemma="casa",
        phrase="O apartamento fica longe.",
        phrase_fr="L'appartement est loin.",
    ))
    assert any("mot cible" in e.lower() or "cible" in e.lower() for e in errors)


def test_sentence_too_short_fails():
    errors = validate_card_sentence(_card(
        phrase="Minha casa.",
        phrase_fr="Ma maison.",
    ))
    assert any("courte" in e.lower() or "mots" in e.lower() for e in errors)


def test_valid_sentence_passes():
    errors = validate_card_sentence(_card())
    assert errors == []


def test_grammar_word_must_not_have_image():
    errors = validate_card_image(_card(
        lemma="de",
        surface="de",
        french="de",
        category="Grammaire",
        skip_image=True,
        image_url="https://example.com/de.jpg",
        phrase="Eu venho de São Paulo.",
        phrase_fr="Je viens de São Paulo.",
    ))
    assert any("image" in e.lower() for e in errors)


def test_english_pronouns_and_auxiliaries_are_function_words():
    for word in ("be", "i", "she", "he", "they", "we", "you", "will", "can", "have", "do"):
        assert is_function_word(word, "en"), word
    assert not is_function_word("house", "en")
    assert not is_function_word("table", "en")
    assert not is_function_word("half", "en")
    assert not is_function_word("few", "en")


def test_apply_normalize_strips_image_from_abstract_word():
    card = _card(
        lemma="she",
        surface="she",
        language="en",
        category="Nom",
        skip_image=False,
        image_url="https://example.com/she.jpg",
    )
    apply_normalize(card)
    assert card.skip_image is True
    assert card.category == "Grammaire"
    assert card.image_url == ""


def test_apply_normalize_reopens_imageable_word_once_tagged_as_function():
    card = _card(
        lemma="half",
        surface="half",
        language="en",
        category="Grammaire",
        skip_image=True,
        image_url="",
    )
    card.flags = ["function"]
    apply_normalize(card)
    assert card.skip_image is False
    assert card.category == "Nom"
    assert "function" not in card.flags


def test_missing_image_is_warning_not_qa_blocker():
    deck = _deck(cards=[_card(image_url="")])
    report = qa_deck(deck)
    assert report.ok is True
    assert deck.cards[0].errors == []
    assert any("image" in item.lower() for item in report.warnings)


def test_grammar_word_with_image_still_blocks_qa():
    deck = _deck(cards=[_card(
        lemma="de",
        surface="de",
        french="de",
        category="Grammaire",
        skip_image=True,
        image_url="https://example.com/de.jpg",
        phrase="Eu venho de São Paulo agora mesmo.",
        phrase_fr="Je viens de São Paulo maintenant.",
    )])
    report = qa_deck(deck)
    assert report.ok is False
    assert any("image" in err.lower() for err in report.errors)


def test_duplicate_lemmas_fail_normalize():
    cards = [
        _card(id="a", lemma="casa"),
        _card(id="b", lemma="Casa"),
    ]
    errors = validate_normalize(cards)
    assert any("doublon" in e.lower() for e in errors)


def test_qa_blocks_export_when_card_has_errors():
    deck = _deck(cards=[_card(phrase="O apartamento fica longe.")])
    result = qa_deck(deck)
    assert result.ok is False
    assert result.failed > 0


def test_lookup_is_accent_insensitive():
    row = lookup_word("pt", "cafe")
    assert row is not None
    assert "caf" in row["Portuguese"].casefold()


def test_qa_skips_images_when_feature_off():
    from app.models import DeckFeatures
    deck = _deck(
        cards=[_card(image_url="")],
    )
    deck.features = DeckFeatures(phrase=True, phonetic=True, audio=False, image=False)
    result = qa_deck(deck)
    assert result.ok is True


def test_disabled_phonetic_does_not_block_sentences():
    from app.models import DeckFeatures
    deck = _deck(stages={
        "ingest": "passed",
        "normalize": "passed",
        "translate": "passed",
        "phonetic": "pending",
        "sentences": "pending",
        "images": "pending",
        "qa": "pending",
        "export": "pending",
    })
    deck.features = DeckFeatures(phrase=True, phonetic=False, audio=False, image=True)
    allowed, reason = can_run(deck, "sentences")
    assert allowed is True
    assert reason == ""


def test_lookup_unknown_language_does_not_crash():
    assert lookup_word("ja", "犬") is None


def test_qa_passes_clean_deck():
    deck = _deck()
    result = qa_deck(deck)
    assert result.ok is True
    assert result.failed == 0


def test_qa_ignores_residual_invalid_sentence_when_phrase_feature_disabled():
    from app.models import DeckFeatures
    deck = _deck(cards=[_card(phrase="x", phrase_fr="")])
    deck.features = DeckFeatures(phrase=False, phonetic=True, audio=False, image=False)
    assert qa_deck(deck).ok
    deck.features.phrase = True
    assert not qa_deck(deck).ok


def test_elided_target_is_recognized_with_straight_and_curly_apostrophes():
    from app.protocol import contains_lemma
    assert contains_lemma("L'amico arriva domani mattina presto.", "l'")
    assert contains_lemma("L’amico arriva domani mattina presto.", "l'")
    assert contains_lemma("C'è una bella casa vicino.", "c’")
    assert not contains_lemma("L'amico arriva domani mattina presto.", "amici")
    assert not contains_lemma("The table is fifty centimeters long.", "centimeter")


def test_normalize_enforces_reviewed_grammar_category_outside_word_list():
    c = _card(lemma="como", surface="como", language="es", category="Grammaire", skip_image=False,
              image_url="/api/media/d/como.png", image_source="openai", image_asset_id="asset",
              image_provenance={"origin": "test"})
    before_audio = c.audio_url
    apply_normalize(c)
    assert c.skip_image and not c.image_url and not c.image_source
    assert not c.image_asset_id and not c.image_provenance
    assert c.audio_url == before_audio


def test_normalize_enforces_explicit_skip_image_for_non_grammar_word():
    c = _card(lemma="explicit", language="en", category="Adjectif", skip_image=True,
              image_url="/api/media/d/explicit.png")
    apply_normalize(c)
    assert not c.image_url and c.category == "Adjectif"


def _reviewed_example(card, checks, kind="vocab", **extra):
    from app.protocol import example_context, example_context_sha256
    card.validated_example = {"schema_version": 1, **example_context(card, kind),
                              "context_sha256": example_context_sha256(card, kind),
                              "checks": checks, "reason": "Independent review of this exact example",
                              "reviewed_by": "independent_reviewer", "review_sha256": "a" * 64, **extra}


def test_reviewed_short_example_passes_with_visible_qa_note():
    c = _card(lemma="bubble", surface="bubble", language="en", phrase="A bubble burst.", phrase_fr="Une bulle a éclaté.")
    assert validate_card_sentence(c)
    _reviewed_example(c, ["short_sentence"])
    assert not validate_card_sentence(c)
    q = qa_deck(_deck(cards=[c]))
    assert q.ok and any("exception explicite" in x for x in q.warnings)


@pytest.mark.parametrize("field,value", [("lemma", "bubbles"), ("surface", "bubbles"), ("language", "pt"),
                                        ("french", "autre sens"), ("phrase", "A bubble bursts."),
                                        ("phrase_fr", "Une bulle éclate.")])
def test_reviewed_example_does_not_survive_any_bound_text_change(field, value):
    from app.protocol import reviewed_example_checks
    c = _card(lemma="bubble", surface="bubble", language="en", phrase="A bubble burst.")
    _reviewed_example(c, ["short_sentence"])
    setattr(c, field, value)
    assert not reviewed_example_checks(c)
    assert validate_card_sentence(c)


def test_reviewed_inflection_requires_attested_form_and_keeps_other_checks():
    c = _card(lemma="centimeter", surface="centimeter", language="en", phrase="The table is fifty centimeters long and wide.")
    _reviewed_example(c, ["inflection"], inflected_form="centimeters")
    assert not validate_card_sentence(c)
    c.validated_example["inflected_form"] = "meters"
    assert validate_card_sentence(c)
    c.phrase = "Fifty centimeters."
    _reviewed_example(c, ["inflection"], inflected_form="centimeters")
    assert any("courte" in e for e in validate_card_sentence(c))


@pytest.mark.parametrize("change", [{"checks": ["long_sentence"]}, {"review_sha256": ""},
                                   {"reason": ""}, {"reviewed_by": ""}, {"context_sha256": "b" * 64}])
def test_invalid_review_proof_never_bypasses_sentence_rules(change):
    c = _card(lemma="bubble", surface="bubble", language="en", phrase="A bubble burst.")
    _reviewed_example(c, ["short_sentence"])
    c.validated_example.update(change)
    assert validate_card_sentence(c)


def test_review_exception_never_permits_missing_translation_or_wrong_kind():
    c = _card(lemma="bubble", surface="bubble", language="en", phrase="A bubble burst.", phrase_fr="")
    _reviewed_example(c, ["short_sentence"])
    assert any("traduction" in e for e in validate_card_sentence(c))
    c.phrase_fr = "Une bulle a éclaté."
    _reviewed_example(c, ["short_sentence"], kind="grammar")
    assert any("courte" in e for e in validate_card_sentence(c, "vocab"))
