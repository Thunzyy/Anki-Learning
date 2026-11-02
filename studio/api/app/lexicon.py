from __future__ import annotations

import csv
from functools import lru_cache

from .paths import IPA_FIELD, SOURCE_CSV, WORD_FIELD
from .protocol import fold, fold_loose


@lru_cache(maxsize=8)
def load_source_rows(language: str) -> list[dict[str, str]]:
    path = SOURCE_CSV.get(language)
    if not path or not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def lookup_word(language: str, word: str) -> dict[str, str] | None:
    if language not in WORD_FIELD:
        return None
    field = WORD_FIELD[language]
    needle = fold(word)
    loose = fold_loose(word)
    loose_hit = None
    for row in load_source_rows(language):
        surface = row.get(field, "")
        if fold(surface) == needle:
            return row
        if loose_hit is None and fold_loose(surface) == loose:
            loose_hit = row
    return loose_hit


def row_to_fields(language: str, row: dict[str, str]) -> dict[str, str]:
    word_field = WORD_FIELD[language]
    ipa_field = IPA_FIELD[language]
    phrase = row.get("Phrase") or row.get("Example_PT") or row.get("Example_ES") or row.get("Example_EN") or ""
    phrase_fr = row.get("Traduction") or row.get("Example_FR") or ""
    return {
        "surface": row.get(word_field, ""),
        "lemma": row.get(word_field, ""),
        "french": row.get("French", ""),
        "category": row.get("Category") or ("Nom" if language != "en" else ""),
        "ipa": row.get(ipa_field, ""),
        "phrase": phrase,
        "phrase_fr": phrase_fr,
        "rank": row.get("Rank", ""),
    }
