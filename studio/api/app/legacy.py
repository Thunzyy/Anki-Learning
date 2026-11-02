from __future__ import annotations

import csv
import re
from pathlib import Path

from .catalog import cards_from_strategy, import_specs, load_strategies, strategy_by_id
from .lexicon import row_to_fields
from .models import Card, Deck, DeckFeatures
from .paths import REPO_ROOT
from .protocol import apply_normalize
from .store import new_id, new_stage_map, now_iso, save_deck, _deck_path

SOUND_RE = re.compile(r"\[sound:([^\]]+)\]", re.IGNORECASE)

AUDIO_DIRS = {
    "pt": REPO_ROOT / "data" / "portuguese" / "audio",
}

def list_strategies() -> list[dict]:
    out: list[dict] = []
    for spec in load_strategies():
        out.append({
            "id": spec["id"],
            "kind": spec.get("kind") or "frequency",
            "cefr": spec.get("cefr") or "",
            "language": spec["language"],
            "language_label": spec["language_label"],
            "label": spec["label"],
            "available": spec["available"],
            "sizes": spec["sizes"],
            "phrase": spec["features"]["phrase"],
            "audio": spec["features"]["audio"],
        })
    return out


def create_from_strategy(
    strategy_id: str,
    limit: int | None,
    name: str,
    features: DeckFeatures | None = None,
    target_language: str = "fr",
    target_label: str = "Français",
) -> Deck:
    spec = strategy_by_id(strategy_id)
    if spec["available"] <= 0:
        raise ValueError("catalogue vide" if spec.get("kind") == "grammar" else "liste de fréquence vide")
    capped = min(limit, spec["available"]) if limit else spec["available"]
    flags = spec["features"]
    chosen = features or DeckFeatures(
        phrase=bool(flags["phrase"]),
        phonetic=bool(flags["phonetic"]),
        audio=bool(flags["audio"]),
        image=bool(flags["image"]),
    )
    deck_id = new_id()
    deck = Deck(
        id=deck_id,
        name=(name or "").strip() or f"{spec['label']} {capped}",
        language=spec["language"],
        language_label=spec["language_label"],
        target_language=target_language,
        target_label=target_label,
        source=f"strategy:{spec['id']}",
        kind="grammar" if spec.get("kind") == "grammar" else "vocab",
        features=chosen,
        cards=cards_from_strategy(spec, capped, deck_id),
        created_at=now_iso(),
        updated_at=now_iso(),
    )
    deck.card_count = len(deck.cards)
    deck.stages = new_stage_map(deck)
    deck.stages["ingest"] = "passed"
    deck.stages["normalize"] = "passed"
    from .cache import apply_deck
    apply_deck(deck)
    from .pipeline import sync_stages
    sync_stages(deck)
    return deck


def parse_sound(value: str) -> str:
    match = SOUND_RE.search(value or "")
    return match.group(1).strip() if match else ""


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def cards_from_rows(
    language: str,
    rows: list[dict[str, str]],
    deck_id: str,
    limit: int | None = None,
    with_audio: bool = False,
) -> list[Card]:
    selected = rows[:limit] if limit else rows
    cards: list[Card] = []
    for index, row in enumerate(selected, start=1):
        fields = row_to_fields(language, row)
        lemma = (fields.get("lemma") or "").strip()
        if not lemma:
            continue
        audio_name = parse_sound(row.get("Audio") or "") if with_audio else ""
        audio_url = f"/api/legacy-audio/{language}/{audio_name}" if audio_name else ""
        card = apply_normalize(Card(
            id=f"{deck_id}-{index:05d}",
            lemma=lemma,
            surface=fields.get("surface") or lemma,
            language=language,
            french=fields.get("french", ""),
            category=fields.get("category", ""),
            ipa=fields.get("ipa", ""),
            phrase=fields.get("phrase", ""),
            phrase_fr=fields.get("phrase_fr", ""),
            audio_url=audio_url,
        ))
        cards.append(card)
    return cards


def build_legacy_deck(spec: dict, rows: list[dict[str, str]] | None = None) -> Deck:
    language = spec["language"]
    source_rows = rows if rows is not None else read_csv_rows(Path(spec["source"]))
    features = DeckFeatures(
        phrase=bool(spec.get("phrase")),
        phonetic=True,
        audio=bool(spec.get("audio")),
        image=bool(spec.get("image", False)),
    )
    if spec.get("columns"):
        cards = cards_from_strategy({**spec, "rows": source_rows, "features": {"audio": features.audio}}, spec.get("limit"), spec["id"])
    else:
        cards = cards_from_rows(
            language,
            source_rows,
            deck_id=spec["id"],
            limit=spec.get("limit"),
            with_audio=features.audio,
        )
    deck = Deck(
        id=spec["id"],
        name=spec["name"],
        language=language,
        language_label=spec.get("language_label") or language,
        target_language="fr",
        target_label="Français",
        source="legacy",
        features=features,
        cards=cards,
        created_at=now_iso(),
        updated_at=now_iso(),
    )
    deck.card_count = len(deck.cards)
    deck.stages = new_stage_map(deck)
    deck.stages["ingest"] = "passed"
    deck.stages["normalize"] = "passed"
    deck.stages["translate"] = "passed"
    if features.phonetic:
        deck.stages["phonetic"] = "passed"
    if features.phrase:
        deck.stages["sentences"] = "passed"
    if features.audio:
        deck.stages["audio"] = "passed"
    return deck


def import_legacy_decks(specs: list[dict] | None = None, overwrite: bool = False) -> list[Deck]:
    imported: list[Deck] = []
    for spec in specs or import_specs():
        path = _deck_path(spec["id"])
        if path.exists() and not overwrite:
            continue
        rows = spec.get("rows")
        imported.append(save_deck(build_legacy_deck(spec, rows)))
    return imported


def legacy_audio_path(language: str, filename: str) -> Path | None:
    folders = []
    if language in AUDIO_DIRS:
        folders.append(AUDIO_DIRS[language])
    for spec in load_strategies():
        if spec["language"] == language and spec.get("audio_dir"):
            folders.append(Path(spec["audio_dir"]))
    name = Path(filename).name
    if name != filename or ".." in name:
        return None
    for folder in folders:
        path = folder / name
        if path.is_file():
            return path
    return None
