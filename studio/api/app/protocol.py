from __future__ import annotations

import re
import unicodedata
import hashlib
import json

from .models import Card, Deck, QaReport, StageName


STAGES: list[StageName] = [
    "ingest",
    "normalize",
    "translate",
    "phonetic",
    "sentences",
    "images",
    "audio",
    "qa",
    "export",
]

FUNCTION_WORDS = {
    "pt": {
        "a", "ao", "aos", "as", "com", "da", "das", "de", "do", "dos", "e",
        "em", "na", "nas", "no", "nos", "o", "os", "ou", "para", "por", "que",
        "se", "um", "uma", "mas", "não", "nao",
        "eu", "tu", "você", "voce", "ele", "ela", "nós", "nos", "eles", "elas",
        "me", "mim", "te", "ti", "lhe", "vos", "si", "meu", "minha", "teu",
        "tua", "seu", "sua", "nosso", "nossa",
        "ser", "sou", "é", "somos", "são", "era", "foi", "estar", "estou",
        "está", "estamos", "estão", "ter", "tenho", "tem", "temos", "têm",
        "haver", "ir", "vou", "vai", "vamos", "vão", "poder", "pode", "devo",
        "deve", "dever", "querer", "quero", "quer",
    },
    "es": {
        "a", "al", "con", "de", "del", "el", "en", "la", "las", "los", "o",
        "para", "por", "que", "se", "un", "una", "y", "pero", "no",
        "yo", "tú", "tu", "él", "ella", "usted", "nosotros", "vosotros",
        "ellos", "ellas", "me", "te", "le", "nos", "os", "les", "mi", "su",
        "ser", "soy", "es", "somos", "son", "era", "fue", "estar", "estoy",
        "está", "haber", "he", "ha", "hay", "tener", "tengo", "tiene",
        "ir", "voy", "va", "poder", "puede", "deber", "debe",
    },
    "en": {
        "a", "an", "and", "at", "by", "for", "from", "in", "of", "on", "or",
        "the", "to", "with", "but", "not",
        "i", "me", "my", "mine", "myself", "you", "your", "yours", "yourself",
        "he", "him", "his", "himself", "she", "her", "hers", "herself",
        "it", "its", "itself", "we", "us", "our", "ours", "ourselves",
        "they", "them", "their", "theirs", "themselves",
        "this", "that", "these", "those", "who", "whom", "whose", "which", "what",
        "be", "am", "is", "are", "was", "were", "been", "being",
        "have", "has", "had", "having", "do", "does", "did", "doing", "done",
        "will", "would", "shall", "should", "can", "could", "may", "might",
        "must", "ought",
        "if", "than", "so", "as", "then", "there", "here", "when", "where",
        "why", "how", "very", "too", "also", "just", "only", "even", "still",
        "yet", "never", "always", "much", "many",
        "some", "any", "no", "nor", "both", "each", "every", "all", "such",
        "own", "same", "other", "another",
        "none", "enough",
        "quite", "rather", "almost", "already", "often", "sometimes",
        "perhaps", "maybe", "however", "therefore", "although", "because",
        "since", "until", "while", "during", "before", "after", "about",
        "against", "among", "between", "through", "under", "over", "into",
        "onto", "upon",
    },
    "it": {
        "a", "ad", "al", "che", "chi", "ci", "con", "da", "dal", "di", "e", "ed",
        "il", "lo", "la", "i", "gli", "le", "in", "nel", "non", "o", "per",
        "se", "si", "su", "un", "uno", "una", "ma", "come",
        "io", "tu", "lui", "lei", "noi", "voi", "loro", "mi", "ti", "vi",
        "li", "mio", "tuo", "suo",
        "essere", "sono", "è", "siamo", "siete", "era", "stato",
        "avere", "ho", "ha", "abbiamo", "stare", "sto", "sta",
        "potere", "può", "dovere", "deve", "volere",
    },
}


class ProtocolError(Exception):
    pass


def fold(text: str) -> str:
    return unicodedata.normalize("NFC", (text or "").strip()).casefold()


