from __future__ import annotations

import base64
import hashlib
import re
import time
from threading import RLock
from urllib.parse import quote, unquote

import httpx

from ..auth.sessions import resolve_for_provider
from ..languages import find_language
from ..models import Card, Settings
from ..paths import MEDIA_DIR, ensure_dirs
from ..protocol import fold
from .audio import safe_name

USER_AGENT = "Learning/1.0 (https://github.com/Thunzyy/Anki-Learning; language-learning deck factory)"
HEADERS = {"User-Agent": USER_AGENT, "Accept": "application/json"}
WIKIDATA = "https://www.wikidata.org/w/api.php"
LABEL_LANGS = "en|fr|pt|es|de|it|nl|pl|sv|da|fi|cs|ro|hu|el|tr|uk|ru|ar|he|hi|ja|ko|zh|id|vi|th|fa|bn|ms|ca|hr|sk|sl|sr|sw|tl|no|bg"

# People, works, names — never a vocab illustration.
REJECT_INSTANCE = {
    "Q5",  # human
    "Q15632617",  # fictional human
    "Q95074",  # fictional character
    "Q11424",  # film
    "Q24856",  # film series
    "Q5398426",  # television series
    "Q15416",  # television program
    "Q7889",  # video game
    "Q7058673",  # video game series
    "Q482994",  # album
    "Q7366",  # song
    "Q134556",  # single
    "Q215380",  # musical group
    "Q188451",  # music genre
    "Q101352",  # family name
    "Q202444",  # given name
    "Q12308941",  # male given name
    "Q11879590",  # female given name
    "Q4167410",  # Wikimedia disambiguation page
    "Q13442814",  # scholarly article
    "Q13406463",  # Wikimedia list article
    "Q4167836",  # Wikimedia category
    "Q571",  # book
    "Q7725634",  # literary work
    "Q3305213",  # painting (named artwork, not the concept)
    "Q15773347",  # film character
    "Q4830453",  # business
    "Q783794",  # company
    "Q891723",  # public company
    "Q6881511",  # enterprise
    "Q431289",  # brand
    "Q43229",  # organization
}

SKIP_FILE = re.compile(
    r"(stamp|postage|portrait|signature|autograph|logo|screenshot|coat.of.arms|flag-mast)",
    re.I,
)
PAREN_RE = re.compile(r"\s*\([^)]*\)\s*")
FILLERS = {
    "the", "a", "an", "one", "un", "une", "le", "la", "les", "el", "los", "las",
    "o", "os", "um", "uma", "de", "du", "des", "d", "in", "of",
}
PERSON_EXTRAS = {"thomas", "mark", "willem", "donnie", "sir"}


def _filename_from_url(url: str) -> str:
    path = unquote((url or "").split("?", 1)[0])
    name = path.rsplit("/", 1)[-1]
    if name.lower().startswith("960px-") or name.lower().startswith("640px-") or name.lower().startswith("330px-"):
        name = name.split("-", 1)[-1]
    if name.lower().endswith(".svg.png"):
        name = name[: -len(".png")]
    return name


def image_url_is_diagram(url: str) -> bool:
    name = _filename_from_url(url).lower()
    return name.endswith(".svg") or ".svg." in name


def image_url_is_unrelated(lemma: str, french: str, url: str) -> bool:
    """True when the file is a compound of the word (Half-mast, Thomas More)."""
    name = _filename_from_url(url)
    if not name:
        return False
    title = name.rsplit(".", 1)[0].replace("_", " ").replace("-", " ")
    return _is_compound_title(lemma, title) or bool(french and _is_compound_title(french, title))


def image_url_is_weak(lemma: str, french: str, url: str) -> bool:
    """True when the image is missing, a diagram, or a homonym file."""
    if not (url or "").strip():
        return True
    if image_is_generated(url):
        # Our own files are named after the concept (gen_mars_marcher.jpg), which the
        # compound-title heuristic, written for stock filenames, reads as a homonym.
        return False
    return image_url_is_unrelated(lemma, french, url) or image_url_is_diagram(url)


