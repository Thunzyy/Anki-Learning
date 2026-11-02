"""Validate real APKG bytes and SQLite references, independently of genanki internals."""
import csv
import hashlib
import html
import json
import re
import sqlite3
import zipfile
from pathlib import Path

import genanki
import pytest

from app.models import Card, Deck, DeckFeatures
from app.protocol import ProtocolError
from app.services import export


@pytest.fixture
def media_roots(tmp_path, monkeypatch):
    from app import cache, legacy, paths

    media = tmp_path / "media"
    cached = tmp_path / "cache"
    legacy_audio = tmp_path / "legacy-audio"
    for folder in (media, cached, legacy_audio):
        folder.mkdir()
    monkeypatch.setattr(paths, "MEDIA_DIR", media)
    monkeypatch.setattr(cache, "CACHE_MEDIA", cached)
    monkeypatch.setattr(legacy, "AUDIO_DIRS", {"pt": legacy_audio})
    monkeypatch.setattr(export, "EXPORTS_DIR", tmp_path / "exports")
    (tmp_path / "exports").mkdir()
    return media, cached, legacy_audio


def deck(*cards, deck_id="stable-deck"):
    return Deck(
        id=deck_id, name="Corrected deck", language="pt", language_label="Portugais",
        features=DeckFeatures(phrase=False, phonetic=False, image=True, audio=False),
        cards=list(cards),
    )


def card(card_id="stable-card", **fields):
    return Card(id=card_id, lemma="casa", surface="casa", language="pt",
                french="maison", category="Nom", **fields)


def write(root, relative, data):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def unpack(path):
    with zipfile.ZipFile(path) as archive:
        media = {name: archive.read(key) for key, name in json.loads(archive.read("media")).items()}
        connection = sqlite3.connect(":memory:")
        connection.deserialize(archive.read("collection.anki2"))
    try:
        notes = connection.execute("SELECT guid,mid,flds FROM notes ORDER BY guid").fetchall()
        cards = connection.execute("SELECT did,ord FROM cards ORDER BY ord").fetchall()
        models, decks = connection.execute("SELECT models,decks FROM col").fetchone()
        return notes, cards, json.loads(models), json.loads(decks), media
    finally:
        connection.close()


def test_same_basename_different_bytes_survive_and_equal_bytes_deduplicate(media_roots):
    media, cache, _ = media_roots
    first, second = b"\xff\xd8\xffONE", b"\xff\xd8\xffTWO"
    write(media, "one/yeah.jpg", first)
    write(media, "two/yeah.jpg", second)
    write(cache, "pt/shared.jpg", first)
    d = deck(card("a", image_url="/api/media/one/yeah.jpg"),
             card("b", image_url="/api/media/two/yeah.jpg"),
             card("c", image_url="/api/cache-media/pt/shared.jpg"))
    before = d.model_dump()
    notes, cards, _, _, bundled = unpack(export.export_apkg(d))
    assert len(notes) == 3 and len(cards) == 6
    assert len(bundled) == 2
    assert set(bundled.values()) == {first, second}
    for guid, _, fields in notes:
        name = re.search(r'<img src="([^"]+)"', fields).group(1)
        expected = second if guid == genanki.guid_for(d.id, "b") else first
        assert bundled[name] == expected
        assert hashlib.sha256(expected).hexdigest() in name
    assert d.model_dump() == before
    other = deck(card(image_url="/api/media/two/yeah.jpg"), deck_id="other")
    other_media = unpack(export.export_apkg(other))[-1]
    assert set(other_media) <= set(bundled)


def test_audio_local_cache_and_legacy_are_referenced_and_bundled(media_roots):
    media, cache, old = media_roots
    for root, path, data in ((media, "one/a.mp3", b"ID3one"), (cache, "pt/a.mp3", b"ID3two"), (old, "a.mp3", b"ID3three")):
        write(root, path, data)
    d = deck(card("a", audio_url="/api/media/one/a.mp3"),
             card("b", audio_url="/api/cache-media/pt/a.mp3"),
             card("c", audio_url="/api/legacy-audio/pt/a.mp3"))
    notes, _, models, _, bundled = unpack(export.export_apkg(d))
    assert set(bundled.values()) == {b"ID3one", b"ID3two", b"ID3three"}
    assert len(bundled) == 3
    for _, _, fields in notes:
        reference = re.search(r"\[sound:([^\]]+)\]", fields).group(1)
        assert reference in bundled
        assert fields.split("\x1f")[0] == "casa"
    assert all("{{Image}}" in template["afmt"] for template in next(iter(models.values()))["tmpls"])


