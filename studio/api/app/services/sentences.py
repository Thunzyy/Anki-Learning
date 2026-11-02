from __future__ import annotations

import json

import httpx

from ..auth.sessions import resolve_for_provider
from ..languages import language_name
from ..models import Card, Settings
from ..protocol import contains_lemma, word_count

SYSTEM_PROMPT = """Tu es un assistant linguistique. Pour chaque mot, génère UNE phrase d'exemple.
Règles strictes:
- Phrase naturelle, niveau A2-B1
- 5 à 12 mots
- Le mot cible apparaît tel quel
- Sens le plus courant
- Si « scene_fr » est fournie, conserve exactement la même situation et adapte-la naturellement dans la langue apprise
- Dans ce cas, n'invente ni autre action, ni autre objet principal
- Pas de nom propre
- Pas de patron « J'aime X » / « Eu gosto de X » / « Me gusta X »
- Traduis la PHRASE ENTIÈRE vers {target}, pas seulement le mot isolé
Réponds UNIQUEMENT un JSON: tableau d'objets {{"word":"...","sentence":"...","translation":"..."}}."""

GRAMMAR_SYSTEM_PROMPT = """Tu génères UNE phrase d'exemple qui ILLUSTRE une règle de grammaire.
Règles strictes:
- La phrase est dans la langue apprise
- 5 à 14 mots, naturel, niveau A2-B1, sans nom propre
- N'insère PAS le titre de la règle dans la phrase
- Traduis la phrase entière vers {target}
Réponds UNIQUEMENT un JSON: tableau d'objets {{"word":"...","sentence":"...","translation":"..."}}."""

LOCAL_FRAMES = {
    "pt": [
        ("Ela deixou o {w} em cima da mesa.", "Elle a laissé le {fr} sur la table."),
        ("A gente precisa desse {w} agora.", "On a besoin de ce {fr} maintenant."),
        ("Eu encontrei um {w} perto da porta.", "J'ai trouvé un {fr} près de la porte."),
        ("Esse {w} não funciona muito bem.", "Ce {fr} ne marche pas très bien."),
        ("Você viu o {w} que eu comprei?", "Tu as vu le {fr} que j'ai acheté ?"),
    ],
    "es": [
        ("Ella dejó el {w} encima de la mesa.", "Elle a laissé le {fr} sur la table."),
        ("Necesitamos este {w} ahora mismo.", "On a besoin de ce {fr} tout de suite."),
        ("Encontré un {w} cerca de la puerta.", "J'ai trouvé un {fr} près de la porte."),
        ("Ese {w} no funciona muy bien.", "Ce {fr} ne marche pas très bien."),
        ("¿Viste el {w} que compré ayer?", "Tu as vu le {fr} que j'ai acheté hier ?"),
    ],
    "en": [
        ("She left the {w} on the kitchen table.", "Elle a laissé le {fr} sur la table."),
        ("We need this {w} right now.", "On a besoin de ce {fr} tout de suite."),
        ("I found a {w} near the door.", "J'ai trouvé un {fr} près de la porte."),
        ("That {w} does not work very well.", "Ce {fr} ne marche pas très bien."),
        ("Did you see the {w} I bought?", "Tu as vu le {fr} que j'ai acheté ?"),
    ],
}


def _lang_label(cards: list[Card], override: str = "") -> str:
    if override:
        return override
    if not cards:
        return "la langue cible"
    return language_name(cards[0].language, cards[0].language)


def generate_sentences(
    cards: list[Card],
    provider: str,
    settings: Settings,
    language_name_hint: str = "",
    target_name: str = "français",
    kind: str = "vocab",
) -> dict[str, dict[str, str]]:
    missing = [card for card in cards if not (card.phrase or "").strip()]
    if not missing:
        return {}
    if kind == "grammar" and provider == "local":
        return {}
    try:
        if provider in {"anthropic", "claude"}:
            return _anthropic(missing, settings, language_name_hint, target_name, kind=kind)
        if provider in {"xai", "grok"}:
            secret = resolve_for_provider("grok", settings)
            return _openai_compat(
                missing,
                secret.value,
                "https://api.x.ai/v1/chat/completions",
                "grok-3",
                extra_headers=secret.extra_headers,
                language_name_hint=language_name_hint,
                target_name=target_name,
                kind=kind,
            )
        if provider in {"openai", "codex"}:
            secret = resolve_for_provider("codex", settings)
            if secret.source != "api-key":
                from .codex_cli import ask_json
                return _to_lookup(ask_json(_prompt_for(kind, target_name) + "\n" +
                                          _user_payload(missing, language_name_hint, kind)))
            return _openai_compat(
                missing,
                secret.value,
                "https://api.openai.com/v1/chat/completions",
                "gpt-4.1-mini",
                extra_headers=secret.extra_headers,
                language_name_hint=language_name_hint,
                target_name=target_name,
                kind=kind,
            )
    except Exception:
        # A failed provider must not silently replace an example by a template
        # that treats every verb/adjective as a noun and changes its scene.
        raise
    if kind == "grammar":
        return {}
    return _local(missing)