def image_is_stock(url: str, source: str = "") -> bool:
    """True when the image comes from Wikimedia / Unsplash / Pexels."""
    src = (source or "").strip().casefold()
    if src in {"wikimedia", "unsplash", "pexels"}:
        return True
    text = (url or "").strip().casefold()
    return "wikimedia.org" in text or "unsplash.com" in text or "pexels.com" in text


def image_is_generated(url: str, source: str = "") -> bool:
    """True when the card already uses a local AI illustration."""
    text = (url or "").strip()
    if not text:
        return False
    src = (source or "").strip().casefold()
    if src in {"wikimedia", "unsplash", "pexels"}:
        return False
    if src in {"generated", "grok", "gemini", "openai"}:
        return True
    return (
        text.startswith("/api/media/")
        or text.startswith("/api/cache-media/sense/")
        or text.startswith("/api/cache-media/concepts/")
    )


IMAGE_PROVIDERS = {"grok", "xai", "codex", "openai"}
TRANSIENT_IMAGE_STATUS = {429, 500, 502, 503, 504}
MAX_IMAGE_ATTEMPTS = 4
PROVIDER_LABELS = {
    "grok": "Grok",
    "xai": "Grok",
    "claude": "Claude",
    "anthropic": "Claude",
    "codex": "Codex",
    "openai": "Codex",
    "local": "Local",
}


def image_generation_status(provider: str, settings: Settings) -> tuple[bool, str]:
    """Whether the settings AI can generate flashcard illustrations."""
    raw = (provider or "").strip().casefold() or "grok"
    name = {"anthropic": "claude", "openai": "codex", "xai": "grok"}.get(raw, raw)
    label = PROVIDER_LABELS.get(name, provider or "cette IA")
    if name == "codex":
        from .codex_cli import availability
        return availability()
    if name not in IMAGE_PROVIDERS:
        return False, (
            f"{label} ne génère pas d'images. "
            "Choisis Grok ou Codex dans les réglages d'images."
        )
    secret = resolve_for_provider("grok", settings)
    if not secret.value:
        return False, "Grok est sélectionné mais pas connecté (OAuth ou clé xAI dans les réglages)."
    return True, ""


DEFAULT_IMAGE_PROMPT = (
    "Create a square pedagogical photo that makes the target word {lemma} "
    "(Translated meaning: {french}; category: {category}) immediately unmistakable. "
    "Use exactly this example situation: {phrase} "
    "Translated version of the same situation: {phrase_fr} "
    "The target concept {sense} must be the central subject or action. "
    "Do not focus on incidental objects, people, or scenery from the sentence. "
    "For a verb, show the action in progress, not only its result. "
    "For an object, food, place, or tool, show the target itself filling most of the frame. "
    "Avoid visible faces unless the target explicitly requires a person, emotion, or facial action. "
    "Use one clear, realistic scene a language learner can recognize immediately. "
    "No text, no letters, no watermark, no brand, no logos."
)

LEGACY_IMAGE_PROMPTS = {
    DEFAULT_IMAGE_PROMPT.replace("Translated meaning", "French meaning").replace("Translated version", "French version"),
    "Square photo of the core beginner meaning of {sense}. "
    "Match this example situation when given: {phrase} "
    "Show the action or the object itself, not a side scene and not the finished result of the action. "
    "One clear subject a language learner would recognize immediately. "
    "No text, no letters, no watermark, no brand, no logos.",
    "Simple square photo illustrating ONLY the everyday meaning of the word {sense}. "
    "One clear object or scene a language learner would recognize immediately. "
    "Do not illustrate a different example word. "
    "No text, no letters, no watermark, no brand.",
}


def word_sense(lemma: str, french: str) -> str:
    if french and fold(french) != fold(lemma):
        return f"{lemma} ({french})"
    return lemma


