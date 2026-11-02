import pytest
from pathlib import Path

from app.models import Card
from app.services import audio


def test_failed_audio_does_not_leave_a_reusable_partial_file(tmp_path, monkeypatch):
    monkeypatch.setattr(audio, "MEDIA_DIR", tmp_path)
    monkeypatch.setattr(audio, "ensure_dirs", lambda: None)
    card = Card(id="c", lemma="pera", surface="pera", language="pt")
    async def fail(text, path, voice):
        Path(path).write_bytes(b"partial")
        raise RuntimeError("provider stopped")
    monkeypatch.setattr(audio, "_save", fail)
    with pytest.raises(RuntimeError, match="provider stopped"):
        audio.generate_audio(card, "d", "pt")
    assert list((tmp_path / "d").iterdir()) == []
    async def succeed(text, path, voice):
        Path(path).write_bytes(b"complete-audio")
    monkeypatch.setattr(audio, "_save", succeed)
    assert audio.generate_audio(card, "d", "pt") == "/api/media/d/pera.mp3"
    assert (tmp_path / "d/pera.mp3").read_bytes() == b"complete-audio"