def test_public_credit_and_provenance_keep_license_without_private_paths(media_roots):
    media, _, _ = media_roots
    write(media, "one/anatomy.jpg", b"\xff\xd8\xffanatomy")
    c = card(image_url="/api/media/one/anatomy.jpg", image_source="manual",
             image_provenance={"evidence": r"C:\Users\private\audit.json", "review": {
                 "external_source": "openstax", "author": "OpenStax / Rice University",
                 "attribution": "Figure 27.2 — OpenStax <unchanged>", "license": "CC BY-NC-SA 4.0",
                 "license_url": "https://creativecommons.org/licenses/by-nc-sa/4.0/",
                 "source_page_url": "https://openstax.org/books/anatomy",
                 "license_evidence_path": r"C:\Users\private\license.html"}})
    d = deck(c)
    notes, _, _, _, _ = unpack(export.export_apkg(d))
    fields = notes[0][2]
    assert "OpenStax" in fields and "CC BY-NC-SA 4.0" in fields
    assert "https://creativecommons.org/licenses/by-nc-sa/4.0/" in fields
    assert "&lt;unchanged&gt;" in fields
    assert "private" not in fields and "C:" not in fields
    encoded = re.search(r'class="image-provenance" style="display:none">(.*?)</span>', fields).group(1)
    public = json.loads(html.unescape(encoded))
    assert public["source"] == "manual" and public["external_source"] == "openstax"
    assert public["sha256"] == hashlib.sha256(b"\xff\xd8\xffanatomy").hexdigest()
    with export.export_csv(d).open(encoding="utf8", newline="") as handle:
        csv_provenance = next(csv.DictReader(handle))["Image_Provenance"]
    assert "private" not in csv_provenance and "OpenStax" in csv_provenance
    c.image_source = "generated"
    c.image_provenance = {"source": "generated", "local_path": r"C:\Users\private\image.jpg"}
    assert export.public_image_provenance(c) == {"source": "generated"}


def test_reexport_retains_historical_model_schema_guids_deck_and_template_ordinals(media_roots):
    media, _, _ = media_roots
    write(media, "one/image.jpg", b"\xff\xd8\xffimage")
    d = deck(card())
    first = unpack(export.export_apkg(d))
    d.cards[0].french = "maison corrigée"
    d.cards[0].image_url = "/api/media/one/image.jpg"
    second = unpack(export.export_apkg(d))
    assert first[0][0][:2] == second[0][0][:2] == (genanki.guid_for(d.id, d.cards[0].id), 2199000100)
    assert first[1] == second[1] == [(export._anki_deck_id(d.id), 0), (export._anki_deck_id(d.id), 1)]
    assert [f["name"] for f in second[2]["2199000100"]["flds"]] == ["Word", "IPA", "French", "Category", "Phrase", "Traduction", "Image"]
    assert "maison corrigée" in second[0][0][2]


@pytest.mark.parametrize("url", ["/api/media/gone/image.jpg", "/api/cache-media/pt/gone.jpg", "/api/media/../../private.jpg", "file:///C:/private.jpg"])
def test_missing_or_unsupported_media_fails_without_replacing_previous_package(media_roots, url):
    d = deck(card())
    path = export.export_apkg(d)
    before = path.read_bytes()
    d.cards[0].image_url = url
    with pytest.raises(ProtocolError, match="export bloqué"):
        export.export_apkg(d)
    assert path.read_bytes() == before


def test_missing_audio_fails_instead_of_publishing_silent_reference(media_roots):
    d = deck(card(audio_url="/api/legacy-audio/pt/gone.mp3"))
    with pytest.raises(ProtocolError, match="média local absent"):
        export.export_apkg(d)


def test_media_must_match_recorded_provenance_before_export(media_roots):
    media, _, _ = media_roots
    write(media, "one/image.jpg", b"\xff\xd8\xffchanged")
    d = deck(card(image_url="/api/media/one/image.jpg", image_provenance={"sha256": "0" * 64}))
    with pytest.raises(ProtocolError, match="empreinte image différente"):
        export.export_apkg(d)


