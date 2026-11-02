"""Download spoken-frequency lists (OpenSubtitles / FrequencyWords).

These are the NGSL equivalent for other languages: most common spoken
words first, so a small deck is enough to start a conversation.

  python scripts/fetch_frequency_lists.py
  python scripts/fetch_frequency_lists.py --lang it --limit 10000
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "studio" / "api"
if str(API) not in sys.path:
    sys.path.insert(0, str(API))

from app.frequency import DEFAULT_LIMIT, SOURCE_CODES, fetch_language  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch OpenSubtitles frequency lists")
    parser.add_argument("--lang", action="append", dest="langs", help="Language id (repeatable)")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="Most common words to keep")
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "frequency")
    args = parser.parse_args()

    langs = args.langs or list(SOURCE_CODES)
    dest_dir: Path = args.out
    dest_dir.mkdir(parents=True, exist_ok=True)
    failed: list[str] = []
    for lang in langs:
        dest = dest_dir / f"{lang}.csv"
        print(f"fetch {lang} -> {dest} (top {args.limit})")
        try:
            fetch_language(lang, dest, limit=args.limit)
            count = max(sum(1 for _ in dest.open(encoding="utf-8")) - 1, 0)
            print(f"  ok {count} words")
        except Exception as exc:
            print(f"  FAIL {exc}")
            failed.append(lang)
    if failed:
        print("failed:", ", ".join(failed))
        return 1
    print(f"done: {len(langs)} lists in {dest_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