def generate_glosses(
    cards: list[Card],
    provider: str,
    settings: Settings,
    language_name_hint: str = "",
    target_name: str = "français",
) -> dict[str, dict[str, str]]:
    missing = [card for card in cards if not (card.french or "").strip()]
    if not missing:
        return {}
    lang = _lang_label(missing, language_name_hint)
    words = [card.lemma for card in missing]
    prompt = (
        f"Traduis en {target_name} chacun de ces mots en {lang}. "
        'Réponds UNIQUEMENT un JSON: [{"word":"...","french":"...","category":"Nom|Verbe|Adjectif|Adverbe|Grammaire"}]. '
        f"La clé french contient la traduction vers {target_name}. "
        f"Mots: {json.dumps(words, ensure_ascii=False)}"
    )
    items = _ask_json(provider, settings, prompt)
    out: dict[str, dict[str, str]] = {}
    for item in items:
        word = str(item.get("word") or "").casefold()
        if word:
            out[word] = {
                "french": str(item.get("french") or "").strip(),
                "category": str(item.get("category") or "Nom").strip(),
            }
    return out


def generate_phrase_translations(
    cards: list[Card],
    provider: str,
    settings: Settings,
    target_name: str = "français",
) -> dict[str, str]:
    pending = [card for card in cards if (card.phrase or "").strip() and not (card.phrase_fr or "").strip()]
    if not pending:
        return {}
    payload = [{"word": card.lemma, "sentence": card.phrase} for card in pending]
    prompt = (
        f"Traduis chaque phrase ENTIÈREMENT en {target_name}, pas mot à mot isolé. "
        'Réponds UNIQUEMENT un JSON: [{"word":"...","translation":"..."}]. '
        f"Phrases: {json.dumps(payload, ensure_ascii=False)}"
    )
    items = _ask_json(provider, settings, prompt)
    out: dict[str, str] = {}
    for item in items:
        word = str(item.get("word") or "").casefold()
        if word:
            out[word] = str(item.get("translation") or "").strip()
    return out


def generate_ipa(cards: list[Card], provider: str, settings: Settings, language_name_hint: str = "") -> dict[str, str]:
    missing = [card for card in cards if not (card.ipa or "").strip()]
    if not missing:
        return {}
    lang = _lang_label(missing, language_name_hint)
    words = [card.lemma for card in missing]
    prompt = (
        f"Donne l'IPA de chaque mot en {lang}. "
        'Réponds UNIQUEMENT un JSON: [{"word":"...","ipa":"/.../"}]. '
        f"Mots: {json.dumps(words, ensure_ascii=False)}"
    )
    items = _ask_json(provider, settings, prompt)
    out: dict[str, str] = {}
    for item in items:
        word = str(item.get("word") or "").casefold()
        if word:
            out[word] = str(item.get("ipa") or "").strip()
    return out


def _ask_json(provider: str, settings: Settings, prompt: str) -> list[dict]:
    if provider == "local":
        return []
    system = "Tu es un assistant linguistique. Réponds uniquement en JSON valide, sans markdown."
    try:
        if provider in {"anthropic", "claude"}:
            secret = resolve_for_provider("claude", settings)
            if not secret.value:
                return []
            import anthropic
            headers = {"anthropic-beta": "oauth-2025-04-20"} if secret.source == "cli-oauth" else None
            client = anthropic.Anthropic(api_key=secret.value, default_headers=headers)
            response = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=4096,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
            return _parse_json_array(response.content[0].text)
        url = "https://api.x.ai/v1/chat/completions"
        model = "grok-3"
        secret = resolve_for_provider("grok" if provider in {"xai", "grok"} else "codex", settings)
        if provider in {"openai", "codex"}:
            if secret.source != "api-key":
                from .codex_cli import ask_json
                return ask_json(system + "\n" + prompt)
            url = "https://api.openai.com/v1/chat/completions"
            model = "gpt-4.1-mini"
        if not secret.value:
            return []
        payload = {
            "model": model,
            "temperature": 0.2,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        }
        headers = {"Authorization": f"Bearer {secret.value}", "Content-Type": "application/json"}
        headers.update(secret.extra_headers)
        with httpx.Client(timeout=60.0) as client:
            response = client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            return _parse_json_array(response.json()["choices"][0]["message"]["content"])
    except Exception as exc:
        raise RuntimeError(f"IA {provider}: {exc}") from exc


