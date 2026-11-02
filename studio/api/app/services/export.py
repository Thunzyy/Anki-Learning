from __future__ import annotations

import csv
import hashlib
import html
import ipaddress
import json
import os
import re
import tempfile
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import unquote, urlsplit, urlunsplit

import httpx

from ..models import Card, Deck
from ..paths import EXPORTS_DIR, ensure_dirs
from ..protocol import qa_deck, ProtocolError

_REMOTE_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
_AUDIO_EXTS = {".mp3", ".wav", ".ogg", ".opus", ".m4a", ".aac"}


def export_csv(deck: Deck) -> Path:
    deck = deck.model_copy(deep=True)
    report = qa_deck(deck)
    if not report.ok:
        raise ProtocolError("export bloqué tant que le QA n'est pas validé")
    ensure_dirs()
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = EXPORTS_DIR / f"{deck.id}_{_slug(deck.name)}.csv"
    fieldnames = [
        "Word",
        "Language",
        "Pronunciation",
        "French",
        "Category",
        "Rank",
        "Phrase",
        "Traduction",
        "Image",
        "Image_Source",
        "Image_Asset_ID",
        "Image_Provenance",
        "Audio",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for index, card in enumerate(deck.cards, start=1):
            writer.writerow({
                "Word": card.surface,
                "Language": deck.language_label or deck.language,
                "Pronunciation": card.ipa,
                "French": card.french,
                "Category": card.category,
                "Rank": f"#{index}",
                "Phrase": card.phrase,
                "Traduction": card.phrase_fr,
                "Image": card.image_url,
                "Image_Source": card.image_source,
                "Image_Asset_ID": card.image_asset_id,
                "Image_Provenance": json.dumps(public_image_provenance(card), ensure_ascii=False),
                "Audio": card.audio_url,
            })
    return path


def export_apkg(deck: Deck, anki_name: str | None = None, *, image_credits: dict[str, dict] | None = None) -> Path:
    import genanki

    deck = deck.model_copy(deep=True)
    report = qa_deck(deck)
    if not report.ok:
        raise ProtocolError("export bloqué tant que le QA n'est pas validé")
    ensure_dirs()
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    model = genanki.Model(
        2199000100,
        "Learning Complete",
        fields=[
            {"name": "Word"},
            {"name": "IPA"},
            {"name": "French"},
            {"name": "Category"},
            {"name": "Phrase"},
            {"name": "Traduction"},
            {"name": "Image"},
        ],
        templates=[
            {
                "name": "L2 vers L1",
                "qfmt": """
<div class="front">
  <div class="word">{{Word}}</div>
  <div class="ipa">{{IPA}}</div>
</div>
""",
                "afmt": """
<div class="back">
  <div class="word">{{Word}}</div>
  <div class="ipa">{{IPA}}</div>
  <hr>
  <div class="fr">{{French}}</div>
  {{#Image}}<div class="pic">{{Image}}</div>{{/Image}}
  {{#Phrase}}<div class="ex">{{Phrase}}<div class="tr">{{Traduction}}</div></div>{{/Phrase}}
  <div class="meta">{{Category}}</div>
</div>
""",
            },
            {
                "name": "L1 vers L2",
                "qfmt": """
<div class="front">
  <div class="fr">{{French}}</div>
  <div class="meta">{{Category}}</div>
</div>
""",
                "afmt": """
<div class="back">
  <div class="fr">{{French}}</div>
  <hr>
  <div class="word">{{Word}}</div>
  <div class="ipa">{{IPA}}</div>
  {{#Image}}<div class="pic">{{Image}}</div>{{/Image}}
  {{#Phrase}}<div class="ex">{{Phrase}}<div class="tr">{{Traduction}}</div></div>{{/Phrase}}
</div>
""",
            },
        ],
        css="""
.card { font-family: Georgia, serif; background:#16110c; color:#efe6d4; text-align:center; padding:28px; }
.word { font-size:34px; color:#efe6d4; }
.ipa { color:#c4a265; font-style:italic; margin-top:6px; }
.fr { font-size:26px; color:#d14a1f; }
.ex { margin-top:16px; text-align:left; border-left:3px solid #c4a265; padding:8px 12px; }
.tr { color:#8a7d6b; margin-top:4px; }
.meta { margin-top:18px; letter-spacing:2px; text-transform:uppercase; font-size:11px; color:#8a7d6b; }
.pic { margin:14px 0; }
.pic img { max-width:220px; max-height:180px; margin:0 auto; display:block; object-fit:contain; }
.image-credit { font:12px sans-serif; color:#b6a894; margin-top:8px; overflow-wrap:anywhere; }
.image-credit a { color:inherit; }
""",
    )
    anki_deck = genanki.Deck(_anki_deck_id(deck.id), anki_name or deck.name)
    media_files: set[str] = set()
    image_cache: dict[str, tuple[str, str]] = {}
    audio_cache: dict[str, tuple[str, str]] = {}
    _prefetch_remote_images([card.image_url for card in deck.cards])
    for card in deck.cards:
        if card.image_url not in image_cache:
            image_cache[card.image_url] = _anki_image(card.image_url)
        image_field, media_path = image_cache[card.image_url]
        if media_path:
            media_files.add(media_path)
            image_field += _image_credit(card, media_path, image_credits)
        if card.audio_url not in audio_cache:
            audio_cache[card.audio_url] = _anki_audio(card.audio_url)
        audio_field, audio_path = audio_cache[card.audio_url]
        if audio_path:
            media_files.add(audio_path)
            image_field += '<div class="audio">' + audio_field + '</div>'
        anki_deck.add_note(genanki.Note(
            model=model,
            # Tied to the card, not to its content: a re-send updates the note
            # in place instead of piling a second copy next to the old one.
            guid=genanki.guid_for(deck.id, card.id),
            fields=[
                card.surface,
                card.ipa,
                card.french,
                card.category,
                card.phrase,
                card.phrase_fr,
                image_field,
            ],
        ))
    path = EXPORTS_DIR / f"{deck.id}_{_slug(deck.name)}.apkg"
    package = genanki.Package(anki_deck)
    package.media_files = sorted(media_files)
    # Keep the historical seven-field model and GUIDs. Media/credits live in
    # Image, already rendered by both backs, so existing imports can update.
    with tempfile.NamedTemporaryFile(dir=EXPORTS_DIR, suffix=".apkg", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        package.write_to_file(str(temporary))
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def _anki_deck_id(deck_id: str) -> int:
    """Stable id per deck. Two decks sharing an id would merge on import."""
    digest = hashlib.sha1(deck_id.encode("utf-8")).hexdigest()[:12]
    return 2_199_000_200 + int(digest, 16) % 1_000_000_000


def _anki_image(url: str) -> tuple[str, str]:
    """Field value and file to bundle. Anki only copies media a field references."""
    raw = (url or "").strip()
    if not raw:
        return "", ""
    if raw.startswith(("http://", "https://")):
        downloaded = _download_remote_image(raw)
        if not downloaded:
            raise ProtocolError("export bloqué : image distante indisponible")
        source = Path(downloaded)
    else:
        source = _local_media(raw)
    path = _package_media(source)
    return _img_tag(path.name), str(path)


def _anki_audio(url: str) -> tuple[str, str]:
    raw = (url or "").strip()
    if not raw:
        return "", ""
    if raw.startswith(("http://", "https://")):
        downloaded = _download_remote_media(raw, audio=True)
        if not downloaded:
            raise ProtocolError("export bloqué : audio distant indisponible")
        source = Path(downloaded)
    else:
        source = _local_media(raw)
    path = _package_media(source)
    return f"[sound:{path.name}]", str(path)


def _local_media(url: str) -> Path:
    from ..paths import MEDIA_DIR
    from ..cache import CACHE_MEDIA
    from ..legacy import AUDIO_DIRS

    roots = {"/api/media/": MEDIA_DIR, "/api/cache-media/": CACHE_MEDIA}
    roots.update({f"/api/legacy-audio/{lang}/": root for lang, root in AUDIO_DIRS.items()})
    for prefix, root in roots.items():
        if not url.startswith(prefix):
            continue
        relative = Path(unquote(url[len(prefix):]))
        path = (root / relative).resolve()
        if relative.is_absolute() or not path.is_relative_to(root.resolve()):
            raise ProtocolError("export bloqué : chemin de média invalide")
        if not path.is_file() or not path.stat().st_size:
            raise ProtocolError(f"export bloqué : média local absent ou vide ({url})")
        return path
    raise ProtocolError("export bloqué : référence média non prise en charge")


def _package_media(source: Path) -> Path:
    """Anki has one global media namespace, even across imported packages."""
    data = source.read_bytes()
    if not data:
        raise ProtocolError("export bloqué : média vide")
    digest = hashlib.sha256(data).hexdigest()
    suffix = source.suffix.lower()
    if suffix == ".jpeg":
        suffix = ".jpg"
    if suffix not in _REMOTE_IMAGE_EXTS | _AUDIO_EXTS | {".svg"}:
        raise ProtocolError("export bloqué : format de média non pris en charge")
    folder = EXPORTS_DIR / "media"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"learning_{digest}{suffix}"
    if not path.is_file() or path.read_bytes() != data:
        with tempfile.NamedTemporaryFile(dir=folder, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(data)
        try:
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    return path


def public_image_provenance(card: Card, sha256: str = "", *, attribution: dict | None = None) -> dict:
    """Public allowlist: never export audit paths, prompts, or local URLs."""
    original = card.image_provenance or {}
    result = {"source": card.image_source or original.get("source") or "unknown"}
    digest = sha256 or original.get("sha256", "")
    if re.fullmatch(r"[0-9a-f]{64}", str(digest)):
        result["sha256"] = digest
    if card.image_asset_id:
        result["asset_id"] = card.image_asset_id
    review = {**(original.get("review") or {}), **(attribution or {})}
    for key in ("origin", "model", "external_source", "author", "title", "attribution", "license", "license_url", "source_page_url", "source_image_url"):
        value = review.get(key) or original.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        if key.endswith("_url"):
            value = _public_url(value)
            if not value:
                continue
        elif re.search(r"[a-zA-Z]:[\\/]|file:|(?:^|\s)/(?:Users|home|tmp)/|\\\\", value):
            continue
        result[key] = value
    return result


def _public_url(value: str) -> str:
    """Export public credit links, never local endpoints or signed queries."""
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower().rstrip('.')
        if parsed.scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
            return ""
        if host == 'localhost' or host.endswith(('.localhost', '.local', '.internal', '.lan', '.test')):
            return ""
        try:
            address = ipaddress.ip_address(host)
            if not address.is_global:
                return ""
        except ValueError:
            if '.' not in host or host.replace('.', '').isdigit() or '%' in host or '\\' in host:
                return ""
        # Queries may contain access tokens and are unnecessary for attribution.
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, '', ''))
    except ValueError:
        return ""


def _image_credit(card: Card, media_path: str, image_credits: dict[str, dict] | None = None) -> str:
    digest = Path(media_path).stem.removeprefix("learning_")
    recorded = (card.image_provenance or {}).get("sha256")
    if recorded and recorded != digest:
        raise ProtocolError(f"export bloqué : empreinte image différente de la provenance ({card.id})")
    metadata = public_image_provenance(card, digest, attribution=(image_credits or {}).get(digest))
    provider_label = "Codex (OpenAI)" if metadata["source"] == "openai" and metadata.get("origin") == "codex_cli" else metadata["source"]
    label = metadata.get("attribution") or metadata.get("author") or metadata.get("external_source") or provider_label
    parts = [html.escape(str(label))]
    for key, title in (("source_page_url", "Source"), ("license_url", metadata.get("license", "Licence"))):
        if metadata.get(key):
            parts.append(f'<a href="{html.escape(metadata[key], quote=True)}">{html.escape(str(title))}</a>')
    if metadata.get("license") and not metadata.get("license_url"):
        parts.append(html.escape(metadata["license"]))
    encoded = html.escape(json.dumps(metadata, ensure_ascii=False, sort_keys=True))
    return '<div class="image-credit">' + ' · '.join(parts) + '</div><span class="image-provenance" style="display:none">' + encoded + '</span>'


def _prefetch_remote_images(urls: list[str]) -> None:
    unique = list(dict.fromkeys(
        (url or "").strip()
        for url in urls
        if (url or "").strip().startswith(("http://", "https://"))
    ))
    if len(unique) < 2:
        return
    workers = min(8, len(unique))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(_download_remote_image, unique))


