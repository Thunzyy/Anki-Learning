from __future__ import annotations

import csv
from pathlib import Path

import yaml

from .models import Card
from .paths import REPO_ROOT
from .protocol import apply_normalize

STRATEGIES_PATH = REPO_ROOT / "data" / "strategies.yaml"
GRAMMAR_PATH = REPO_ROOT / "data" / "grammar" / "cefr.yaml"
STANDARD_LIMITS = [500, 1000, 2000, 3000, 4000, 5000]
CEFR_LEVELS = ["a1", "a2", "b1", "b2", "c1"]
SPOKEN_LABEL = "Spoken frequency (OpenSubtitles)"

_STRATEGIES_CACHE: tuple[tuple, list[dict]] | None = None


def _resolve(path: str | Path) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = REPO_ROOT / candidate
    return candidate


def load_cefr_rules() -> list[dict]:
    if not GRAMMAR_PATH.exists():
        return []
    raw = yaml.safe_load(GRAMMAR_PATH.read_text(encoding="utf-8")) or {}
    rules: list[dict] = []
    for item in raw.get("rules") or []:
        rule_id = str(item.get("id") or "").strip()
        title = str(item.get("title_fr") or "").strip()
        explain = str(item.get("explain_fr") or "").strip()
        level = str(item.get("level") or "").strip().lower()
        if not (rule_id and title and explain and level):
            continue
        rules.append({
            "id": rule_id,
            "level": level,
            "category": str(item.get("category") or "syntax").strip(),
            "title_fr": title,
            "explain_fr": explain,
        })
    return rules


def _raw_file() -> dict:
    path = STRATEGIES_PATH
    if not path.exists():
        return {"defaults": {}, "strategies": []}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _defaults() -> dict:
    return _raw_file().get("defaults") or {}


_COUNT_CACHE: dict[str, tuple[float, int]] = {}


def _count_rows(path: Path) -> int:
    if not path.exists():
        return 0
    key = str(path)
    mtime = path.stat().st_mtime
    cached = _COUNT_CACHE.get(key)
    if cached and cached[0] == mtime:
        return cached[1]
    with path.open(encoding="utf-8", newline="") as handle:
        count = max(sum(1 for _ in handle) - 1, 0)
    _COUNT_CACHE[key] = (mtime, count)
    return count


def _sizes(available: int, declared: list[int] | None) -> list[int]:
    base = list(declared or _defaults().get("sizes") or STANDARD_LIMITS)
    sizes = [limit for limit in base if limit < available]
    if available > 0:
        sizes.append(available)
    return sizes


def _features(spec: dict) -> dict:
    merged = dict((_defaults().get("features") or {}))
    merged.update(spec.get("features") or {})
    return {
        "phonetic": bool(merged.get("phonetic", True)),
        "phrase": bool(merged.get("phrase", False)),
        "audio": bool(merged.get("audio", False)),
        "image": bool(merged.get("image", False)),
    }


def _rank_value(row: dict[str, str], columns: dict[str, str]) -> int:
    raw = ""
    rank_col = columns.get("rank") or ""
    if rank_col:
        raw = row.get(rank_col) or ""
    if not raw:
        raw = row.get("Rank") or ""
    raw = str(raw).strip().lstrip("#")
    try:
        return int(raw)
    except ValueError:
        return 10**9


def order_rows(rows: list[dict[str, str]], columns: dict[str, str] | None = None) -> list[dict[str, str]]:
    mapping = columns or {}
    if not rows or all(_rank_value(row, mapping) == 10**9 for row in rows):
        return list(rows)
    return sorted(rows, key=lambda row: _rank_value(row, mapping))


def _item_from_spec(spec: dict) -> dict:
    rows = spec.get("rows")
    if rows is not None:
        available = len(rows)
        source = spec.get("source") or GRAMMAR_PATH
        if source:
            source = _resolve(source)
        sizes = list(spec.get("sizes") or ([available] if available else []))
    else:
        source = _resolve(spec["source"])
        available = _count_rows(source)
        sizes = _sizes(available, spec.get("sizes"))
    audio_dir = spec.get("audio_dir")
    return {
        "id": spec["id"],
        "kind": spec.get("kind") or "frequency",
        "cefr": spec.get("cefr") or "",
        "language": spec["language"],
        "language_label": spec.get("language_label") or spec["language"],
        "label": spec["label"],
        "source": source,
        "columns": dict(spec.get("columns") or {}),
        "features": _features(spec),
        "available": available,
        "sizes": sizes,
        "rows": rows,
        "audio_dir": _resolve(audio_dir) if audio_dir else None,
        "import": list(spec.get("import") or []),
    }


def _spoken_specs() -> list[dict]:
    folder = REPO_ROOT / "data" / "frequency"
    if not folder.is_dir():
        return []
    from .languages import find_language

    specs: list[dict] = []
    for path in sorted(folder.glob("*.csv")):
        lang = path.stem
        if not lang or lang.startswith("_"):
            continue
        meta = find_language(lang)
        language = meta["id"] if meta["id"] != "und" else lang
        try:
            relative = path.relative_to(REPO_ROOT).as_posix()
        except ValueError:
            relative = str(path)
        specs.append({
            "id": f"spoken-{lang}",
            "language": language,
            "language_label": meta["label"] if meta["id"] != "und" else lang,
            "label": "Spoken frequency (Wikipedia)" if lang == "sw" else SPOKEN_LABEL,
            "source": relative,
            "columns": {"word": "Word", "rank": "Rank"},
            "features": {"phonetic": True, "phrase": False, "audio": False, "image": False},
        })
    return specs