def _local(cards: list[Card]) -> dict[str, dict[str, str]]:
    code = cards[0].language if cards else "pt"
    if code not in LOCAL_FRAMES:
        return {}
    frames = LOCAL_FRAMES[code]
    out: dict[str, dict[str, str]] = {}
    for index, card in enumerate(cards):
        sentence_t, translation_t = frames[index % len(frames)]
        french = card.french or card.lemma
        sentence = sentence_t.format(w=card.lemma, fr=french)
        translation = translation_t.format(w=card.lemma, fr=french)
        if not contains_lemma(sentence, card.lemma) or word_count(sentence) < 5:
            sentence = f"Eu uso a palavra {card.lemma} nesta frase simples."
            translation = f"J'utilise le mot {card.french or card.lemma} dans cette phrase simple."
        out[card.lemma.casefold()] = {"sentence": sentence, "translation": translation}
    return out


def _parse_json_array(text: str) -> list[dict]:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        text = text.rsplit("```", 1)[0]
    return json.loads(text)


def _to_lookup(items: list[dict]) -> dict[str, dict[str, str]]:
    lookup: dict[str, dict[str, str]] = {}
    for item in items:
        word = str(item.get("word") or "").strip().casefold()
        if not word:
            continue
        lookup[word] = {
            "sentence": str(item.get("sentence") or "").strip(),
            "translation": str(item.get("translation") or "").strip(),
        }
    return lookup


def _prompt_for(kind: str, target_name: str) -> str:
    if kind == "grammar":
        return GRAMMAR_SYSTEM_PROMPT.format(target=target_name)
    return SYSTEM_PROMPT.format(target=target_name)


def _user_payload(cards: list[Card], language_name_hint: str, kind: str) -> str:
    lang = _lang_label(cards, language_name_hint)
    if kind == "grammar":
        rules = [{"word": card.lemma, "rule": card.french} for card in cards]
        return f"Langue: {lang}. Règles: {json.dumps(rules, ensure_ascii=False)}"
    words = [
        {
            "word": card.lemma,
            "meaning": card.french,
            "category": card.category,
            "scene_fr": card.phrase_fr,
        }
        for card in cards
    ]
    return f"Langue: {lang}. Mots et scènes: {json.dumps(words, ensure_ascii=False)}"


def _anthropic(cards: list[Card], settings: Settings, language_name_hint: str = "", target_name: str = "français", kind: str = "vocab") -> dict[str, dict[str, str]]:
    secret = resolve_for_provider("claude", settings)
    if not secret.value:
        raise RuntimeError("aucune session Claude OAuth ni clé Anthropic")
    import anthropic

    default_headers = {}
    if secret.source == "cli-oauth":
        default_headers["anthropic-beta"] = "oauth-2025-04-20"
    client = anthropic.Anthropic(api_key=secret.value, default_headers=default_headers or None)
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        system=_prompt_for(kind, target_name),
        messages=[{"role": "user", "content": _user_payload(cards, language_name_hint, kind)}],
    )
    text = response.content[0].text
    return _to_lookup(_parse_json_array(text))


def _openai_compat(
    cards: list[Card],
    api_key: str,
    url: str,
    model: str,
    extra_headers: dict[str, str] | None = None,
    language_name_hint: str = "",
    target_name: str = "français",
    kind: str = "vocab",
) -> dict[str, dict[str, str]]:
    if not api_key.strip():
        raise RuntimeError("aucune session OAuth ni clé API pour ce fournisseur")
    payload = {
        "model": model,
        "temperature": 0.4,
        "messages": [
            {"role": "system", "content": _prompt_for(kind, target_name)},
            {"role": "user", "content": _user_payload(cards, language_name_hint, kind)},
        ],
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    with httpx.Client(timeout=60.0) as client:
        response = client.post(
            url,
            headers=headers,
            json=payload,
        )
        response.raise_for_status()
        text = response.json()["choices"][0]["message"]["content"]
    return _to_lookup(_parse_json_array(text))
