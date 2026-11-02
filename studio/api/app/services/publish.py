from __future__ import annotations

import os
import subprocess
import unicodedata
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

import httpx

from ..models import Deck, Settings


class PublishError(Exception):
    pass


class AnkiConnectError(Exception):
    pass


DEFAULT_REPO = "Thunzyy/Anki-Learning"
DEFAULT_RELEASE = "decks"
# AnkiConnect listens on 8765; the Learning API keeps 8700 to leave that port free.
DEFAULT_ANKI = "http://127.0.0.1:8765"
# 8766 shipped as the default for a while, inside a port range Windows reserves.
LEGACY_ANKI_URLS = {"http://127.0.0.1:8766", "http://localhost:8766"}


def anki_url(settings: Settings) -> str:
    url = (settings.anki_connect_url or "").strip().rstrip("/")
    if not url or url in LEGACY_ANKI_URLS:
        return DEFAULT_ANKI
    return url


def language_folder(deck: Deck) -> str:
    label = (deck.language_label or "").strip()
    if label:
        return label
    from ..languages import find_language
    found = find_language(deck.language)
    return (found.get("label") or deck.language or "Deck").strip()


def _slug(name: str) -> str:
    # GitHub normalizes accented upload names; use the same stable ASCII key
    # for upload, replacement and publication status comparisons.
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in name)
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-") or "deck"


def release_asset_name(deck: Deck) -> str:
    # GitHub release assets cannot contain slashes; language prefix still groups them.
    folder = _slug(language_folder(deck))
    return f"{folder}__{_slug(deck.name)}.apkg"


def language_zip_asset_name(folder: str) -> str:
    return f"{_slug(folder)}.zip"


