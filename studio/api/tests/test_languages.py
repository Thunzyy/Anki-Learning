from app.languages import find_language


def test_find_language_matches_codes_and_labels():
    assert find_language("fr")["id"] == "fr"
    assert find_language("Français")["id"] == "fr"
    assert find_language("PORTUGAIS (BRÉSIL)")["id"] == "pt"


def test_find_language_ignores_missing_accents():
    """A label typed without accents must not fork a deck onto its own language slug."""
    assert find_language("francais")["id"] == "fr"
    assert find_language("Portugais (Bresil)")["id"] == "pt"
    assert find_language("Coreen")["id"] == "ko"


def test_find_language_keeps_unknown_words_as_custom_slugs():
    assert find_language("klingon")["id"] == "klingon"
    assert find_language("")["id"] == "und"
