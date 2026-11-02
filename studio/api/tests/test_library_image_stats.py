import pytest
from fastapi.testclient import TestClient

from app import store
from app.main import app
from app.models import Card, Deck


def test_images_are_deduplicated_across_languages_and_ignore_history(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DECKS_DIR", tmp_path)
    for language in ("en", "es"):
        deck = Deck(id=language, name=language, language=language, cards=[
            Card(id="a", lemma="house", surface="house", language=language,
                 image_url="/api/media/shared.png", image_history=[{"url": "/old.png"}]),
            Card(id="b", lemma="apple", surface="apple", language=language,
                 image_url=f"/api/media/{language}.png"),
            Card(id="c", lemma="stone", surface="stone", language=language),
            Card(id="d", lemma="tree", surface="tree", language=language,
                 image_url="/hidden.png", skip_image=True),
            Card(id="e", lemma="the", surface="the", language=language,
                 category="Grammaire", image_url="/grammar.png"),
        ])
        (tmp_path / f"{language}.json").write_text(deck.model_dump_json(), encoding="utf-8")
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    response = TestClient(app).get("/api/library/images/stats")
    assert response.status_code == 200
    assert response.json() == {"images": 3, "illustrated_cards": 4}
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


def test_empty_library_has_zero_images(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DECKS_DIR", tmp_path)
    assert store.library_image_stats() == {"images": 0, "illustrated_cards": 0}


@pytest.mark.parametrize("raw", ["", "not json", '{"id":"broken"}'])
def test_unreadable_deck_reports_unavailable_instead_of_partial_total(tmp_path, monkeypatch, raw):
    monkeypatch.setattr(store, "DECKS_DIR", tmp_path)
    (tmp_path / "broken.json").write_text(raw, encoding="utf-8")
    response = TestClient(app).get("/api/library/images/stats")
    assert response.status_code == 503
