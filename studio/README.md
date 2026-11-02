# Anki Learning technical notes

For setup, see the [installation guide](../docs/installation.md). For a first deck, follow the [illustrated walkthrough](../README.md#create-your-first-deck).

## How deck enrichment works

The **Complete with AI** button, `POST /api/decks/{id}/enrich`, and `python -m app.complete <deck_id>` use `pipeline.fill_missing`. It processes translations, phonetics, examples, and audio in parallel batches. When searching for photos, it uses the selected image bank and looks up the word's meaning through Wikidata. For words still missing images, it can generate illustrations using Grok or Codex, depending on the selected provider. If it cannot continue, it reports the reason, such as a disconnected provider, an exhausted quota, or a word with no suitable visual meaning.

The `normalize` stage marks function words such as *be*, *she*, and articles with `skip_image`. They remain unillustrated. Running enrichment again resumes work on the missing fields.

## Contextual image library

Open **Manage images** in a deck to preview replacements by source or remove illustrations. Replacements retain a backup and assignment history. To reuse an illustration, the library matches both meaning and scene. A word alone is insufficient for an automatic match.

Historical images remain available. If the only recorded source is `generated`, the provider is unknown. In the editor, search for an image and confirm its context for reuse across languages. Meaning and scene IDs work across languages. Without them, matching requires the same translated meaning and translated example in the same target language.

The [image library guide](../docs/image-library.md) covers importing Grok and Gemini files, removing Wikimedia images across decks, and restoring backups. Gemini images are available through import. Codex can generate images directly through an authenticated session in the official CLI.

## Generating images with Codex

Install a Codex CLI version with native image generation support and sign in with `codex login`. In **Settings**, choose Codex as the image provider. You can select the text provider independently. For an existing deck, select Codex under **Manage images** when replacing illustrations.

Enrichment first looks for an illustration with matching context. A known scene also supplies the French example to translate into another language. Within a single server process, simultaneous enrichment requests for the same scene share one generation. Explicit regeneration creates a new image.

Learning runs Codex jobs with connectors, plugins, and shell commands disabled. It does not send the ChatGPT session to the OpenAI API as an API key. Each job retains its results under `codex-jobs/` and must produce a new, valid native image file in the expected output directory. Failures and quota errors remain visible. Learning does not silently substitute a drawing or a generic sentence.

Provenance records the source `openai`, the origin `codex_cli`, the prompt, and the file hash. The prompt establishes the intended context. Checking whether the image depicts that context still requires visual review.

## Validation and exports

Exports stop if content validation fails or a referenced media file is missing. Anki media filenames are derived from file contents to avoid collisions between decks. Exports retain the current note IDs and seven-field Anki model. The existing media field includes audio and credits.

GitHub publication uploads the new file before replacing the old one. On failure, the old file or its backup remains available. Replacement targets the asset by name so that editions with similar names are preserved. Large files are streamed and asset names are normalized to remove accents.

A reviewer can explicitly approve a short example or a valid inflected form. The exception applies to the exact word, sentence, and translation. Editing any of those values invalidates it. Normal generation rules still apply.

Audio requires `edge-tts>=7.3.1`. Learning writes audio to a temporary file and makes it reusable only after successful generation. A retry cannot pick up partial output from a failed attempt.

## Sending all decks to Anki

**Send all decks to Anki**, below the navigation, checks AnkiConnect and sends every language using the existing per-deck export and import routines, validation rules, and note IDs. If a deck fails, Learning records the failure and continues with the remaining decks. The summary lets you retry failed and unprocessed decks.

Imports run sequentially and continue as you navigate within Learning. Keep the tab open. Reloading the page interrupts the batch. **Stop after this deck** lets the current import finish. Use **Send this language to Anki** for one language. Importing into Anki Desktop leaves AnkiWeb synchronization under your control and preserves older notes absent from Learning.

## Bulk publication and the library image count

**Publish all to GitHub**, next to the Anki button, publishes one APKG per deck to the release configured in Settings. It uses the same validation and replacement process as individual publication. Decks are processed in sequence. You can stop after the current deck and retry remaining decks. The batch continues after failures and displays warnings about cleaning up old assets. One global action can run at a time in a tab. Keep that tab open until it finishes.

The **Images** count in the library measures distinct image URLs in use across all decks after applying the editor's normalization rules. Images reused across cards or languages count once. The count excludes assignment history, unused media, and illustrations hidden on grammar cards. It does not check whether each file exists on disk. A read error marks the count as unavailable instead of showing a partial total.

## Local storage

Application state lives outside version control in `studio/data/` or the directory set by `LEARNING_DATA_DIR`.

| Path | Contents |
| --- | --- |
| `decks/<id>.json` | A complete deck, including its cards |
| `settings.json` | Providers, keys, and the illustration prompt |
| `media/<deck_id>/` | Images and audio belonging to one deck |
| `cache/` | Glossaries, scenes, and shared image files |
| `image-library.sqlite3` | Image provenance, context, and operation history |
| `image-backups/` | Backups before replacement and files quarantined during migration |
| `exports/` | Generated `.apkg` and `.csv` files |

On first API startup, Learning initializes historical decks (`legacy-*.json`) from the CSV files in `data/`. Subsequent reviewed corrections live in the local deck JSON files and their logs. GitHub releases include their own APKG files, CSV files, and checksums. Back up local corrections separately from the original CSVs.