def illustration_prompt(
    lemma: str,
    french: str,
    template: str = "",
    phrase: str = "",
    phrase_fr: str = "",
    category: str = "",
) -> str:
    raw = (template or "").strip() or DEFAULT_IMAGE_PROMPT
    if raw in LEGACY_IMAGE_PROMPTS:
        raw = DEFAULT_IMAGE_PROMPT
    category_hint = (category or "").strip()
    if category_hint.casefold() == "nom":
        category_hint = "unspecified"
    return (
        raw.replace("{lemma}", lemma or "")
        .replace("{french}", french or "")
        .replace("{sense}", word_sense(lemma, french))
        .replace("{phrase}", (phrase or "").strip() or "(none)")
        .replace("{phrase_fr}", (phrase_fr or "").strip() or "(none)")
        .replace("{category}", category_hint or "unspecified")
    )


def _is_compound_title(needle: str, title: str) -> bool:
    lem = fold(needle)
    cleaned = fold(PAREN_RE.sub(" ", title or "")).strip()
    if not lem or not cleaned or cleaned == lem:
        return False
    tokens = [token for token in re.split(r"[\s\-–—_/]+", cleaned) if token]
    if lem in tokens:
        extras = [token for token in tokens if token != lem and token not in FILLERS]
        if not extras:
            return False
        if tokens[0] == lem:
            return True
        if tokens[-1] == lem and any(extra in PERSON_EXTRAS for extra in extras):
            return True
        return False
    return any(token.startswith(lem) and len(token) > len(lem) + 1 for token in tokens)


def page_title_matches(lemma: str, title: str) -> bool:
    """Accept only a page whose title is the lemma, not Half-mast or Thomas More."""
    lem = fold(lemma)
    cleaned = fold(PAREN_RE.sub(" ", title or "")).strip()
    return bool(lem) and cleaned == lem


def score_sense(
    lemma: str,
    french: str,
    labels: dict[str, str],
    aliases: dict[str, list[str]] | None = None,
    instance_of: list[str] | None = None,
    located: bool = False,
) -> int:
    """Score how well a Wikidata item is the vocabulary sense, not a homonym."""
    if set(instance_of or []) & REJECT_INSTANCE:
        return 0
    lemma_f = fold(lemma)
    french_f = fold(french)
    if not lemma_f and not french_f:
        return 0

    lemma_label = False
    lemma_alias = False
    french_label = False
    french_alias = False
    for value in labels.values():
        folded = fold(value)
        if lemma_f and folded == lemma_f:
            lemma_label = True
        if french_f and folded == french_f:
            french_label = True
    for values in (aliases or {}).values():
        for value in values:
            folded = fold(value)
            if lemma_f and folded == lemma_f:
                lemma_alias = True
            if french_f and folded == french_f:
                french_alias = True

    lemma_hit = lemma_label or lemma_alias
    french_hit = french_label or french_alias
    if not lemma_hit and not french_hit:
        return 0
    # "plus" matching a supermarket named PLUS is not the English word "more".
    if lemma_f and french_f and lemma_f != french_f and not lemma_hit:
        return 0
    # Village named More, parish church, etc. — only keep if French also matches (Paris / Paris).
    if located and french_f and not french_hit:
        return 0

    # Alias-only hit on a multi-word person/title ("Half" → Willem Kalf).
    if lemma_alias and not lemma_label and not french_hit:
        primary = labels.get("en") or next(iter(labels.values()), "")
        if len(re.findall(r"\w+", primary, flags=re.UNICODE)) >= 2:
            return 0

    score = 0
    if lemma_label:
        score += 100
    elif lemma_alias:
        score += 70
    if french_label:
        score += 100
    elif french_alias:
        score += 70
    if lemma_hit and french_hit:
        score += 80
    return score


_SCENE_LOCKS = [RLock() for _ in range(64)]


def fetch_image(card: Card, bank: str, settings: Settings, deck_id: str = "",
                allow_generate: bool = False, force_generate: bool = False,
                target_language: str = "fr", strict_source: bool = False,
                generation_provider: str = "") -> tuple[str, str]:
    from ..image_library import context_key
    # Serialize the same scene through lookup + creation, including across decks.
    # Fixed lock stripes bound memory; explicit regeneration still bypasses reuse.
    key = context_key(card, target_language) or card.id
    lock = _SCENE_LOCKS[int(hashlib.sha256(key.encode()).hexdigest()[:8], 16) % len(_SCENE_LOCKS)]
    with lock:
        return _fetch_image(card, bank, settings, deck_id, allow_generate, force_generate,
                            target_language, strict_source, generation_provider)