def fold_loose(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", (text or "").strip())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()


def word_count(text: str) -> int:
    return len(re.findall(r"\w+(?:['’-]\w+)?", text or "", flags=re.UNICODE))


def contains_lemma(phrase: str, lemma: str) -> bool:
    if not phrase or not lemma:
        return False
    # Elided articles/pronouns (Italian l', c') attach to the next word.
    # Normalize typographic apostrophes but retain exact accented letters.
    target = unicodedata.normalize("NFC", lemma.strip()).replace("’", "'")
    text = unicodedata.normalize("NFC", phrase).replace("’", "'")
    right_boundary = "" if target.endswith("'") else r"(?!\w)"
    pattern = r"(?<!\w)" + re.escape(target) + right_boundary
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


def is_function_word(lemma: str, language: str) -> bool:
    return fold(lemma) in FUNCTION_WORDS.get(language, set())


def feature_enabled(deck: Deck, stage: str) -> bool:
    features = getattr(deck, "features", None)
    if features is None:
        return True
    mapping = {
        "phonetic": features.phonetic,
        "sentences": features.phrase,
        "images": features.image,
        "audio": features.audio,
    }
    return mapping.get(stage, True)


def can_run(deck: Deck, stage: str) -> tuple[bool, str]:
    if stage not in STAGES:
        return False, f"étape inconnue: {stage}"
    if not feature_enabled(deck, stage):
        return False, f"l'étape « {stage} » est désactivée pour ce deck"
    index = STAGES.index(stage)  # type: ignore[arg-type]
    if index == 0:
        return True, ""
    for previous in STAGES[:index]:
        if not feature_enabled(deck, previous):
            continue
        status = deck.stages.get(previous, "pending")
        if status not in {"passed", "skipped"}:
            return False, f"l'étape « {previous} » doit être validée avant « {stage} »"
    return True, ""


def validate_normalize(cards: list[Card]) -> list[str]:
    errors: list[str] = []
    seen: dict[str, str] = {}
    for card in cards:
        lemma = fold(card.lemma)
        if not lemma:
            errors.append(f"{card.id}: lemme vide")
            continue
        if lemma in seen:
            errors.append(f"doublon de lemme: {card.lemma} ({seen[lemma]} / {card.id})")
        else:
            seen[lemma] = card.id
    return errors


def example_context(card: Card, kind: str = "vocab") -> dict:
    """Exact text identity bound by an independently reviewed exception."""
    return {**{name: getattr(card, name) for name in
               ("language", "lemma", "surface", "french", "phrase", "phrase_fr")}, "kind": kind}


def example_context_sha256(card: Card, kind: str = "vocab") -> str:
    return hashlib.sha256(json.dumps(example_context(card, kind), ensure_ascii=False,
                                    sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def reviewed_example_checks(card: Card, kind: str = "vocab") -> set[str]:
    proof = card.validated_example
    if not isinstance(proof, dict) or proof.get("schema_version") != 1:
        return set()
    checks = proof.get("checks")
    if not isinstance(checks, list) or not checks or any(
        not isinstance(check, str) or check not in {"short_sentence", "inflection"} for check in checks
    ):
        return set()
    if any(proof.get(key) != value for key, value in example_context(card, kind).items()):
        return set()
    if proof.get("context_sha256") != example_context_sha256(card, kind):
        return set()
    if any(not isinstance(proof.get(key), str) or not proof[key].strip()
           for key in ("reason", "reviewed_by")):
        return set()
    if not isinstance(proof.get("review_sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", proof["review_sha256"]):
        return set()
    result = set(checks)
    if "inflection" in result:
        form = proof.get("inflected_form")
        if not isinstance(form, str) or not form.strip() or not contains_lemma(card.phrase, form):
            result.discard("inflection")
    # An exception never permits an empty example or missing translation.
    if word_count(card.phrase) < 2 or not card.phrase_fr.strip():
        return set()
    return result


def validate_card_sentence(card: Card, kind: str = "vocab") -> list[str]:
    errors: list[str] = []
    phrase = (card.phrase or "").strip()
    translation = (card.phrase_fr or "").strip()
    if not phrase:
        errors.append(f"{card.lemma}: phrase manquante")
        return errors
    count = word_count(phrase)
    reviewed = reviewed_example_checks(card, kind)
    if kind == "grammar":
        if count < 3 and "short_sentence" not in reviewed:
            errors.append(f"{card.lemma}: phrase trop courte ({count} mots, minimum 3)")
        if fold(phrase) == fold(card.lemma):
            errors.append(f"{card.lemma}: la phrase ne doit pas être le titre de la règle")
    else:
        if not contains_lemma(phrase, card.lemma) and not contains_lemma(phrase, card.surface) and "inflection" not in reviewed:
            errors.append(f"{card.lemma}: la phrase doit contenir le mot cible")
        if count < 5 and "short_sentence" not in reviewed:
            errors.append(f"{card.lemma}: phrase trop courte ({count} mots, minimum 5)")
        if count > 14:
            errors.append(f"{card.lemma}: phrase trop longue ({count} mots, maximum 14)")
    if not translation:
        errors.append(f"{card.lemma}: traduction complète de la phrase manquante")
    return errors


def validate_card_image(card: Card) -> list[str]:
    errors: list[str] = []
    has_image = bool((card.image_url or "").strip())
    grammar = card.category.casefold() == "grammaire" or is_function_word(card.lemma, card.language)
    if grammar or card.skip_image:
        if has_image:
            errors.append(f"{card.lemma}: mot grammatical, aucune image autorisée")
    return errors


def image_coverage_warning(card: Card) -> str:
    has_image = bool((card.image_url or "").strip())
    grammar = card.category.casefold() == "grammaire" or is_function_word(card.lemma, card.language)
    if grammar or card.skip_image or has_image:
        return ""
    if card.category.casefold() == "nom":
        return f"{card.lemma}: nom concret, une image est obligatoire"
    return ""


def validate_card_translate(card: Card) -> list[str]:
    if not (card.french or "").strip():
        return [f"{card.lemma}: traduction manquante"]
    return []


def validate_card_phonetic(card: Card) -> list[str]:
    if not (card.ipa or "").strip():
        return [f"{card.lemma}: phonétique manquante"]
    return []


def qa_deck(deck: Deck) -> QaReport:
    errors: list[str] = []
    warnings: list[str] = []
    failed = 0
    for card in deck.cards:
        card_errors = validate_card_translate(card)
        if feature_enabled(deck, "phonetic"):
            card_errors.extend(validate_card_phonetic(card))
        if feature_enabled(deck, "sentences"):
            card_errors.extend(validate_card_sentence(card, getattr(deck, "kind", "vocab")))
            reviewed = reviewed_example_checks(card, getattr(deck, "kind", "vocab"))
            if reviewed:
                warnings.append(f"{card.lemma}: exemple relu, exception explicite ({', '.join(sorted(reviewed))})")
        if feature_enabled(deck, "images"):
            card_errors.extend(validate_card_image(card))
            note = image_coverage_warning(card)
            if note:
                warnings.append(note)
        if feature_enabled(deck, "audio") and not (card.audio_url or "").strip():
            card_errors.append(f"{card.lemma}: audio manquant")
        card.errors = card_errors
        if card_errors:
            failed += 1
            errors.extend(card_errors)
        if "Eu gosto de" in (card.phrase or "") or "Me gusta" in (card.phrase or ""):
            warnings.append(f"{card.lemma}: phrase générique, à relire")
    return QaReport(
        ok=failed == 0 and len(deck.cards) > 0,
        checked=len(deck.cards),
        failed=failed,
        errors=errors,
        warnings=warnings,
    )


def apply_normalize(card: Card) -> Card:
    card.lemma = card.lemma.strip()
    card.surface = (card.surface or card.lemma).strip()
    if is_function_word(card.lemma, card.language):
        card.skip_image = True
        # Auxiliary/modal verbs remain verbs even when illustration policy
        # treats them as function words. Do not overwrite a reviewed POS on load.
        if fold(card.category) != "verbe":
            card.category = "Grammaire"
        card.image_url = ""
        card.image_source = ""
        if "function" not in card.flags:
            card.flags.append("function")
    else:
        if "function" in card.flags:
            card.flags = [flag for flag in card.flags if flag != "function"]
            card.skip_image = False
            if card.category.casefold() == "grammaire":
                card.category = "Nom"
        elif not card.category:
            card.category = "Nom"
    # Match the existing image policy even for reviewed grammar categories
    # not present in the finite function-word lexicon (e.g. Spanish como).
    if fold(card.category) == "grammaire" or card.skip_image:
        card.skip_image = True
        card.image_url = ""
        card.image_source = ""
        card.image_asset_id = ""
        card.image_provenance = {}
    return card
