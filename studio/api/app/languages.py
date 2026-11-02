from __future__ import annotations

import unicodedata

LANGUAGES: list[dict[str, str]] = [
    {"id": "fr", "label": "Français", "wiki": "fr", "voice": "fr-FR-DeniseNeural"},
    {"id": "en", "label": "Anglais", "wiki": "en", "voice": "en-US-JennyNeural"},
    {"id": "de", "label": "Allemand", "wiki": "de", "voice": "de-DE-KatjaNeural"},
    {"id": "ar", "label": "Arabe", "wiki": "ar", "voice": "ar-SA-ZariyahNeural"},
    {"id": "bg", "label": "Bulgare", "wiki": "bg", "voice": "bg-BG-KalinaNeural"},
    {"id": "zh", "label": "Chinois (mandarin)", "wiki": "zh", "voice": "zh-CN-XiaoxiaoNeural"},
    {"id": "ko", "label": "Coréen", "wiki": "ko", "voice": "ko-KR-SunHiNeural"},
    {"id": "da", "label": "Danois", "wiki": "da", "voice": "da-DK-ChristelNeural"},
    {"id": "es", "label": "Espagnol", "wiki": "es", "voice": "es-ES-ElviraNeural"},
    {"id": "fi", "label": "Finnois", "wiki": "fi", "voice": "fi-FI-NooraNeural"},
    {"id": "el", "label": "Grec", "wiki": "el", "voice": "el-GR-AthinaNeural"},
    {"id": "he", "label": "Hébreu", "wiki": "he", "voice": "he-IL-HilaNeural"},
    {"id": "hi", "label": "Hindi", "wiki": "hi", "voice": "hi-IN-SwaraNeural"},
    {"id": "hu", "label": "Hongrois", "wiki": "hu", "voice": "hu-HU-NoemiNeural"},
    {"id": "id", "label": "Indonésien", "wiki": "id", "voice": "id-ID-GadisNeural"},
    {"id": "it", "label": "Italien", "wiki": "it", "voice": "it-IT-ElsaNeural"},
    {"id": "ja", "label": "Japonais", "wiki": "ja", "voice": "ja-JP-NanamiNeural"},
    {"id": "nl", "label": "Néerlandais", "wiki": "nl", "voice": "nl-NL-FennaNeural"},
    {"id": "no", "label": "Norvégien", "wiki": "no", "voice": "nb-NO-PernilleNeural"},
    {"id": "pl", "label": "Polonais", "wiki": "pl", "voice": "pl-PL-ZofiaNeural"},
    {"id": "pt", "label": "Portugais (Brésil)", "wiki": "pt", "voice": "pt-BR-FranciscaNeural"},
    {"id": "pt-pt", "label": "Portugais (Portugal)", "wiki": "pt", "voice": "pt-PT-RaquelNeural"},
    {"id": "ro", "label": "Roumain", "wiki": "ro", "voice": "ro-RO-AlinaNeural"},
    {"id": "ru", "label": "Russe", "wiki": "ru", "voice": "ru-RU-SvetlanaNeural"},
    {"id": "sv", "label": "Suédois", "wiki": "sv", "voice": "sv-SE-SofieNeural"},
    {"id": "cs", "label": "Tchèque", "wiki": "cs", "voice": "cs-CZ-VlastaNeural"},
    {"id": "th", "label": "Thaï", "wiki": "th", "voice": "th-TH-PremwadeeNeural"},
    {"id": "tr", "label": "Turc", "wiki": "tr", "voice": "tr-TR-EmelNeural"},
    {"id": "uk", "label": "Ukrainien", "wiki": "uk", "voice": "uk-UA-PolinaNeural"},
    {"id": "vi", "label": "Vietnamien", "wiki": "vi", "voice": "vi-VN-HoaiMyNeural"},
    {"id": "ca", "label": "Catalan", "wiki": "ca", "voice": "ca-ES-JoanaNeural"},
    {"id": "hr", "label": "Croate", "wiki": "hr", "voice": "hr-HR-GabrijelaNeural"},
    {"id": "sk", "label": "Slovaque", "wiki": "sk", "voice": "sk-SK-ViktoriaNeural"},
    {"id": "sl", "label": "Slovène", "wiki": "sl", "voice": "sl-SI-PetraNeural"},
    {"id": "sr", "label": "Serbe", "wiki": "sr", "voice": "sr-RS-SophieNeural"},
    {"id": "fa", "label": "Persan", "wiki": "fa", "voice": "fa-IR-DilaraNeural"},
    {"id": "sw", "label": "Swahili", "wiki": "sw", "voice": "sw-KE-ZuriNeural"},
    {"id": "tl", "label": "Tagalog", "wiki": "tl", "voice": "fil-PH-BlessicaNeural"},
    {"id": "bn", "label": "Bengali", "wiki": "bn", "voice": "bn-IN-TanishaaNeural"},
    {"id": "ur", "label": "Ourdou", "wiki": "ur", "voice": "ur-PK-UzmaNeural"},
    {"id": "ms", "label": "Malais", "wiki": "ms", "voice": "ms-MY-YasminNeural"},
]


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", (text or "").strip())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()


def find_language(query: str) -> dict[str, str]:
    raw = (query or "").strip()
    if not raw:
        return {"id": "und", "label": "Langue inconnue", "wiki": "en", "voice": "en-US-JennyNeural"}
    needle = _fold(raw)
    for item in LANGUAGES:
        if _fold(item["id"]) == needle or _fold(item["label"]) == needle:
            return item
    slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in raw).strip("-") or "custom"
    return {"id": slug, "label": raw, "wiki": slug[:2] or "en", "voice": ""}


def language_name(code: str, fallback: str = "") -> str:
    found = find_language(code)
    if found["id"] != "und":
        return found["label"]
    return fallback or code
