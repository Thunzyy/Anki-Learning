from pathlib import Path

from app.catalog import cards_from_strategy, load_strategies, strategy_by_id


def _write_catalog(tmp_path: Path) -> Path:
    csv_path = tmp_path / "data" / "italian" / "freq.csv"
    csv_path.parent.mkdir(parents=True)
    csv_path.write_text(
        "Italiano,IPA,French,Esempio,Traduzione\n"
        "ciao,/ˈtʃa.o/,salut,Ciao amico mio oggi.,Salut mon ami aujourd'hui.\n"
        "grazie,/ˈɡrat.tsje/,merci,Ti do grazie adesso.,Je te remercie maintenant.\n",
        encoding="utf-8",
    )
    yaml_path = tmp_path / "data" / "strategies.yaml"
    yaml_path.write_text(
        """
defaults:
  sizes: [1000, 2000]
strategies:
  - id: it-freq
    language: it
    language_label: Italiano
    label: Frequenza italiana
    source: data/italian/freq.csv
    columns:
      word: Italiano
      ipa: IPA
      french: French
      phrase: Esempio
      phrase_fr: Traduzione
    features:
      phrase: true
      phonetic: true
""",
        encoding="utf-8",
    )
    return yaml_path


def test_load_strategies_is_cached_until_the_catalog_changes(tmp_path, monkeypatch):
    yaml_path = _write_catalog(tmp_path)
    monkeypatch.setattr("app.catalog.STRATEGIES_PATH", yaml_path)
    monkeypatch.setattr("app.catalog.REPO_ROOT", tmp_path)
    first = load_strategies()
    second = load_strategies()
    assert first is second
    yaml_path.write_text(yaml_path.read_text(encoding="utf-8") + "\n# touch\n", encoding="utf-8")
    third = load_strategies()
    assert third is not first
    assert any(item["id"] == "it-freq" for item in third)


def test_catalog_picks_up_a_new_language_strategy(tmp_path, monkeypatch):
    yaml_path = _write_catalog(tmp_path)
    monkeypatch.setattr("app.catalog.STRATEGIES_PATH", yaml_path)
    monkeypatch.setattr("app.catalog.REPO_ROOT", tmp_path)
    items = load_strategies()
    spec = next(item for item in items if item["id"] == "it-freq")
    assert spec["language"] == "it"
    assert spec["available"] == 2
    assert spec["features"]["phrase"] is True


def test_cards_follow_declared_columns(tmp_path, monkeypatch):
    yaml_path = _write_catalog(tmp_path)
    monkeypatch.setattr("app.catalog.STRATEGIES_PATH", yaml_path)
    monkeypatch.setattr("app.catalog.REPO_ROOT", tmp_path)
    spec = strategy_by_id("it-freq")
    cards = cards_from_strategy(spec, limit=1, deck_id="d1")
    assert len(cards) == 1
    assert cards[0].lemma == "ciao"
    assert cards[0].french == "salut"
    assert cards[0].ipa == "/ˈtʃa.o/"
    assert "ciao" in cards[0].phrase.lower()


def test_unknown_strategy_id(tmp_path, monkeypatch):
    yaml_path = _write_catalog(tmp_path)
    monkeypatch.setattr("app.catalog.STRATEGIES_PATH", yaml_path)
    monkeypatch.setattr("app.catalog.REPO_ROOT", tmp_path)
    try:
        strategy_by_id("nope")
    except ValueError as exc:
        assert "nope" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_cards_take_most_common_first_even_if_csv_is_shuffled(tmp_path, monkeypatch):
    csv_path = tmp_path / "data" / "german" / "freq.csv"
    csv_path.parent.mkdir(parents=True)
    csv_path.write_text(
        "Word,Rank,Frequency\n"
        "selten,#3,10\n"
        "und,#1,9000\n"
        "haus,#2,400\n",
        encoding="utf-8",
    )
    yaml_path = tmp_path / "data" / "strategies.yaml"
    yaml_path.write_text(
        """
strategies:
  - id: de-freq
    language: de
    language_label: Allemand
    label: Frequenz
    source: data/german/freq.csv
    columns:
      word: Word
      rank: Rank
""",
        encoding="utf-8",
    )
    monkeypatch.setattr("app.catalog.STRATEGIES_PATH", yaml_path)
    monkeypatch.setattr("app.catalog.REPO_ROOT", tmp_path)
    spec = strategy_by_id("de-freq")
    cards = cards_from_strategy(spec, limit=2, deck_id="d1")
    assert [card.lemma for card in cards] == ["und", "haus"]


def test_cards_from_strategy_none_limit_takes_all(tmp_path, monkeypatch):
    yaml_path = _write_catalog(tmp_path)
    monkeypatch.setattr("app.catalog.STRATEGIES_PATH", yaml_path)
    monkeypatch.setattr("app.catalog.REPO_ROOT", tmp_path)
    spec = strategy_by_id("it-freq")
    cards = cards_from_strategy(spec, limit=None, deck_id="d1")
    assert [card.lemma for card in cards] == ["ciao", "grazie"]


def test_sizes_include_conversation_starter(tmp_path, monkeypatch):
    csv_path = tmp_path / "data" / "german" / "freq.csv"
    csv_path.parent.mkdir(parents=True)
    header = "Word,Rank\n"
    body = "".join(f"w{i},#{i}\n" for i in range(1, 2501))
    csv_path.write_text(header + body, encoding="utf-8")
    yaml_path = tmp_path / "data" / "strategies.yaml"
    yaml_path.write_text(
        """
strategies:
  - id: de-freq
    language: de
    language_label: Allemand
    label: Frequenz
    source: data/german/freq.csv
    columns:
      word: Word
""",
        encoding="utf-8",
    )
    monkeypatch.setattr("app.catalog.STRATEGIES_PATH", yaml_path)
    monkeypatch.setattr("app.catalog.REPO_ROOT", tmp_path)
    spec = strategy_by_id("de-freq")
    assert 500 in spec["sizes"]
    assert 1000 in spec["sizes"]
    assert 2000 in spec["sizes"]
    assert spec["sizes"][-1] == 2500
    assert 3000 not in spec["sizes"]


def test_spoken_csv_is_auto_registered(tmp_path, monkeypatch):
    spoken = tmp_path / "data" / "frequency" / "it.csv"
    spoken.parent.mkdir(parents=True)
    spoken.write_text("Word,Rank,Frequency\nciao,#1,99\ngrazie,#2,80\n", encoding="utf-8")
    yaml_path = tmp_path / "data" / "strategies.yaml"
    yaml_path.write_text("strategies: []\n", encoding="utf-8")
    monkeypatch.setattr("app.catalog.STRATEGIES_PATH", yaml_path)
    monkeypatch.setattr("app.catalog.REPO_ROOT", tmp_path)
    spec = strategy_by_id("spoken-it")
    assert spec["language"] == "it"
    assert spec["available"] == 2
    cards = cards_from_strategy(spec, limit=None, deck_id="it1")
    assert [card.lemma for card in cards] == ["ciao", "grazie"]
