import os
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[1]
STUDIO_DIR = API_DIR.parent
REPO_ROOT = STUDIO_DIR.parent

# Local state (decks, settings, media, exports, cache). Override with
# LEARNING_DATA_DIR to run an isolated instance next to the default one.
DATA_DIR = Path(os.environ.get("LEARNING_DATA_DIR") or STUDIO_DIR / "data").resolve()
DECKS_DIR = DATA_DIR / "decks"
EXPORTS_DIR = DATA_DIR / "exports"
MEDIA_DIR = DATA_DIR / "media"
SETTINGS_PATH = DATA_DIR / "settings.json"

SOURCE_CSV = {
    "pt": REPO_ROOT / "data" / "portuguese" / "brazilian.csv",
    "es": REPO_ROOT / "data" / "spanish" / "spanish.csv",
    "en": REPO_ROOT / "data" / "english" / "ngsl_vocab.csv",
}

WORD_FIELD = {"pt": "Portuguese", "es": "Spanish", "en": "English"}
IPA_FIELD = {"pt": "Pronunciation", "es": "Pronunciation", "en": "Phonetic"}


def ensure_dirs() -> None:
    for path in (DATA_DIR, DECKS_DIR, EXPORTS_DIR, MEDIA_DIR):
        path.mkdir(parents=True, exist_ok=True)