def _grammar_specs() -> list[dict]:
    from .languages import LANGUAGES

    grouped: dict[str, list[dict]] = {level: [] for level in CEFR_LEVELS}
    for rule in load_cefr_rules():
        if rule["level"] in grouped:
            grouped[rule["level"]].append(rule)
    rows_by_level = {
        level: [
            {
                "Word": rule["title_fr"],
                "French": rule["explain_fr"],
                "Category": f"{level}:{rule['category']}",
            }
            for rule in rules
        ]
        for level, rules in grouped.items()
    }
    specs: list[dict] = []
    for lang in LANGUAGES:
        for level in CEFR_LEVELS:
            rows = rows_by_level[level]
            specs.append({
                "id": f"grammar-{lang['id']}-{level}",
                "kind": "grammar",
                "cefr": level,
                "language": lang["id"],
                "language_label": lang["label"],
                "label": f"Grammaire {level.upper()}",
                "source": "data/grammar/cefr.yaml",
                "columns": {"word": "Word", "french": "French", "category": "Category"},
                "features": {"phonetic": False, "phrase": True, "audio": False, "image": False},
                "sizes": [len(rows)] if rows else [],
                "rows": rows,
            })
    return specs


def load_strategies() -> list[dict]:
    global _STRATEGIES_CACHE
    key = _strategies_fingerprint()
    if _STRATEGIES_CACHE and _STRATEGIES_CACHE[0] == key:
        return _STRATEGIES_CACHE[1]
    items: list[dict] = []
    seen: set[str] = set()
    for spec in (_raw_file().get("strategies") or []) + _spoken_specs() + _grammar_specs():
        if spec["id"] in seen:
            continue
        seen.add(spec["id"])
        items.append(_item_from_spec(spec))
    _STRATEGIES_CACHE = (key, items)
    return items


def _stat(path: Path) -> tuple[float, int]:
    if not path.exists():
        return (0.0, 0)
    info = path.stat()
    return (info.st_mtime, info.st_size)


def _strategies_fingerprint() -> tuple:
    freq = REPO_ROOT / "data" / "frequency"
    spoken = ()
    if freq.is_dir():
        spoken = tuple(sorted((p.name, *_stat(p)) for p in freq.glob("*.csv")))
    return (_stat(STRATEGIES_PATH), _stat(GRAMMAR_PATH), spoken)


def strategy_by_id(strategy_id: str) -> dict:
    for spec in load_strategies():
        if spec["id"] == strategy_id:
            return spec
    raise ValueError(f"stratégie inconnue: {strategy_id}")


def import_specs() -> list[dict]:
    specs: list[dict] = []
    for strategy in load_strategies():
        for item in strategy["import"]:
            specs.append({
                "id": item["id"],
                "name": item["name"],
                "language": strategy["language"],
                "language_label": strategy["language_label"],
                "source": strategy["source"],
                "columns": strategy["columns"],
                "limit": item.get("limit"),
                "phrase": strategy["features"]["phrase"],
                "audio": strategy["features"]["audio"],
                "image": strategy["features"]["image"],
                "audio_dir": strategy["audio_dir"],
            })
    return specs


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def fields_from_row(row: dict[str, str], columns: dict[str, str]) -> dict[str, str]:
    def take(*keys: str) -> str:
        for key in keys:
            if key and row.get(key):
                return str(row[key]).strip()
        return ""

    word_col = columns.get("word") or ""
    lemma = take(word_col, "Word", "English", "Portuguese", "Spanish")
    return {
        "surface": lemma,
        "lemma": lemma,
        "french": take(columns.get("french", ""), "French"),
        "category": take(columns.get("category", ""), "Category"),
        "ipa": take(columns.get("ipa", ""), "Pronunciation", "Phonetic"),
        "phrase": take(columns.get("phrase", ""), "Phrase", "Example_PT", "Example_ES", "Example_EN"),
        "phrase_fr": take(columns.get("phrase_fr", ""), "Traduction", "Example_FR"),
        "audio": take(columns.get("audio", ""), "Audio"),
    }


def cards_from_strategy(spec: dict, limit: int | None, deck_id: str) -> list[Card]:
    rows = spec.get("rows")
    if rows is None:
        rows = read_csv_rows(Path(spec["source"]))
    rows = order_rows(rows, spec.get("columns") or {})
    selected = rows[:limit] if limit else rows
    cards: list[Card] = []
    with_audio = bool((spec.get("features") or {}).get("audio") or spec.get("audio"))
    language = spec["language"]
    columns = spec.get("columns") or {}
    for index, row in enumerate(selected, start=1):
        fields = fields_from_row(row, columns)
        lemma = fields["lemma"]
        if not lemma:
            continue
        audio_name = ""
        if with_audio:
            from .legacy import parse_sound
            audio_name = parse_sound(fields.get("audio") or "")
        audio_url = f"/api/legacy-audio/{language}/{audio_name}" if audio_name else ""
        cards.append(apply_normalize(Card(
            id=f"{deck_id}-{index:05d}",
            lemma=lemma,
            surface=fields["surface"] or lemma,
            language=language,
            french=fields["french"],
            category=fields["category"],
            ipa=fields["ipa"],
            phrase=fields["phrase"],
            phrase_fr=fields["phrase_fr"],
            audio_url=audio_url,
        )))
    return cards