def test_reviewed_credit_sidecar_is_bound_to_bytes_without_mutating_deck(media_roots):
    media, _, _ = media_roots
    raw = b"\xff\xd8\xffexternal"
    write(media, "one/external.jpg", raw)
    d = deck(card(image_url="/api/media/one/external.jpg", image_source="pexels"))
    before = d.model_dump()
    credits = {hashlib.sha256(raw).hexdigest(): {
        "author": "Kelly E", "license": "Pexels License", "source_page_url": "https://www.pexels.com/photo/15545037/",
        "license_url": "https://www.pexels.com/license/", "evidence": r"C:\private\receipt.json"},
        "0" * 64: {"author": "Unrelated author"}}
    fields = unpack(export.export_apkg(d, image_credits=credits))[0][0][2]
    assert "Kelly E" in fields and "Pexels License" in fields
    assert "Unrelated author" not in fields and "private" not in fields
    assert d.model_dump() == before


def test_codex_origin_and_model_are_public_but_paths_and_prompts_are_not(media_roots):
    media, _, _ = media_roots
    write(media, "one/codex.png", b"\x89PNG\r\n\x1a\nimage")
    c = card(image_url="/api/media/one/codex.png", image_source="openai", image_provenance={
        "origin": "codex_cli", "model": "gpt-image-1", "prompt": "private prompt",
        "original_path": r"C:\Users\private\generated.png"})
    fields = unpack(export.export_apkg(deck(c)))[0][0][2]
    assert "Codex (OpenAI)" in fields and "codex_cli" in fields and "gpt-image-1" in fields
    assert "private" not in fields and "prompt" not in fields
    c.image_provenance['model'] = r'C:\Users\private\model'
    assert 'model' not in export.public_image_provenance(c)


@pytest.mark.parametrize('url', ['http://localhost:8700/api/media/a.jpg', 'http://127.0.0.1/a.jpg', 'http://10.0.0.1/a.jpg', 'http://192.168.1.2/a.jpg', 'http://[::1]/a.jpg', 'http://[fc00::1]/a.jpg', 'https://user:secret@example.com/a', 'http://pcfixe/a', 'http://files.internal/a'])
def test_public_attribution_rejects_private_and_credential_urls(url):
    c = card(image_provenance={'review': {'source_page_url': url}})
    assert 'source_page_url' not in export.public_image_provenance(c)


def test_public_attribution_strips_query_secrets_and_fragment():
    c = card(image_provenance={'review': {'source_page_url': 'https://example.com/photo?token=SECRET&sig=SECRET#private'}})
    assert export.public_image_provenance(c)['source_page_url'] == 'https://example.com/photo'


@pytest.mark.parametrize('payload,mime', [(b'<html>error</html>', 'text/html'), (b'{"error":"quota"}', 'application/json'), (b'not audio', 'audio/mpeg'), (b'ID3pretend', 'text/html')])
def test_remote_audio_rejects_html_json_and_non_audio_even_with_http_200(media_roots, monkeypatch, payload, mime):
    import httpx
    transport = httpx.MockTransport(lambda req: httpx.Response(200, content=payload, headers={'Content-Type': mime}))
    client = httpx.Client
    monkeypatch.setattr(export.httpx, 'Client', lambda **kwargs: client(transport=transport, **kwargs))
    with pytest.raises(ProtocolError, match='audio distant indisponible'):
        export._anki_audio('https://example.com/error.mp3')


@pytest.mark.parametrize('payload,mime,ext', [(b'ID3audio', 'audio/mpeg', 'mp3'), (b'RIFFxxxxWAVEaudio', 'audio/wav', 'wav'), (b'OggSaudio', 'audio/ogg', 'ogg')])
def test_remote_audio_supported_headers_are_bundled(media_roots, monkeypatch, payload, mime, ext):
    import httpx
    transport = httpx.MockTransport(lambda req: httpx.Response(200, content=payload, headers={'Content-Type': mime}))
    client = httpx.Client
    monkeypatch.setattr(export.httpx, 'Client', lambda **kwargs: client(transport=transport, **kwargs))
    field, path = export._anki_audio(f'https://example.com/speech.{ext}')
    assert field == '[sound:' + Path(path).name + ']'
    assert Path(path).read_bytes() == payload
