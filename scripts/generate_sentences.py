"""
Generate example sentences for vocabulary words using Claude API.

Usage:
    python scripts/generate_sentences.py data/portuguese/brazilian.csv --limit 2000
    python scripts/generate_sentences.py data/portuguese/brazilian.csv --start 500 --limit 1000

Adds Example_PT and Example_FR columns to the CSV.
Saves progress after each batch so you can resume if interrupted.
"""

import anthropic
import csv
import json
import os
import sys
import argparse
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SYSTEM_PROMPT = """Tu es un assistant linguistique expert. Pour chaque mot donné, génère :
1. Une phrase d'exemple SIMPLE et NATURELLE dans la langue source (niveau A2-B1)
2. Sa traduction en français

Règles :
- Phrases courtes (6-12 mots max)
- Vocabulaire simple et quotidien
- Le mot doit être utilisé dans son sens le plus courant
- Pour les verbes conjugués, utilise l'infinitif dans la phrase
- La phrase doit aider à comprendre le SENS du mot par le contexte
- Pas de noms propres
- Pas de phrases génériques type "J'aime X" pour chaque mot

Réponds UNIQUEMENT en JSON valide, sans markdown ni commentaire."""


def build_batch_prompt(words, lang_name):
    """Build a prompt for a batch of words."""
    word_list = json.dumps(words, ensure_ascii=False)
    return f"""Génère une phrase d'exemple pour chacun de ces mots en {lang_name}.

Mots : {word_list}

Réponds en JSON : un tableau d'objets avec exactement les clés "word", "sentence", "translation".
Exemple de format :
[
  {{"word": "casa", "sentence": "Minha casa fica perto do parque.", "translation": "Ma maison est près du parc."}},
  {{"word": "comer", "sentence": "Vamos comer juntos hoje à noite.", "translation": "On va manger ensemble ce soir."}}
]"""


def parse_response(text):
    """Extract JSON array from API response."""
    text = text.strip()
    # Remove markdown code fences if present
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        text = text.rsplit("```", 1)[0]
    return json.loads(text)


def generate_batch(client, words, lang_name, max_retries=3):
    """Generate sentences for a batch of words."""
    for attempt in range(max_retries):
        try:
            response = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": build_batch_prompt(words, lang_name)}],
            )
            results = parse_response(response.content[0].text)

            # Build lookup by word
            lookup = {}
            for item in results:
                lookup[item["word"].lower().strip()] = item
            return lookup

        except (json.JSONDecodeError, KeyError) as e:
            print(f"    Parse error (attempt {attempt + 1}): {e}")
            if attempt == max_retries - 1:
                return {}
        except anthropic.RateLimitError:
            wait = 30 * (attempt + 1)
            print(f"    Rate limited, waiting {wait}s...")
            time.sleep(wait)
        except Exception as e:
            print(f"    API error (attempt {attempt + 1}): {e}")
            if attempt == max_retries - 1:
                return {}

    return {}


def detect_language(csv_path):
    """Detect language from CSV headers."""
    with open(csv_path, "r", encoding="utf-8") as f:
        headers = csv.DictReader(f).fieldnames
    if "Portuguese" in headers:
        return "Portuguese", "portugais brésilien"
    elif "Spanish" in headers:
        return "Spanish", "espagnol"
    elif "English" in headers:
        return "English", "anglais"
    raise ValueError(f"Unknown language in headers: {headers}")


def main():
    parser = argparse.ArgumentParser(description="Generate example sentences")
    parser.add_argument("csv_path", help="Path to source CSV")
    parser.add_argument("--limit", type=int, help="Process only first N words")
    parser.add_argument("--start", type=int, default=0, help="Start from word N (skip already done)")
    parser.add_argument("--batch-size", type=int, default=50, help="Words per API call")
    args = parser.parse_args()

    csv_path = os.path.join(BASE_DIR, args.csv_path)
    field_name, lang_name = detect_language(csv_path)

    # Read existing CSV
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        headers = list(reader.fieldnames)
        rows = list(reader)

    # Add new columns if missing
    if "Example_PT" not in headers and field_name == "Portuguese":
        headers.extend(["Example_PT", "Example_FR"])
    elif "Example_ES" not in headers and field_name == "Spanish":
        headers.extend(["Example_ES", "Example_FR"])
    elif "Example_EN" not in headers and field_name == "English":
        headers.extend(["Example_EN", "Example_FR"])

    example_col = f"Example_{field_name[:2].upper()}"
    translation_col = "Example_FR"

    # Determine range
    end = args.limit if args.limit else len(rows)
    start = args.start
    to_process = []

    for i in range(start, min(end, len(rows))):
        if not rows[i].get(example_col):
            to_process.append(i)

    print(f"Language: {lang_name}")
    print(f"Total words: {len(rows)}")
    print(f"To generate: {len(to_process)} sentences (rows {start}-{end})")

    if not to_process:
        print("Nothing to do!")
        return

    client = anthropic.Anthropic()
    batch_size = args.batch_size
    done = 0
    failed = []

    for batch_start in range(0, len(to_process), batch_size):
        batch_indices = to_process[batch_start:batch_start + batch_size]
        batch_words = [rows[i][field_name] for i in batch_indices]

        print(f"\n  Batch {batch_start // batch_size + 1}: words {batch_indices[0]+1}-{batch_indices[-1]+1} ({len(batch_words)} words)")

        lookup = generate_batch(client, batch_words, lang_name)

        for idx in batch_indices:
            word = rows[idx][field_name].lower().strip()
            if word in lookup:
                rows[idx][example_col] = lookup[word]["sentence"]
                rows[idx][translation_col] = lookup[word]["translation"]
                done += 1
            else:
                failed.append(rows[idx][field_name])
                rows[idx].setdefault(example_col, "")
                rows[idx].setdefault(translation_col, "")

        # Save progress after each batch
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            for row in rows:
                writer.writerow({h: row.get(h, "") for h in headers})

        print(f"  Saved. Progress: {done}/{len(to_process)}")

    print(f"\nDone! Generated {done}/{len(to_process)} sentences.")
    if failed:
        print(f"Failed ({len(failed)}): {', '.join(failed[:20])}")


if __name__ == "__main__":
    main()