def _fetch_image(
    card: Card,
    bank: str,
    settings: Settings,
    deck_id: str = "",
    allow_generate: bool = False,
    force_generate: bool = False,
    target_language: str = "fr",
    strict_source: bool = False,
    generation_provider: str = "",
) -> tuple[str, str]:
    if card.skip_image or card.category.casefold() == "grammaire":
        return "", ""
    from ..image_library import lookup, assign, register, blocked
    bank = (bank or "grok").casefold()
    generator = bank if bank in IMAGE_PROVIDERS else (generation_provider or settings.default_ai_provider)
    generator = {"openai": "codex", "xai": "grok"}.get(generator, generator)
    provider_source = "openai" if generator == "codex" else generator
    effective_bank = provider_source if force_generate else ("openai" if bank == "codex" else bank)
    if blocked(effective_bank):
        raise RuntimeError(f"Image source disabled: {effective_bank}")
    asset = lookup(card, target_language, effective_bank if strict_source else "") if not force_generate else None
    if asset:
        assign(card, asset, "contextual_cache")
        return card.image_url, card.image_source
    generate_only = force_generate or bank in IMAGE_PROVIDERS or bank == "generated"
    before = card.model_copy(deep=True)

    def record_assignment():
        asset = {"url": card.image_url, "source": card.image_source,
                 "id": card.image_asset_id, "metadata": card.image_provenance}
        card.image_url, card.image_source = before.image_url, before.image_source
        card.image_asset_id, card.image_provenance = before.image_asset_id, before.image_provenance
        assign(card, asset, "provider_fetch")

    found = ("", "")
    concept_labels: dict[str, str] = {}
    if not generate_only:
        if bank == "unsplash":
            found = _unsplash(card, settings)
        elif bank == "pexels":
            found = _pexels(card, settings)
        else:
            found, concept_labels = _wikimedia(card)
            if found[0] and image_url_is_weak(card.lemma, card.french, found[0]):
                found = ("", "")
            if not found[0] and bank == "wikimedia" and not strict_source:
                if settings.unsplash_access_key.strip():
                    found = _unsplash(card, settings)
                if not found[0] and settings.pexels_api_key.strip():
                    found = _pexels(card, settings)
        if found[0] and not image_url_is_weak(card.lemma, card.french, found[0]):
            card.image_url = found[0]
            card.image_source = found[1]
            register(card, target_language, verified_context=False, metadata={"origin": "provider_search"})
            record_assignment()
            return found
    if allow_generate:
        if blocked(provider_source):
            raise RuntimeError(f"Image source disabled: {provider_source}")
        ready, reason = image_generation_status(generator, settings)
        if not ready:
            raise RuntimeError(reason)
        if generator == "codex":
            if not deck_id:
                raise RuntimeError("Un deck est requis pour enregistrer l'image Codex.")
            from .codex_cli import generate_image
            from uuid import uuid4
            path = generate_image(illustration_prompt(card.lemma, card.french, settings.image_prompt,
                phrase=card.phrase, phrase_fr=card.phrase_fr, category=card.category),
                MEDIA_DIR / deck_id / (safe_name(card.lemma) + "-" + uuid4().hex[:12]))
            generated = (f"/api/media/{deck_id}/{path.name}", "openai")
        else:
            generated = _generate(card, settings, deck_id)
        if generated[0]:
            card.image_url = generated[0]
            card.image_source = generated[1]
            register(card, target_language, verified_context=True, metadata={
                "origin": "codex_cli" if generator == "codex" else "grok_api",
                "model": "Codex native image generation" if generator == "codex" else "grok-imagine-image-2.0",
                "prompt": illustration_prompt(card.lemma, card.french, settings.image_prompt,
                    phrase=card.phrase, phrase_fr=card.phrase_fr, category=card.category),
                "context_basis": "generation_prompt", "target_language": target_language,
                "visual_review": "not_reviewed",
            })
            record_assignment()
        return generated
    return "", ""