def pack_language_zip(folder: str, packages: list[tuple[str, Path]], dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(dest, "w", compression=ZIP_DEFLATED) as archive:
        for filename, path in packages:
            safe = Path(filename).name.replace("\\", "-").replace("/", "-").strip() or "deck.apkg"
            if not safe.lower().endswith(".apkg"):
                safe = f"{safe}.apkg"
            archive.write(path, f"{folder}/{safe}")
    return dest


def anki_deck_name(deck: Deck) -> str:
    return f"{language_folder(deck)}::{deck.name}"


def _github_token() -> str:
    for key in ("GITHUB_TOKEN", "GH_TOKEN"):
        if os.environ.get(key):
            return os.environ[key]
    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        token = (result.stdout or "").strip()
        if token:
            return token
    except OSError:
        pass
    raise PublishError("aucun jeton GitHub — lance `gh auth login`")


def _github(method: str, url: str, **kwargs):
    optional_auth = kwargs.pop("optional_auth", False)
    timeout = kwargs.pop("timeout", 90.0)
    headers = dict(kwargs.pop("headers", None) or {})
    token = ""
    try:
        token = _github_token()
    except PublishError:
        if not optional_auth:
            raise
    if token:
        headers["Authorization"] = f"Bearer {token}"
    headers.setdefault("Accept", "application/vnd.github+json")
    headers.setdefault("X-GitHub-Api-Version", "2022-11-28")
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        response = client.request(method, url, headers=headers, **kwargs)
        if response.status_code >= 400:
            raise PublishError(f"GitHub {response.status_code}: {response.text[:300]}")
        if response.status_code == 204 or not response.content:
            return {}
        return response.json()


def _repo_tag(settings: Settings) -> tuple[str, str, str]:
    repo = (settings.github_repo or DEFAULT_REPO).strip()
    tag = (settings.github_release or DEFAULT_RELEASE).strip()
    return repo, tag, f"https://github.com/{repo}/releases/tag/{tag}"


def _get_or_create_release(settings: Settings) -> dict:
    repo, tag, page = _repo_tag(settings)
    api = f"https://api.github.com/repos/{repo}"
    try:
        release = _github("GET", f"{api}/releases/tags/{tag}")
    except PublishError as exc:
        if "404" not in str(exc):
            raise
        release = _github(
            "POST",
            f"{api}/releases",
            json={
                "tag_name": tag,
                "name": "Learning decks",
                "body": "Decks Anki publiés depuis Learning, groupés par langue (`Anglais/…`, `Italien/…`).",
                "draft": False,
                "prerelease": False,
            },
        )
    if not isinstance(release, dict):
        release = {"html_url": page, "assets": []}
    release.setdefault("html_url", page)
    return release


def _replace_asset(repo: str, release: dict, asset: str, path: Path, also: str = "") -> dict:
    """Upload first; preserve the previous asset until the new name is confirmed.

    ``also`` is an explicit legacy filename, never a substring selector.
    Failed/ambiguous operations leave recoverable temporary assets in place.
    """
    api = f"https://api.github.com/repos/{repo}"
    previous = [dict(item) for item in release.get("assets") or []
                if item.get("name") == asset or (also and item.get("name") == also)]
    token = uuid4().hex
    temporary_name = f"upload-{token}-{asset}"
    path = Path(path)
    size = path.stat().st_size
    with path.open("rb") as content:
        uploaded = _github(
            "POST",
            f"https://uploads.github.com/repos/{repo}/releases/{release['id']}/assets?name={quote(temporary_name, safe='')}",
            content=iter(lambda: content.read(1024 * 1024), b""),
            headers={"Content-Type": "application/octet-stream", "Content-Length": str(size)},
            timeout=httpx.Timeout(connect=30.0, read=600.0, write=600.0, pool=30.0),
        )
    if not isinstance(uploaded, dict) or not uploaded.get("id") or uploaded.get("size") != size or uploaded.get("state") != "uploaded":
        raise PublishError("GitHub : nouvel asset incomplet ; ancien fichier conservé")
    uploaded_url = f"{api}/releases/assets/{uploaded['id']}"
    renamed = []
    try:
        for item in previous:
            # Record before PATCH: a timeout can hide a successful remote rename.
            renamed.append(item)
            _github("PATCH", f"{api}/releases/assets/{item['id']}",
                    json={"name": f"backup-{token}-{item['name']}"})
        final = _github("PATCH", uploaded_url, json={"name": asset})
        if not isinstance(final, dict) or final.get("id") != uploaded["id"] or final.get("name") != asset:
            raise PublishError("GitHub : nom final du nouvel asset non confirmé")
    except (PublishError, httpx.HTTPError) as exc:
        # A transport failure may have occurred after GitHub committed the rename.
        try:
            final = _github("GET", uploaded_url)
        except (PublishError, httpx.HTTPError):
            final = {}
        if not isinstance(final, dict) or final.get("id") != uploaded["id"] or final.get("name") != asset:
            rollback_errors = []
            for item in reversed(renamed):
                try:
                    restored = _github("PATCH", f"{api}/releases/assets/{item['id']}", json={"name": item["name"]})
                    if not isinstance(restored, dict) or restored.get("name") != item["name"]:
                        rollback_errors.append(str(item["id"]))
                except (PublishError, httpx.HTTPError):
                    rollback_errors.append(str(item["id"]))
            detail = (f" ; restauration à vérifier pour assets {', '.join(rollback_errors)}"
                      if rollback_errors else " ; noms précédents restaurés")
            raise PublishError(f"Remplacement GitHub interrompu : {exc}{detail}. Aucun ancien fichier supprimé.") from exc
    warnings = []
    for item in previous:
        try:
            _github("DELETE", f"{api}/releases/assets/{item['id']}")
        except (PublishError, httpx.HTTPError) as exc:
            warnings.append(f"Nouvel asset publié ; sauvegarde {item['id']} conservée : {exc}")
    final["cleanup_warnings"] = warnings
    old_ids = {item["id"] for item in previous}
    release["assets"] = [item for item in release.get("assets") or [] if item.get("id") not in old_ids] + [final]
    return final


def publish_release(deck: Deck, path: Path, settings: Settings) -> dict:
    repo, tag, page = _repo_tag(settings)
    asset = release_asset_name(deck)
    release = _get_or_create_release(settings)
    uploaded = _replace_asset(repo, release, asset, path, also=f"{_slug(deck.name)}.apkg")
    return {
        "ok": True,
        "tag": tag,
        "asset": asset,
        "url": release.get("html_url") or page,
        "download": uploaded.get("browser_download_url") or "",
        "warnings": uploaded.get("cleanup_warnings", []),
    }


def _separate_asset_name(folder: str, filename: str) -> str:
    stem = Path(filename).stem
    return f"{_slug(folder)}__{_slug(stem)}.apkg"


def publish_language_release(
    folder: str,
    packages: list[tuple[str, Path]],
    settings: Settings,
    dest: Path | None = None,
    mode: str = "folder",
) -> dict:
    if not packages:
        raise PublishError("aucun deck à publier")
    if mode not in ("folder", "separate"):
        raise PublishError(f"mode inconnu: {mode}")
    repo, tag, page = _repo_tag(settings)
    release = _get_or_create_release(settings)
    if mode == "separate":
        uploaded: list[dict] = []
        for filename, path in packages:
            asset = _separate_asset_name(folder, filename)
            item = _replace_asset(repo, release, asset, path)
            uploaded.append({
                "asset": asset,
                "download": item.get("browser_download_url") or "",
                "warnings": item.get("cleanup_warnings", []),
            })
        first = uploaded[0]
        return {
            "ok": True,
            "tag": tag,
            "asset": first["asset"],
            "url": release.get("html_url") or page,
            "download": first["download"],
            "files": len(packages),
            "mode": "separate",
            "assets": [item["asset"] for item in uploaded],
            "warnings": [warning for item in uploaded for warning in item["warnings"]],
        }
    asset = language_zip_asset_name(folder)
    if dest is None:
        from ..paths import EXPORTS_DIR, ensure_dirs
        ensure_dirs()
        dest = EXPORTS_DIR / asset
    pack_language_zip(folder, packages, dest)
    uploaded_zip = _replace_asset(repo, release, asset, dest)
    return {
        "ok": True,
        "tag": tag,
        "asset": asset,
        "url": release.get("html_url") or page,
        "download": uploaded_zip.get("browser_download_url") or "",
        "files": len(packages),
        "mode": "folder",
        "warnings": uploaded_zip.get("cleanup_warnings", []),
    }


def _read_release(settings: Settings) -> dict | None:
    repo, tag, _page = _repo_tag(settings)
    try:
        release = _github(
            "GET",
            f"https://api.github.com/repos/{repo}/releases/tags/{tag}",
            optional_auth=True,
        )
    except PublishError:
        return None
    return release if isinstance(release, dict) else None


def release_status(deck: Deck, settings: Settings) -> dict:
    _repo, tag, page = _repo_tag(settings)
    asset = release_asset_name(deck)
    zip_name = language_zip_asset_name(language_folder(deck))
    release = _read_release(settings)
    if release is None:
        return {"published": False, "url": page, "download": "", "asset": asset, "tag": tag}
    html = release.get("html_url") or page
    slug = _slug(deck.name)
    zip_hit = None
    for existing in release.get("assets") or []:
        name = existing.get("name") or ""
        if name in {asset, f"{slug}.apkg"}:
            return {
                "published": True,
                "url": html,
                "download": existing.get("browser_download_url") or "",
                "asset": name,
                "tag": tag,
            }
        if name == zip_name:
            zip_hit = existing
    if zip_hit:
        return {
            "published": True,
            "url": html,
            "download": zip_hit.get("browser_download_url") or "",
            "asset": zip_name,
            "tag": tag,
        }
    return {"published": False, "url": html, "download": "", "asset": asset, "tag": tag}


def asset_kind(name: str) -> str:
    lowered = name.lower()
    if lowered.endswith(".zip"):
        return "folder"
    if lowered.endswith(".apkg"):
        return "deck"
    return "other"


def _language_assets(folder: str, release: dict) -> tuple[dict | None, list[dict]]:
    zip_name = language_zip_asset_name(folder)
    prefix = f"{_slug(folder)}__"
    zip_hit = None
    decks: list[dict] = []
    for existing in release.get("assets") or []:
        name = existing.get("name") or ""
        if name == zip_name:
            zip_hit = existing
        elif name.startswith(prefix) and name.lower().endswith(".apkg"):
            decks.append(existing)
    return zip_hit, decks


def _summarize_asset(existing: dict) -> dict:
    name = existing.get("name") or ""
    return {
        "name": name,
        "kind": asset_kind(name),
        "download": existing.get("browser_download_url") or "",
        "size": existing.get("size") or 0,
    }


def language_release_status(folder: str, settings: Settings) -> dict:
    _repo, tag, page = _repo_tag(settings)
    asset = language_zip_asset_name(folder)
    empty = {
        "published": False,
        "url": page,
        "download": "",
        "asset": asset,
        "tag": tag,
        "kind": "",
        "assets": [],
    }
    release = _read_release(settings)
    if release is None:
        return empty
    html = release.get("html_url") or page
    zip_hit, decks = _language_assets(folder, release)
    found = ([zip_hit] if zip_hit else []) + decks
    items = [_summarize_asset(item) for item in found]
    if zip_hit:
        return {
            "published": True,
            "url": html,
            "download": zip_hit.get("browser_download_url") or "",
            "asset": zip_hit.get("name") or asset,
            "tag": tag,
            "kind": "folder",
            "assets": items,
        }
    if decks:
        first = decks[0]
        return {
            "published": True,
            "url": html,
            "download": first.get("browser_download_url") or "",
            "asset": first.get("name") or "",
            "tag": tag,
            "kind": "decks",
            "files": len(decks),
            "assets": items,
        }
    return {**empty, "url": html}


def list_release_assets(settings: Settings) -> dict:
    repo, tag, page = _repo_tag(settings)
    release = _read_release(settings)
    if release is None:
        return {"ok": True, "repo": repo, "tag": tag, "url": page, "assets": []}
    html = release.get("html_url") or page
    assets = []
    for existing in release.get("assets") or []:
        name = existing.get("name") or ""
        assets.append({
            "id": existing.get("id"),
            "name": name,
            "kind": asset_kind(name),
            "download": existing.get("browser_download_url") or "",
            "size": existing.get("size") or 0,
        })
    return {"ok": True, "repo": repo, "tag": tag, "url": html, "assets": assets}


def delete_release_asset(name: str, settings: Settings) -> dict:
    repo, tag, _page = _repo_tag(settings)
    release = _github("GET", f"https://api.github.com/repos/{repo}/releases/tags/{tag}")
    if not isinstance(release, dict):
        raise PublishError(f"asset introuvable: {name}")
    for existing in release.get("assets") or []:
        if (existing.get("name") or "") == name:
            _github("DELETE", f"https://api.github.com/repos/{repo}/releases/assets/{existing['id']}")
            return {"ok": True, "deleted": name, "tag": tag}
    raise PublishError(f"asset introuvable: {name}")


def unpublish_language(folder: str, settings: Settings) -> dict:
    repo, tag, _page = _repo_tag(settings)
    release = _github("GET", f"https://api.github.com/repos/{repo}/releases/tags/{tag}")
    if not isinstance(release, dict):
        return {"ok": True, "deleted": [], "tag": tag}
    zip_name = language_zip_asset_name(folder)
    prefix = f"{_slug(folder)}__"
    deleted: list[str] = []
    for existing in release.get("assets") or []:
        name = existing.get("name") or ""
        if name == zip_name or (name.startswith(prefix) and name.lower().endswith(".apkg")):
            _github("DELETE", f"https://api.github.com/repos/{repo}/releases/assets/{existing['id']}")
            deleted.append(name)
    return {"ok": True, "deleted": deleted, "tag": tag}


def anki_invoke(action: str, params: dict | None = None, url: str = "") -> object:
    target = (url or DEFAULT_ANKI).rstrip("/")
    payload: dict = {"action": action, "version": 6}
    if params:
        payload["params"] = params
    try:
        with httpx.Client(timeout=120.0) as client:
            response = client.post(target, json=payload)
            response.raise_for_status()
            body = response.json()
    except httpx.ConnectError as exc:
        raise AnkiConnectError(
            f"aucune réponse sur {target} — ouvre Anki avec le module AnkiConnect installé"
        ) from exc
    except Exception as exc:
        raise AnkiConnectError(str(exc) or exc.__class__.__name__) from exc
    if isinstance(body, dict) and body.get("error"):
        raise AnkiConnectError(str(body["error"]))
    if isinstance(body, dict) and "result" in body:
        return body["result"]
    return body


def anki_status(settings: Settings) -> dict:
    url = anki_url(settings)
    try:
        version = anki_invoke("version", url=url)
        return {"ok": True, "url": url, "version": version}
    except AnkiConnectError as exc:
        return {"ok": False, "url": url, "error": str(exc)}


def send_to_anki(deck: Deck, path: Path, settings: Settings) -> dict:
    url = anki_url(settings)
    folder = language_folder(deck)
    full = anki_deck_name(deck)
    anki_invoke("version", url=url)
    anki_invoke("createDeck", {"deck": folder}, url=url)
    anki_invoke("createDeck", {"deck": full}, url=url)
    target = str(Path(path).resolve()).replace("\\", "/")
    if anki_invoke("importPackage", {"path": target}, url=url) is False:
        raise AnkiConnectError(
            f"Anki a refusé le paquet « {Path(path).name} ». "
            "Ferme la fenêtre d'import restée ouverte, puis réessaie."
        )
    imported = anki_invoke("findCards", {"query": f'deck:"{full}"'}, url=url)
    count = len(imported) if isinstance(imported, list) else 0
    if deck.cards and not count:
        raise AnkiConnectError(
            f"Anki a accepté l'import mais le deck « {full} » est vide. "
            "Vérifie qu'aucun profil différent n'est ouvert."
        )
    return {"ok": True, "deck": full, "url": url, "cards": count}
