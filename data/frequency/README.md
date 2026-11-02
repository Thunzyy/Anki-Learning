# Spoken frequency lists (NGSL equivalent)

These CSVs are the **spoken-frequency counterpart of the English NGSL** for other languages.

Source: [hermitdave/FrequencyWords](https://github.com/hermitdave/FrequencyWords) (OpenSubtitles 2018). Words are ranked by how often they appear in film and series subtitles — everyday speech, not academic writing.

| Column | Meaning |
|--------|---------|
| `Word` | Surface form as it appears in subtitles |
| `Rank` | `#1` = most common |
| `Frequency` | Raw corpus count |

Each file keeps the **10,000 most common words** (or the full list if it is shorter). Rare words stay at the bottom so a 500- or 1000-word deck is enough to start a conversation.

Every UI language has a spoken list here, including English (`en.csv`), Spanish (`es.csv`), Brazilian Portuguese (`pt.csv`) and European Portuguese (`pt-pt.csv`). Curated pedagogical lists (NGSL, TSL, Brazilian, Spanish) stay available beside them.

Swahili is not in OpenSubtitles 2018; `sw.csv` comes from Wikipedia word frequency.

Refresh or extend a list:

```bash
python scripts/fetch_frequency_lists.py
python scripts/fetch_frequency_lists.py --lang it --limit 10000
```

Learning picks up every `data/frequency/<lang>.csv` as strategy `spoken-<lang>` (no YAML edit).