def _remote_media_name(url: str) -> str:
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    suffix = Path(unquote(url.split("?", 1)[0])).suffix.lower()
    ext = suffix if suffix in _REMOTE_IMAGE_EXTS else ".jpg"
    return f"img_{digest}{ext}"


def _download_remote_image(url: str) -> str:
    return _download_remote_media(url)


def _download_remote_media(url: str, *, audio: bool = False) -> str:
    from ..paths import MEDIA_DIR
    from .images import USER_AGENT

    raw = (url or "").strip()
    if not raw.startswith(("http://", "https://")):
        return ""
    folder = MEDIA_DIR / "anki-remote"
    folder.mkdir(parents=True, exist_ok=True)
    if audio:
        suffix = Path(unquote(urlsplit(raw).path)).suffix.lower()
        if suffix not in _AUDIO_EXTS:
            suffix = ".mp3"
        name = "audio_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16] + suffix
    else:
        name = _remote_media_name(raw)
    path = folder / name
    if path.is_file() and path.stat().st_size > 0:
        if not audio or _valid_audio_bytes(path.read_bytes()):
            return str(path)
    try:
        with httpx.Client(
            timeout=20.0,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept": "audio/*,*/*;q=0.8" if audio else "image/*,*/*;q=0.8"},
        ) as client:
            response = client.get(raw)
            response.raise_for_status()
            data = response.content
            content_type = (getattr(response, "headers", {}) or {}).get("content-type", "").split(';', 1)[0].lower()
        if not data:
            return ""
        if audio and (not _valid_audio_bytes(data) or (content_type and not content_type.startswith('audio/') and content_type not in {'application/octet-stream', 'application/ogg'})):
            return ""
        path.write_bytes(data)
        return str(path)
    except (httpx.HTTPError, OSError):
        return ""


def _valid_audio_bytes(data: bytes) -> bool:
    if data.startswith((b'ID3', b'OggS', b'fLaC')):
        return True
    if data[:4] == b'RIFF' and data[8:12] == b'WAVE':
        return True
    if data[4:8] == b'ftyp':
        return True
    # MPEG/ADTS frame sync (MP3/AAC without an ID3 tag).
    return len(data) >= 4 and data[0] == 0xff and data[1] & 0xe0 == 0xe0


def _img_tag(src: str) -> str:
    name = unicodedata.normalize("NFC", src).replace('"', "&quot;")
    return f'<img src="{name}">'


def _slug(name: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in name).strip("-")
    return cleaned or "deck"