def _wikimedia(card: Card) -> tuple[tuple[str, str], dict[str, str]]:
    lang = find_language(card.language).get("wiki") or "en"
    try:
        with httpx.Client(timeout=12.0, headers=HEADERS, follow_redirects=True) as client:
            found, labels = _from_wikidata(client, card, lang)
            if found:
                return (found, "wikimedia"), labels
            found = _from_wikipedia(client, card, lang)
            if found:
                return (found, "wikimedia"), labels
    except httpx.HTTPError:
        return ("", ""), {}
    return ("", ""), {}


def _from_wikidata(client: httpx.Client, card: Card, wiki_lang: str) -> tuple[str, dict[str, str]]:
    ids: list[str] = []
    target_wiki = find_language(wiki_lang).get("wiki") or wiki_lang
    gloss_lang = "en" if (card.french and wiki_lang != "en") else wiki_lang
    for query, language in ((card.lemma, wiki_lang), (card.french, gloss_lang)):
        ids.extend(_wd_search(client, query, language))
    unique: list[str] = []
    seen: set[str] = set()
    for qid in ids:
        if qid and qid not in seen:
            seen.add(qid)
            unique.append(qid)
    if not unique:
        return "", {}
    entities = _wd_entities(client, unique[:16])
    ranked: list[tuple[int, dict]] = []
    for entity in entities:
        score = score_sense(
            card.lemma,
            card.french,
            _plain_labels(entity),
            _plain_aliases(entity),
            _claim_ids(entity, "P31"),
            located=bool((entity.get("claims") or {}).get("P625")),
        )
        if score > 0:
            ranked.append((score, entity))
    ranked.sort(key=lambda item: item[0], reverse=True)
    labels: dict[str, str] = {}
    if ranked:
        best = ranked[0][1]
        qid = (best.get("id") or "").strip()
        if qid:
            card.image_concept = qid
        labels = _plain_labels(best)
        for values in _plain_aliases(best).values():
            for value in values:
                if value and fold(value) not in {fold(v) for v in labels.values()}:
                    labels[f"alias:{fold(value)}"] = value
    for _score, entity in ranked:
        image = _pick_p18(entity)
        if image:
            qid = (entity.get("id") or "").strip()
            if qid:
                card.image_concept = qid
            return image, _plain_labels(entity)
    for _score, entity in ranked:
        image = _sitelink_image(client, entity, wiki_lang or target_wiki)
        if image:
            return image, _plain_labels(entity)
    return "", labels


