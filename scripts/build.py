"""
Build .apkg files from CSV data + HTML templates.

Usage:
    python scripts/build.py                        # Build all decks, all versions
    python scripts/build.py portuguese             # Build one language, all versions
    python scripts/build.py portuguese 2000        # Build one deck, all versions
    python scripts/build.py portuguese 2000 basic  # Build one deck, one version
"""

import csv
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import zipfile

import genanki
import yaml

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST_DIR = os.path.join(BASE_DIR, "dist")

# Version offsets for model_id and deck_id to avoid conflicts in Anki
VERSION_OFFSETS = {
    "basic": 0,
    "complete": 100,
}


def read_template_file(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def load_config():
    with open(os.path.join(BASE_DIR, "decks.yaml"), "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_csv(path, limit=None):
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    if limit:
        rows = rows[:limit]
    return rows


def generate_cloze(sentence, word):
    """Replace the target word in the sentence with a blank."""
    if not sentence or not word:
        return ""
    pattern = re.escape(word)
    return re.sub(
        pattern, '<span class="cloze-blank">______</span>',
        sentence, count=1, flags=re.IGNORECASE,
    )


def compute_fields(row, version_config):
    """Compute derived fields like Cloze from source fields."""
    computed = version_config.get("computed_fields", {})
    for field_name, spec in computed.items():
        if spec["type"] == "cloze":
            sentence = row.get(spec["sentence_field"], "")
            word = row.get(spec["word_field"], "")
            row[field_name] = generate_cloze(sentence, word)
    return row


def build_model(lang_config, version_config, deck_config, version_name):
    """Create a genanki Model from config."""
    templates_dir = os.path.join(BASE_DIR, lang_config["templates_dir"])
    css = read_template_file(os.path.join(templates_dir, "style.css"))

    fields = [{"name": f} for f in version_config["fields"]]

    templates = []
    for tmpl in version_config["templates"]:
        front = read_template_file(os.path.join(templates_dir, tmpl["front"]))
        back = read_template_file(os.path.join(templates_dir, tmpl["back"]))
        templates.append({
            "name": tmpl["name"],
            "qfmt": front,
            "afmt": back,
        })

    offset = VERSION_OFFSETS.get(version_name, 0)
    model_id = deck_config["model_id"] + offset
    model_name = f"{deck_config['name']} ({version_name})"

    return genanki.Model(
        model_id=model_id,
        name=model_name,
        fields=fields,
        templates=templates,
        css=css,
    )


def interleave_card_order(apkg_path):
    """Reorder cards so all types for each note appear together."""
    tmpdir = tempfile.mkdtemp()
    try:
        with zipfile.ZipFile(apkg_path, "r") as z:
            z.extractall(tmpdir)

        db_file = "collection.anki21" if os.path.exists(
            os.path.join(tmpdir, "collection.anki21")
        ) else "collection.anki2"
        conn = sqlite3.connect(os.path.join(tmpdir, db_file))
        cur = conn.cursor()

        cur.execute("SELECT id, nid, ord FROM cards ORDER BY nid, ord")
        cards = cur.fetchall()

        for i, (card_id, _, _) in enumerate(cards):
            cur.execute("UPDATE cards SET due = ? WHERE id = ?", (i, card_id))

        conn.commit()
        conn.close()

        with zipfile.ZipFile(apkg_path, "w", zipfile.ZIP_DEFLATED) as z:
            for fname in os.listdir(tmpdir):
                z.write(os.path.join(tmpdir, fname), fname)
    finally:
        shutil.rmtree(tmpdir)


def build_deck_version(lang_name, lang_config, deck_config, version_name, version_config):
    """Build a single .apkg deck for a specific version."""
    source = deck_config.get("source", lang_config.get("source"))
    csv_path = os.path.join(BASE_DIR, source)
    limit = deck_config.get("limit")

    rows = load_csv(csv_path, limit)
    model = build_model(lang_config, version_config, deck_config, version_name)

    offset = VERSION_OFFSETS.get(version_name, 0)
    deck_id = deck_config["deck_id"] + offset
    deck_name = deck_config["name"]

    deck = genanki.Deck(deck_id=deck_id, name=deck_name)

    field_names = version_config["fields"]
    sentences_count = 0

    for row in rows:
        compute_fields(row, version_config)
        fields = [row.get(f, "") for f in field_names]
        note = genanki.Note(model=model, fields=fields)
        deck.add_note(note)
        if row.get("Phrase"):
            sentences_count += 1

    # File naming: base_file + _version.apkg
    base_file = deck_config["base_file"]
    output_path = os.path.join(DIST_DIR, f"{base_file}_{version_name}.apkg")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # Collect audio files referenced by notes
    media_files = []
    media_set = set()
    audio_dir = version_config.get("audio_dir")
    if audio_dir:
        audio_path = os.path.join(BASE_DIR, audio_dir)
        if os.path.isdir(audio_path):
            for row in rows:
                for audio_field in ("Audio", "Cloze_Audio"):
                    tag = row.get(audio_field, "")
                    if tag.startswith("[sound:"):
                        fname = tag[7:-1]
                        fpath = os.path.join(audio_path, fname)
                        if os.path.exists(fpath) and fpath not in media_set:
                            media_set.add(fpath)
                            media_files.append(fpath)

    package = genanki.Package(deck)
    package.media_files = media_files
    package.write_to_file(output_path)

    if len(version_config["templates"]) > 1:
        interleave_card_order(output_path)

    n_templates = len(version_config["templates"])
    extra = f", {sentences_count} sentences" if sentences_count else ""
    print(f"    {version_name:10s} → {os.path.basename(output_path)} "
          f"({len(rows)} notes × {n_templates} types{extra})")


def main():
    config = load_config()

    lang_filter = sys.argv[1] if len(sys.argv) > 1 else None
    deck_filter = sys.argv[2] if len(sys.argv) > 2 else None
    version_filter = sys.argv[3] if len(sys.argv) > 3 else None

    total = 0
    for lang_name, lang_config in config.items():
        if lang_filter and lang_filter.lower() not in lang_name.lower():
            continue

        versions = lang_config.get("versions", {})
        # Languages with fields at top level (no versions) use them as default
        if "fields" in lang_config and not any("fields" in v for v in versions.values()):
            for v_name, v_config in versions.items():
                v_config.setdefault("fields", lang_config["fields"])

        print(f"\n=== {lang_name.title()} ===")
        for deck_config in lang_config["decks"]:
            if deck_filter:
                match = (
                    deck_filter in deck_config["name"]
                    or deck_filter in deck_config.get("base_file", "")
                    or deck_filter == str(deck_config.get("limit", ""))
                )
                if not match:
                    continue

            print(f"  {deck_config['name']}:")
            for version_name, version_config in versions.items():
                if version_filter and version_filter != version_name:
                    continue
                build_deck_version(
                    lang_name, lang_config, deck_config,
                    version_name, version_config,
                )
                total += 1

    print(f"\nBuilt {total} deck(s) in dist/")


if __name__ == "__main__":
    main()
