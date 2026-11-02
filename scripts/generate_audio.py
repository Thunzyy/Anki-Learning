"""
Generate TTS audio files for vocabulary words using edge-tts.

Usage:
    python scripts/generate_audio.py data/portuguese/brazilian.csv --limit 2000
    python scripts/generate_audio.py data/portuguese/brazilian.csv --limit 2000 --voice pt-BR-AntonioNeural
"""

import asyncio
import csv
import os
import sys
import argparse
import re

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
    """Create a safe filename from a word."""
    # Remove special chars, keep letters/numbers/hyphens
    name = re.sub(r'[^\w\-]', '_', word.lower().strip())
    return name


async def generate_one(word, output_path, voice):
    """Generate audio for a single word."""
    communicate = edge_tts.Communicate(word, voice)
    await communicate.save(output_path)


async def generate_batch(words, audio_dir, voice, lang_prefix, batch_size=20):
    """Generate audio for a batch of words with concurrency limit."""
    sem = asyncio.Semaphore(batch_size)

    async def gen(word, path):
        async with sem:
            try:
                await generate_one(word, path, voice)
                return True
            except Exception as e:
                print(f"    Error for '{word}': {e}")
                return False

    tasks = []
    for word in words:
        fname = f"{lang_prefix}_{safe_filename(word)}.mp3"
        fpath = os.path.join(audio_dir, fname)
        if not os.path.exists(fpath):
            tasks.append(gen(word, fpath))

    if tasks:
        results = await asyncio.gather(*tasks)
        return sum(results)
    return 0


def main():
    parser = argparse.ArgumentParser(description="Generate TTS audio")
    parser.add_argument("csv_path", help="Path to source CSV")
    parser.add_argument("--limit", type=int, help="Process only first N words")
    parser.add_argument("--voice", help="Override TTS voice")
    parser.add_argument("--batch-size", type=int, default=20, help="Concurrent requests")
    args = parser.parse_args()

    csv_path = os.path.join(BASE_DIR, args.csv_path)
    lang = detect_language(csv_path)
    voice = args.voice or VOICES.get(lang, "pt-BR-FranciscaNeural")
    lang_prefix = lang[:2].lower()

    # Audio output directory
    lang_dir = os.path.dirname(csv_path)
    audio_dir = os.path.join(lang_dir, "audio")
    os.makedirs(audio_dir, exist_ok=True)

    # Read words
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        headers = list(reader.fieldnames)
        rows = list(reader)

    limit = args.limit or len(rows)
    words = [r[lang] for r in rows[:limit]]

    # Check existing
    existing = set(os.listdir(audio_dir))
    to_generate = [w for w in words
                   if f"{lang_prefix}_{safe_filename(w)}.mp3" not in existing]

    print(f"Language: {lang} | Voice: {voice}")
    print(f"Total words: {limit} | Already done: {limit - len(to_generate)} | To generate: {len(to_generate)}")

    if to_generate:
        # Generate in chunks to show progress
        chunk_size = 200
        total_done = 0
        for i in range(0, len(to_generate), chunk_size):
            chunk = to_generate[i:i + chunk_size]
            done = asyncio.run(generate_batch(chunk, audio_dir, voice, lang_prefix, args.batch_size))
            total_done += done
            print(f"  Progress: {total_done}/{len(to_generate)}")

        print(f"\nGenerated {total_done} audio files in {audio_dir}")

    # Update CSV with Audio field
    if "Audio" not in headers:
        headers.append("Audio")

    for row in rows[:limit]:
        word = row[lang]
        fname = f"{lang_prefix}_{safe_filename(word)}.mp3"
        fpath = os.path.join(audio_dir, fname)
        if os.path.exists(fpath):
            row["Audio"] = f"[sound:{fname}]"
        else:
            row.setdefault("Audio", "")

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow({h: row.get(h, "") for h in headers})

    audio_count = sum(1 for r in rows[:limit] if r.get("Audio"))
    print(f"Updated CSV: {audio_count}/{limit} words with audio tags")


if __name__ == "__main__":
    main()
