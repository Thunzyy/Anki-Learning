import json
import hashlib
import zipfile
from pathlib import Path

import httpx
import pytest

from app.models import Card, Deck, DeckFeatures

JPEG = b"\xff\xd8\xffFAKEJPEG"


class _FakeImageClient:
    fetches: list[str] = []
    content = JPEG
    error: Exception | None = None

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url):
        type(self).fetches.append(url)
        if self.error:
            raise self.error

        class _Resp:
            content = _FakeImageClient.content

            def raise_for_status(self):
                pass

        return _Resp()


def _patch_remote_download(tmp_path, monkeypatch, error=None):
    from app import paths
    from app.services import export

    _FakeImageClient.fetches = []
    _FakeImageClient.content = JPEG
    _FakeImageClient.error = error
    monkeypatch.setattr(paths, "MEDIA_DIR", tmp_path / "media")
    monkeypatch.setattr(export.httpx, "Client", _FakeImageClient)
    return export


def _deck(deck_id: str, name: str) -> Deck:
    return Deck(
        id=deck_id,
        name=name,
        language="pt",
        language_label="Portugais",
        features=DeckFeatures(phrase=True, phonetic=True, audio=False, image=False),
        cards=[
            Card(
                id="c1",
                lemma="casa",
                surface="casa",
                language="pt",
                french="maison",
                category="Nom",
                ipa="/ˈka.zɐ/",
                phrase="A casa dela é muito grande.",
                phrase_fr="Sa maison est vraiment très grande.",
            )
        ],
    )


def test_anki_deck_ids_differ_between_decks():
    from app.services.export import _anki_deck_id

    ids = {_anki_deck_id(f"legacy-pt-{n}") for n in (1000, 2000, 3000, 4000, "full")}
    assert len(ids) == 5
    assert _anki_deck_id("legacy-pt-1000") == _anki_deck_id("legacy-pt-1000")


def test_note_guid_survives_a_content_change(tmp_path, monkeypatch):
    """Re-sending an improved deck must update notes, not duplicate them."""
    import genanki

    from app import paths
    from app.services import export

    monkeypatch.setattr(paths, "EXPORTS_DIR", tmp_path)
    monkeypatch.setattr(export, "EXPORTS_DIR", tmp_path)
    captured = []

    class Spy(genanki.Note):
        def __init__(self, *args, **kwargs):
            captured.append(kwargs.get("guid"))
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(genanki, "Note", Spy)
    deck = _deck("legacy-pt-1000", "Portugais 1000")
    export.export_apkg(deck)
    media = tmp_path / "media" / "legacy-pt-1000"
    media.mkdir(parents=True)
    (media / "casa.jpg").write_bytes(JPEG)
    monkeypatch.setattr(paths, "MEDIA_DIR", tmp_path / "media")
    deck.cards[0].image_url = "/api/media/legacy-pt-1000/casa.jpg"
    deck.cards[0].phrase = "A casa dela fica perto do mercado."
    deck.cards[0].phrase_fr = "Sa maison se trouve près du marché."
    export.export_apkg(deck)

    assert len(captured) == 2
    assert captured[0] == captured[1]


def test_image_field_carries_the_img_tag_so_anki_copies_the_media(tmp_path, monkeypatch):
    from app import paths
    from app.services import export

    media = tmp_path / "media" / "legacy-pt-1000"
    media.mkdir(parents=True)
    (media / "casa.jpg").write_bytes(b"\xff\xd8\xff")
    monkeypatch.setattr(paths, "MEDIA_DIR", tmp_path / "media")
    monkeypatch.setattr(export, "EXPORTS_DIR", tmp_path / "exports")

    field, path = export._anki_image("/api/media/legacy-pt-1000/casa.jpg")
    name = "learning_" + hashlib.sha256(b"\xff\xd8\xff").hexdigest() + ".jpg"
    assert field == f'<img src="{name}">'
    assert Path(path).name == name
    assert export._anki_image("") == ("", "")


def test_remote_image_is_downloaded_so_anki_shows_a_file_not_a_url(tmp_path, monkeypatch):
    export = _patch_remote_download(tmp_path, monkeypatch)

    field, path = export._anki_image("https://example.com/photo.jpg")

    assert field.startswith('<img src="')
    assert "https://" not in field
    assert Path(path).read_bytes() == JPEG
    assert _FakeImageClient.fetches == ["https://example.com/photo.jpg"]


def test_failed_remote_image_does_not_leave_a_clickable_url(tmp_path, monkeypatch):
    from app.protocol import ProtocolError

    export = _patch_remote_download(tmp_path, monkeypatch, error=httpx.ConnectError("offline"))

    with pytest.raises(ProtocolError, match="image distante indisponible"):
        export._anki_image("https://example.com/missing.jpg")


def test_remote_image_is_not_fetched_twice(tmp_path, monkeypatch):
    export = _patch_remote_download(tmp_path, monkeypatch)
    url = "https://example.com/photo.jpg"

    first, path = export._anki_image(url)
    assert _FakeImageClient.fetches == [url]
    _FakeImageClient.fetches.clear()
    second, again = export._anki_image(url)

    assert first == second
    assert path == again
    assert _FakeImageClient.fetches == []


def test_export_apkg_bundles_the_downloaded_image(tmp_path, monkeypatch):
    from app import paths
    from app.services import export

    _patch_remote_download(tmp_path, monkeypatch)
    monkeypatch.setattr(paths, "EXPORTS_DIR", tmp_path / "exports")
    monkeypatch.setattr(export, "EXPORTS_DIR", tmp_path / "exports")
    deck = _deck("legacy-pt-1000", "Portugais 1000")
    deck.cards[0].image_url = "https://example.com/photo.jpg"

    path = export.export_apkg(deck)

    with zipfile.ZipFile(path) as archive:
        media = json.loads(archive.read("media"))
        assert any(name.endswith(".jpg") for name in media.values())
        file_id = next(key for key, name in media.items() if name.endswith(".jpg"))
        assert archive.read(file_id) == JPEG


def test_export_apkg_writes_the_nested_deck_name(tmp_path, monkeypatch):
    from app import paths
    from app.services import export

    monkeypatch.setattr(paths, "EXPORTS_DIR", tmp_path)
    monkeypatch.setattr(export, "EXPORTS_DIR", tmp_path)
    path = export.export_apkg(_deck("legacy-pt-1000", "Portugais 1000"), anki_name="Portugais::Portugais 1000")
    assert path.exists()
    with zipfile.ZipFile(path) as archive:
        assert "collection.anki2" in archive.namelist()
