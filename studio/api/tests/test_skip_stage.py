from fastapi.testclient import TestClient

from app import cache, main, store
from app.models import Settings


def test_cannot_skip_to_images_via_api(tmp_path, monkeypatch):
    # Keep the test independent of a running server and the user's local decks.
    monkeypatch.setattr(store, "DECKS_DIR", tmp_path)
    monkeypatch.setattr(store, "ensure_dirs", lambda: None)
    monkeypatch.setattr(main, "load_settings", lambda: Settings(default_ai_provider="local"))
    monkeypatch.setattr(cache, "apply_deck", lambda deck: None)
    monkeypatch.setattr(main, "fill_missing", lambda deck, settings: None)
    client = TestClient(main.app)
    created = client.post(
        "/api/decks",
        json={
            "name": "skip-test",
            "language": "pt",
            "words": ["zzqxvlum"],
        },
    )
    assert created.status_code == 200
    deck_id = created.json()["id"]
    skipped = client.post(f"/api/decks/{deck_id}/run/images")
    assert skipped.status_code == 409
    assert "translate" in skipped.json()["detail"]