def _from_wikipedia(client: httpx.Client, card: Card, lang: str) -> str:
    title = card.lemma.strip().replace(" ", "_")
    for candidate in (title, title[:1].upper() + title[1:] if title else title):
        if not candidate:
            continue
        response = client.get(f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{candidate}")
        if response.status_code != 200:
            continue
        data = response.json()
        if data.get("type") == "disambiguation":
            continue
        if not page_title_matches(card.lemma, data.get("title") or ""):
            continue
        found = (data.get("originalimage") or {}).get("source") or (data.get("thumbnail") or {}).get("source") or ""
        if found:
            return found
    search = client.get(
        f"https://{lang}.wikipedia.org/w/api.php",
        params={
            "action": "query",
            "generator": "search",
            "gsrsearch": f'intitle:"{card.lemma}"',
            "gsrlimit": 8,
            "prop": "pageimages",
            "piprop": "thumbnail",
            "pithumbsize": 640,
            "format": "json",
        },
    )
    if search.status_code != 200:
        return ""
    pages = (search.json().get("query") or {}).get("pages") or {}
    for page in pages.values():
        if not page_title_matches(card.lemma, page.get("title") or ""):
            continue
        thumb = (page.get("thumbnail") or {}).get("source") or ""
        if thumb:
            return thumb
    return ""


def _wd_search(client: httpx.Client, query: str, language: str) -> list[str]:
    needle = (query or "").strip()
    if not needle or not language:
        return []
    response = client.get(
        WIKIDATA,
        params={
            "action": "wbsearchentities",
            "search": needle,
            "language": language,
            "uselang": "fr",
            "type": "item",
            "limit": 8,
            "format": "json",
        },
    )
    if response.status_code != 200:
        return []
    return [hit.get("id") or "" for hit in response.json().get("search") or [] if hit.get("id")]


def _wd_entities(client: httpx.Client, ids: list[str]) -> list[dict]:
    if not ids:
        return []
    response = client.get(
        WIKIDATA,
        params={
            "action": "wbgetentities",
            "ids": "|".join(ids),
            "props": "labels|aliases|claims|sitelinks",
            "languages": LABEL_LANGS,
            "format": "json",
        },
    )
    if response.status_code != 200:
        return []
    entities = response.json().get("entities") or {}
    return [entities[qid] for qid in ids if qid in entities]


def _plain_labels(entity: dict) -> dict[str, str]:
    return {lang: obj.get("value") or "" for lang, obj in (entity.get("labels") or {}).items()}


def _plain_aliases(entity: dict) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for lang, items in (entity.get("aliases") or {}).items():
        out[lang] = [item.get("value") or "" for item in items if item.get("value")]
    return out


def _claim_ids(entity: dict, prop: str) -> list[str]:
    found: list[str] = []
    for claim in (entity.get("claims") or {}).get(prop) or []:
        value = ((claim.get("mainsnak") or {}).get("datavalue") or {}).get("value")
        if isinstance(value, dict) and value.get("id"):
            found.append(value["id"])
    return found


def _claim_files(entity: dict, prop: str) -> list[str]:
    found: list[str] = []
    for claim in (entity.get("claims") or {}).get(prop) or []:
        value = ((claim.get("mainsnak") or {}).get("datavalue") or {}).get("value")
        if isinstance(value, str) and value.strip():
            found.append(value.strip())
    return found


def _pick_p18(entity: dict) -> str:
    scored: list[tuple[int, str]] = []
    for name in _claim_files(entity, "P18"):
        if SKIP_FILE.search(name):
            continue
        rank = 20 if name.lower().endswith(".svg") else 5
        scored.append((rank, name))
    if not scored:
        return ""
    scored.sort(key=lambda item: item[0], reverse=True)
    return f"https://commons.wikimedia.org/wiki/Special:FilePath/{quote(scored[0][1])}?width=640"


def _sitelink_image(client: httpx.Client, entity: dict, wiki_lang: str) -> str:
    sitelinks = entity.get("sitelinks") or {}
    for site in (f"{wiki_lang}wiki", "enwiki", "frwiki"):
        title = (sitelinks.get(site) or {}).get("title") or ""
        if not title:
            continue
        lang = site.removesuffix("wiki") or "en"
        response = client.get(
            f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{title.replace(' ', '_')}"
        )
        if response.status_code != 200:
            continue
        data = response.json()
        if data.get("type") == "disambiguation":
            continue
        original = (data.get("originalimage") or {}).get("source") or ""
        if original:
            return original
        thumb = data.get("thumbnail") or {}
        if (thumb.get("width") or 0) < 120:
            continue
        if thumb.get("source"):
            return thumb["source"]
    return ""


def _photo_blob(*parts: str) -> str:
    return fold(" ".join(part or "" for part in parts))


def _contains_word(blob: str, needle: str) -> bool:
    if not needle:
        return False
    return re.search(r"(?<![\w-])" + re.escape(needle) + r"(?![\w-])", blob) is not None


def _photo_matches(card: Card, *parts: str) -> bool:
    blob = _photo_blob(*parts)
    if not blob:
        return False
    return any(_contains_word(blob, fold(needle)) for needle in (card.lemma, card.french))


def _unsplash(card: Card, settings: Settings) -> tuple[str, str]:
    key = settings.unsplash_access_key.strip()
    if not key:
        raise RuntimeError("clé Unsplash manquante (réglages)")
    query = " ".join(part for part in (card.lemma, card.french) if part).strip() or card.lemma
    with httpx.Client(timeout=12.0) as client:
        response = client.get(
            "https://api.unsplash.com/search/photos",
            params={"query": query, "per_page": 8, "orientation": "squarish"},
            headers={"Authorization": f"Client-ID {key}", "Accept-Version": "v1"},
        )
        response.raise_for_status()
        for photo in response.json().get("results") or []:
            if not _photo_matches(card, photo.get("alt_description") or "", photo.get("description") or ""):
                continue
            url = ((photo.get("urls") or {}).get("regular")) or ""
            if url:
                return url, "unsplash"
    return "", ""


def _pexels(card: Card, settings: Settings) -> tuple[str, str]:
    key = settings.pexels_api_key.strip()
    if not key:
        raise RuntimeError("clé Pexels manquante (réglages)")
    query = " ".join(part for part in (card.lemma, card.french) if part).strip() or card.lemma
    with httpx.Client(timeout=12.0) as client:
        response = client.get(
            "https://api.pexels.com/v1/search",
            params={"query": query, "per_page": 8},
            headers={"Authorization": key},
        )
        response.raise_for_status()
        for photo in response.json().get("photos") or []:
            if not _photo_matches(card, photo.get("alt") or "", photo.get("url") or ""):
                continue
            src = photo.get("src") or {}
            url = src.get("large") or src.get("medium") or ""
            if url:
                return url, "pexels"
    return "", ""


def _generate(card: Card, settings: Settings, deck_id: str) -> tuple[str, str]:
    if not (deck_id or "").strip():
        return "", ""
    secret = resolve_for_provider("grok", settings)
    if not secret.value:
        return "", ""
    headers = {"Authorization": f"Bearer {secret.value}", "Content-Type": "application/json"}
    headers.update(secret.extra_headers or {})
    with httpx.Client(timeout=180.0, follow_redirects=True) as client:
        payload = {
            "model": "grok-imagine-image-2.0",
            "prompt": illustration_prompt(
                card.lemma,
                card.french,
                settings.image_prompt,
                phrase=card.phrase,
                phrase_fr=card.phrase_fr,
                category=card.category,
            ),
            "n": 1,
            "response_format": "b64_json",
            "aspect_ratio": "1:1",
        }
        response = _post_generated_image(client, headers, payload)
        item = (response.json().get("data") or [{}])[0]
        raw = b""
        if item.get("b64_json"):
            raw = base64.b64decode(item["b64_json"])
        elif item.get("url"):
            downloaded = client.get(item["url"])
            downloaded.raise_for_status()
            raw = downloaded.content
        if not raw:
            return "", ""
    ensure_dirs()
    folder = MEDIA_DIR / deck_id
    folder.mkdir(parents=True, exist_ok=True)
    from uuid import uuid4
    path = folder / f"{safe_name(card.lemma)}-{uuid4().hex[:12]}.jpg"
    path.write_bytes(raw)
    return f"/api/media/{deck_id}/{path.name}", "grok"


def _post_generated_image(client: httpx.Client, headers: dict, payload: dict):
    for attempt in range(MAX_IMAGE_ATTEMPTS):
        try:
            response = client.post(
                "https://api.x.ai/v1/images/generations",
                headers=headers,
                json=payload,
            )
        except httpx.TransportError:
            if attempt + 1 >= MAX_IMAGE_ATTEMPTS:
                raise
            time.sleep(float(2**attempt))
            continue
        if response.status_code not in TRANSIENT_IMAGE_STATUS:
            response.raise_for_status()
            return response
        if attempt + 1 >= MAX_IMAGE_ATTEMPTS:
            response.raise_for_status()
        retry_after = (getattr(response, "headers", {}) or {}).get("Retry-After", "")
        try:
            delay = float(retry_after)
        except (TypeError, ValueError):
            delay = float(2**attempt)
        time.sleep(max(0.0, delay))
    raise RuntimeError("génération d'image Grok interrompue")

