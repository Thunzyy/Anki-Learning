from app.frequency import fetch_urls, parse_frequency_text, write_frequency_csv


SAMPLE = """
ich 12000
das 9000
!!! 10
123 8
und 8000
ich 3
"""


def test_parse_frequency_text_keeps_most_common_first_and_skips_junk():
    rows = parse_frequency_text(SAMPLE, limit=10)
    assert [row["Word"] for row in rows] == ["ich", "das", "und"]
    assert rows[0]["Rank"] == "#1"
    assert rows[1]["Rank"] == "#2"
    assert rows[2]["Frequency"] == "8000"


def test_parse_frequency_text_respects_limit():
    rows = parse_frequency_text(SAMPLE, limit=2)
    assert len(rows) == 2
    assert rows[0]["Word"] == "ich"


def test_swahili_has_fallback_url_when_opensubtitles_is_missing():
    urls = fetch_urls("sw")
    assert any("freq_sw.txt" in url for url in urls)


def test_write_frequency_csv_roundtrip(tmp_path):
    path = tmp_path / "de.csv"
    rows = parse_frequency_text(SAMPLE)
    write_frequency_csv(path, rows)
    text = path.read_text(encoding="utf-8")
    assert text.splitlines()[0] == "Word,Rank,Frequency"
    assert "ich,#1,12000" in text
