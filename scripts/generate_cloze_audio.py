"""
Generate cloze TTS audio: full sentence with target word replaced by a pause.

Usage:
    python scripts/generate_cloze_audio.py data/portuguese/brazilian.csv --limit 2000
"""

import asyncio
import csv
import os
import re
import sys
import argparse

import edge_tts

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

VOICES = {
    "Portuguese": "pt-BR-FranciscaNeural",
    "Spanish": "es-MX-DaliaNeural",
    "English": "en-US-JennyNeural",
}


def detect_language(csv_path):
    with open(csv_path, "r", encoding="utf-8") as f:
        headers = csv.DictReader(f).fieldnames
    for lang in ["Portuguese", "Spanish", "English"]:
        if lang in headers:
            return lang
    raise ValueError(f"Unknown language: {headers}")


def safe_filename(word):
    return re.sub(r'[^\w\-]', '_', word.lower().strip())


def make_cloze_text(phrase, word):
    """Replace the target word with '...' to create a natural pause in TTS."""
    if not phrase or not word:
        return None
    result = re.sub(re.escape(word), '...', phrase, count=1, flags=re.IGNORECASE)
    if result == phrase:
        return None
    return result


async def generate_batch(items, audio_dir, voice, batch_size=20):
    """Generate cloze audio for a batch of (word, cloze_text) pairs."""
    sem = asyncio.Semaphore(batch_size)

    async def gen(fname, text):
        async with sem:
            fpath = os.path.join(audio_dir, fname)
            try:
                comm = edge_tts.Communicate(text, voice)
                await comm.save(fpath)
                return True
            except Exception as e:
                print(f"    Error for '{fname}': {e}")
                return False

    tasks = []
    for fname, text in items:
        fpath = os.path.join(audio_dir, fname)
        if not os.path.exists(fpath):
            tasks.append(gen(fname, text))

    if tasks:
        results = await asyncio.gather(*tasks)
        return sum(results)
    return 0


def main():
    parser = argparse.ArgumentParser(description="Generate cloze TTS audio")
    parser.add_argument("csv_path", help="Path to source CSV")
    parser.add_argument("--limit", type=int, help="Process only first N words")
    parser.add_argument("--voice", help="Override TTS voice")
    parser.add_argument("--batch-size", type=int, default=20, help="Concurrent requests")
    args = parser.parse_args()

    csv_path = os.path.join(BASE_DIR, args.csv_path)
    lang = detect_language(csv_path)
    voice = args.voice or VOICES.get(lang, "pt-BR-FranciscaNeural")
    lang_prefix = lang[:2].lower()

    lang_dir = os.path.dirname(csv_path)
    audio_dir = os.path.join(lang_dir, "audio")
    os.makedirs(audio_dir, exist_ok=True)

    # Read CSV
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        headers = list(reader.fieldnames)
        rows = list(reader)

    limit = args.limit or len(rows)

    # Build list of cloze audio to generate
    to_generate = []
    for row in rows[:limit]:
        word = row[lang]
        phrase = row.get("Phrase", "")
        cloze_text = make_cloze_text(phrase, word)
        if cloze_text:
            fname = f"{lang_prefix}_cloze_{safe_filename(word)}.mp3"
            to_generate.append((fname, cloze_text, row))

    # Check existing
    existing = set(os.listdir(audio_dir))
    new_items = [(fname, text) for fname, text, _ in to_generate
                 if fname not in existing]

    print(f"Language: {lang} | Voice: {voice}")
    print(f"Words with phrases: {len(to_generate)} | Already done: {len(to_generate) - len(new_items)} | To generate: {len(new_items)}")

    if new_items:
        chunk_size = 200
        total_done = 0
        for i in range(0, len(new_items), chunk_size):
            chunk = new_items[i:i + chunk_size]
            done = asyncio.run(generate_batch(chunk, audio_dir, voice, args.batch_size))
            total_done += done
            print(f"  Progress: {total_done}/{len(new_items)}")

        print(f"\nGenerated {total_done} cloze audio files")

    # Update CSV with Cloze_Audio field
    if "Cloze_Audio" not in headers:
        headers.append("Cloze_Audio")

    for fname, _, row in to_generate:
        fpath = os.path.join(audio_dir, fname)
        if os.path.exists(fpath):
            row["Cloze_Audio"] = f"[sound:{fname}]"

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow({h: row.get(h, "") for h in headers})

    cloze_count = sum(1 for r in rows[:limit] if r.get("Cloze_Audio"))
    print(f"Updated CSV: {cloze_count}/{limit} words with cloze audio tags")


if __name__ == "__main__":
    main()
