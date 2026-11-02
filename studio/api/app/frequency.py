from __future__ import annotations

import csv
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

RAW_BASE = "https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018"

# Learning language id → FrequencyWords 2018 folder. Spoken subtitles ≈ NGSL for that language.
SOURCE_CODES: dict[str, str] = {
    "ar": "ar",
    "bg": "bg",
    "bn": "bn",
    "ca": "ca",
    "cs": "cs",
    "da": "da",
    "de": "de",
    "el": "el",
    "fa": "fa",
    "fi": "fi",
    "fr": "fr",
    "he": "he",
    "hi": "hi",
    "hr": "hr",
    "hu": "hu",
    "id": "id",
    "it": "it",
    "ja": "ja",
    "ko": "ko",
    "ms": "ms",
    "en": "en",
    "es": "es",
    "nl": "nl",
    "no": "no",
    "pl": "pl",
    "pt": "pt_br",
    "pt-pt": "pt",
    "ro": "ro",
    "ru": "ru",
    "sk": "sk",
    "sl": "sl",
    "sr": "sr",
    "sv": "sv",
    "sw": "sw",
    "th": "th",
    "tl": "tl",
    "tr": "tr",
    "uk": "uk",
    "ur": "ur",
    "vi": "vi",
    "zh": "zh_cn",
}

DEFAULT_LIMIT = 10000

# Used when FrequencyWords has no file (Swahili is absent from OpenSubtitles 2018).
FALLBACK_URLS: dict[str, list[str]] = {
    "sw": [
        "https://raw.githubusercontent.com/maximofcom/Most-Frequent-Words-In-323-Languages/main/Languages/freq_sw.txt",
    ],
}


def parse_frequency_text(text: str, limit: int | None = None) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.rsplit(None, 1)
        if len(parts) != 2:
            continue
        word, count = parts
        if not any(char.isalpha() for char in word):
            continue
        try:
            freq = int(count)
        except ValueError:
            continue
        key = word.casefold()
        if key in seen:
            continue
        seen.add(key)
        rows.append({
            "Word": word,
            "Rank": f"#{len(rows) + 1}",
            "Frequency": str(freq),
        })
        if limit and len(rows) >= limit:
            break
    return rows


def write_frequency_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["Word", "Rank", "Frequency"])
        writer.writeheader()
        writer.writerows(rows)


def frequency_urls(code: str) -> list[str]:
    return [
        f"{RAW_BASE}/{code}/{code}_50k.txt",
        f"{RAW_BASE}/{code}/{code}_full.txt",
    ]


def fetch_text(url: str, timeout: int = 60) -> str:
    request = Request(url, headers={"User-Agent": "learning-frequency-fetch/1.0"})
    with urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def fetch_urls(language: str) -> list[str]:
    code = SOURCE_CODES.get(language, language.replace("-", "_"))
    return frequency_urls(code) + list(FALLBACK_URLS.get(language) or [])


def fetch_language(language: str, dest: Path, limit: int = DEFAULT_LIMIT) -> Path:
    last_error: Exception | None = None
    for url in fetch_urls(language):
        try:
            rows = parse_frequency_text(fetch_text(url), limit=limit)
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            last_error = exc
            continue
        if rows:
            write_frequency_csv(dest, rows)
            return dest
    raise RuntimeError(f"could not fetch frequency list for {language}: {last_error}")
