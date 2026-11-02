from pathlib import Path

import pytest

from app.models import Card, Deck, DeckFeatures, Settings


class AssetServer:
    def __init__(self, *, fail_upload=False, fail_final=False, ambiguous_final=False, fail_delete=False):
        self.assets = {9: {"id": 9, "name": "anglais.zip"},
                       10: {"id": 10, "name": "anglais.zip-notes.apkg"}}
        self.calls = []
        self.fail_upload = fail_upload
        self.fail_final = fail_final
        self.ambiguous_final = ambiguous_final
        self.fail_delete = fail_delete

    def __call__(self, method, url, **kwargs):
        from urllib.parse import parse_qs, urlparse
        from app.services.publish import PublishError
        self.calls.append((method, url, kwargs))
        if method == "POST":
            if self.fail_upload:
                raise PublishError("upload interrupted")
            chunks = list(kwargs["content"])
            assert all(len(chunk) <= 1024 * 1024 for chunk in chunks)
            assert int(kwargs["headers"]["Content-Length"]) == sum(map(len, chunks))
            assert kwargs["timeout"].write >= 600
            self.assets[100] = {"id": 100, "name": parse_qs(urlparse(url).query)["name"][0],
                                "size": sum(map(len, chunks)), "state": "uploaded"}
            return dict(self.assets[100])
        asset_id = int(url.rsplit("/", 1)[-1])
        if method == "GET":
            return dict(self.assets[asset_id])
        if method == "PATCH":
            if asset_id == 100 and self.fail_final:
                if self.ambiguous_final:
                    self.assets[100]["name"] = kwargs["json"]["name"]
                raise PublishError("rename response lost")
            self.assets[asset_id]["name"] = kwargs["json"]["name"]
            return dict(self.assets[asset_id])
        if method == "DELETE":
            assert self.assets[100]["name"] == "anglais.zip", "must confirm new final before deletion"
            if self.fail_delete:
                raise PublishError("backup cleanup unavailable")
            del self.assets[asset_id]
            return {}
        raise AssertionError(method)


def test_replacement_upload_failure_never_touches_old_assets(tmp_path, monkeypatch):
    from app.services import publish
    server = AssetServer(fail_upload=True)
    monkeypatch.setattr(publish, "_github", server)
    path = tmp_path / "file.zip"
    path.write_bytes(b"new")
    with pytest.raises(publish.PublishError, match="upload interrupted"):
        publish._replace_asset("owner/repo", {"id": 42, "assets": list(server.assets.values())}, "anglais.zip", path)
    assert server.assets[9]["name"] == "anglais.zip"
    assert [call[0] for call in server.calls] == ["POST"]


def test_replacement_rename_failure_restores_old_name_without_deleting(tmp_path, monkeypatch):
    from app.services import publish
    server = AssetServer(fail_final=True)
    monkeypatch.setattr(publish, "_github", server)
    path = tmp_path / "file.zip"
    path.write_bytes(b"new")
    with pytest.raises(publish.PublishError, match="noms précédents restaurés"):
        publish._replace_asset("owner/repo", {"id": 42, "assets": list(server.assets.values())}, "anglais.zip", path)
    assert server.assets[9]["name"] == "anglais.zip"
    assert not any(call[0] == "DELETE" for call in server.calls)
    assert server.assets[100]["name"].startswith("upload-")


def test_replacement_streams_and_preserves_similarly_named_sibling(tmp_path, monkeypatch):
    from app.services import publish
    server = AssetServer()
    monkeypatch.setattr(publish, "_github", server)
    monkeypatch.setattr(Path, "read_bytes", lambda self: pytest.fail("never buffer a whole release asset"))
    path = tmp_path / "file.zip"
    path.write_bytes(b"x" * (2 * 1024 * 1024 + 19))
    result = publish._replace_asset("owner/repo", {"id": 42, "assets": list(server.assets.values())}, "anglais.zip", path, also="anglais")
    assert result["name"] == "anglais.zip"
    assert 9 not in server.assets and server.assets[10]["name"] == "anglais.zip-notes.apkg"


def test_replacement_recovers_successful_final_rename_after_response_loss(tmp_path, monkeypatch):
    from app.services import publish
    server = AssetServer(fail_final=True, ambiguous_final=True)
    monkeypatch.setattr(publish, "_github", server)
    path = tmp_path / "file.zip"
    path.write_bytes(b"new")
    result = publish._replace_asset("owner/repo", {"id": 42, "assets": list(server.assets.values())}, "anglais.zip", path)
    assert result["name"] == "anglais.zip" and 9 not in server.assets


def test_replacement_cleanup_failure_keeps_backup_and_reports_warning(tmp_path, monkeypatch):
    from app.services import publish
    server = AssetServer(fail_delete=True)
    monkeypatch.setattr(publish, "_github", server)
    path = tmp_path / "file.zip"
    path.write_bytes(b"new")
    result = publish._replace_asset("owner/repo", {"id": 42, "assets": list(server.assets.values())}, "anglais.zip", path)
    assert result["name"] == "anglais.zip" and result["cleanup_warnings"]
    assert server.assets[9]["name"].startswith("backup-")


def _deck(**kwargs) -> Deck:
    data = dict(
        id="legacy-en-ngsl",
        name="NGSL — General Service List",
        language="en",
        language_label="Anglais",
        features=DeckFeatures(phrase=True, phonetic=True, audio=False, image=False),
        cards=[
            Card(
                id="c1",
                lemma="house",
                surface="house",
                language="en",
                french="maison",
                category="Nom",
                ipa="/haʊs/",
                phrase="I need this house right now.",
                phrase_fr="J'ai besoin de cette maison maintenant.",
            )
        ],
    )
    data.update(kwargs)
    return Deck(**data)


def test_release_asset_uses_language_folder():
    from app.services.publish import release_asset_name

    assert release_asset_name(_deck()) == "anglais__ngsl-general-service-list.apkg"


def test_portuguese_asset_matches_github_ascii_normalization(monkeypatch):
    from app.services import publish
    deck = _deck(name="Portugais brésilien 1000", language="pt", language_label="Portugais (Brésil)")
    name = "portugais-bresil__portugais-bresilien-1000.apkg"
    assert publish.release_asset_name(deck) == name
    monkeypatch.setattr(publish, "_read_release", lambda settings: {"assets": [{"name": name}]})
    assert publish.release_status(deck, Settings())["published"]


def test_release_status_does_not_match_a_sibling_by_substring(monkeypatch):
    from app.services import publish
    monkeypatch.setattr(publish, "_read_release", lambda settings: {
        "assets": [{"name": "anglais__ngsl-general-service-list-extra.apkg"}]})
    assert not publish.release_status(_deck(), Settings())["published"]


def test_anki_deck_nests_under_language_folder():
    from app.services.publish import anki_deck_name

    assert anki_deck_name(_deck()) == "Anglais::NGSL — General Service List"


def test_release_status_is_published_when_the_asset_exists(monkeypatch):
    from app.services import publish

    def fake_gh(method, url, **kwargs):
        assert method == "GET"
        return {
            "html_url": "https://github.com/Thunzyy/Anki-flashcard/releases/tag/decks",
            "assets": [{
                "name": "anglais__ngsl-general-service-list.apkg",
                "browser_download_url": "https://example.com/anglais__ngsl.apkg",
            }],
        }

    monkeypatch.setattr(publish, "_github", fake_gh)
    status = publish.release_status(_deck(), Settings())
    assert status["published"] is True
    assert status["url"] == "https://github.com/Thunzyy/Anki-flashcard/releases/tag/decks"
    assert status["download"] == "https://example.com/anglais__ngsl.apkg"


def test_release_status_is_unpublished_when_the_asset_is_missing(monkeypatch):
    from app.services import publish

    monkeypatch.setattr(
        publish,
        "_github",
        lambda *a, **k: {
            "html_url": "https://github.com/Thunzyy/Anki-flashcard/releases/tag/decks",
            "assets": [],
        },
    )
    status = publish.release_status(_deck(), Settings())
    assert status["published"] is False
    assert status["url"].endswith("/releases/tag/decks")
    assert status["download"] == ""


def test_release_status_is_unpublished_when_github_has_no_release(monkeypatch):
    from app.services import publish

    def boom(*_a, **_k):
        raise publish.PublishError("GitHub 404: Not Found")

    monkeypatch.setattr(publish, "_github", boom)
    status = publish.release_status(_deck(), Settings())
    assert status["published"] is False
    assert "releases/tag/decks" in status["url"]


def test_publish_release_uploads_named_asset(monkeypatch, tmp_path):
    from app.services import publish

    calls = []

    def fake_gh(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if method == "GET" and url.endswith("/releases/tags/decks"):
            return {"id": 42, "html_url": "https://github.com/Thunzyy/Anki-flashcard/releases/tag/decks", "assets": []}
        if method == "POST" and "assets" in url:
            assert b"".join(kwargs["content"]) == b"PK"
            return {"id": 100, "size": 2, "state": "uploaded"}
        if method == "PATCH":
            return {"id": int(url.rsplit("/", 1)[-1]), "name": kwargs["json"]["name"], "browser_download_url": "https://example.com/final.apkg"}
        raise AssertionError(f"unexpected {method} {url}")

    monkeypatch.setattr(publish, "_github", fake_gh)
    monkeypatch.setattr(publish, "_github_token", lambda: "tok")
    path = tmp_path / "deck.apkg"
    path.write_bytes(b"PK")
    result = publish.publish_release(_deck(), path, Settings())
    assert result["tag"] == "decks"
    assert result["asset"] == "anglais__ngsl-general-service-list.apkg"
    assert any(method == "PATCH" and kwargs["json"]["name"] == "anglais__ngsl-general-service-list.apkg" for method, _, kwargs in calls)


def test_send_to_anki_creates_language_folder_then_imports(monkeypatch, tmp_path):
    from app.services import publish

    seen = []

    def fake_invoke(action, params=None, url=""):
        seen.append((action, params or {}))
        if action == "version":
            return 6
        if action == "createDeck":
            return 1
        if action == "importPackage":
            return True
        if action == "findCards":
            return [1712000000001, 1712000000002]
        return None

    monkeypatch.setattr(publish, "anki_invoke", fake_invoke)
    path = tmp_path / "deck.apkg"
    path.write_bytes(b"PK")
    result = publish.send_to_anki(_deck(), path, Settings())
    assert result["deck"] == "Anglais::NGSL — General Service List"
    assert result["cards"] == 2
    assert ("createDeck", {"deck": "Anglais"}) in seen
    assert ("createDeck", {"deck": "Anglais::NGSL — General Service List"}) in seen
    assert any(action == "importPackage" for action, _ in seen)


def test_send_to_anki_raises_when_package_is_refused(monkeypatch, tmp_path):
    from app.services import publish

    def fake_invoke(action, params=None, url=""):
        return False if action == "importPackage" else 6

    monkeypatch.setattr(publish, "anki_invoke", fake_invoke)
    path = tmp_path / "deck.apkg"
    path.write_bytes(b"PK")
    with pytest.raises(publish.AnkiConnectError):
        publish.send_to_anki(_deck(), path, Settings())


def test_send_to_anki_raises_when_deck_stays_empty(monkeypatch, tmp_path):
    from app.services import publish

    def fake_invoke(action, params=None, url=""):
        if action == "findCards":
            return []
        return True if action == "importPackage" else 6

    monkeypatch.setattr(publish, "anki_invoke", fake_invoke)
    path = tmp_path / "deck.apkg"
    path.write_bytes(b"PK")
    with pytest.raises(publish.AnkiConnectError):
        publish.send_to_anki(_deck(), path, Settings())


def test_anki_url_replaces_the_legacy_port():
    from app.services.publish import anki_url

    # 8766 sits in a range Windows reserves, so nothing can ever listen there.
    assert anki_url(Settings(anki_connect_url="http://127.0.0.1:8766")) == "http://127.0.0.1:8765"
    assert anki_url(Settings(anki_connect_url="")) == "http://127.0.0.1:8765"
    assert anki_url(Settings(anki_connect_url="http://192.168.1.4:8765/")) == "http://192.168.1.4:8765"


def test_anki_status_reports_down(monkeypatch):
    from app.services import publish

    def boom(*_a, **_k):
        raise publish.AnkiConnectError("connection refused")

    monkeypatch.setattr(publish, "anki_invoke", boom)
    status = publish.anki_status(Settings())
    assert status["ok"] is False
    assert "8765" in status["url"]


def test_language_zip_asset_uses_folder_slug():
    from app.services.publish import language_zip_asset_name

    assert language_zip_asset_name("Anglais") == "anglais.zip"
    assert language_zip_asset_name("Portugais (Brésil)") == "portugais-bresil.zip"


def test_pack_language_zip_nests_apkgs_in_folder(tmp_path):
    from zipfile import ZipFile

    from app.services.publish import pack_language_zip

    first = tmp_path / "a.apkg"
    first.write_bytes(b"PK-one")
    second = tmp_path / "b.apkg"
    second.write_bytes(b"PK-two")
    dest = tmp_path / "anglais.zip"
    pack_language_zip(
        "Anglais",
        [("NGSL — General Service List.apkg", first), ("Grammaire A1.apkg", second)],
        dest,
    )
    with ZipFile(dest) as archive:
        names = set(archive.namelist())
        assert names == {
            "Anglais/NGSL — General Service List.apkg",
            "Anglais/Grammaire A1.apkg",
        }
        assert archive.read("Anglais/NGSL — General Service List.apkg") == b"PK-one"


def test_publish_language_uploads_zip_and_replaces_previous(monkeypatch, tmp_path):
    from app.services import publish

    calls = []
    first = tmp_path / "one.apkg"
    first.write_bytes(b"PK1")
    dest = tmp_path / "anglais.zip"

    def fake_gh(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if method == "GET" and url.endswith("/releases/tags/decks"):
            return {
                "id": 42,
                "html_url": "https://github.com/Thunzyy/Anki-flashcard/releases/tag/decks",
                "assets": [{"id": 9, "name": "anglais.zip"}],
            }
        if method == "DELETE":
            return {}
        if method == "POST" and "assets" in url:
            payload = b"".join(kwargs["content"])
            assert payload.startswith(b"PK")
            return {"id": 100, "size": len(payload), "state": "uploaded"}
        if method == "PATCH":
            return {"id": int(url.rsplit("/", 1)[-1]), "name": kwargs["json"]["name"], "browser_download_url": "https://example.com/anglais.zip"}
        raise AssertionError(f"unexpected {method} {url}")

    monkeypatch.setattr(publish, "_github", fake_gh)
    monkeypatch.setattr(publish, "_github_token", lambda: "tok")
    result = publish.publish_language_release(
        "Anglais",
        [("NGSL.apkg", first)],
        Settings(),
        dest=dest,
    )
    assert result["asset"] == "anglais.zip"
    assert dest.exists()
    assert any(method == "PATCH" and kwargs["json"]["name"] == "anglais.zip" for method, _, kwargs in calls)
    assert [method for method, _, _ in calls] == ["GET", "POST", "PATCH", "PATCH", "DELETE"]
    assert any(method == "DELETE" and url.endswith("/releases/assets/9") for method, url, _ in calls)


def test_language_release_status_is_published_when_zip_exists(monkeypatch):
    from app.services import publish

    monkeypatch.setattr(
        publish,
        "_github",
        lambda *a, **k: {
            "html_url": "https://github.com/Thunzyy/Anki-flashcard/releases/tag/decks",
            "assets": [{
                "name": "anglais.zip",
                "browser_download_url": "https://example.com/anglais.zip",
            }],
        },
    )
    status = publish.language_release_status("Anglais", Settings())
    assert status["published"] is True
    assert status["asset"] == "anglais.zip"
    assert status["download"] == "https://example.com/anglais.zip"


def test_language_release_status_is_unpublished_when_zip_is_missing(monkeypatch):
    from app.services import publish

    monkeypatch.setattr(
        publish,
        "_github",
        lambda *a, **k: {
            "html_url": "https://github.com/Thunzyy/Anki-flashcard/releases/tag/decks",
            "assets": [{"name": "italien.zip"}],
        },
    )
    status = publish.language_release_status("Anglais", Settings())
    assert status["published"] is False
    assert status["url"].endswith("/releases/tag/decks")


def test_release_status_is_published_when_language_zip_exists(monkeypatch):
    from app.services import publish

    monkeypatch.setattr(
        publish,
        "_github",
        lambda *a, **k: {
            "html_url": "https://github.com/Thunzyy/Anki-flashcard/releases/tag/decks",
            "assets": [{
                "name": "anglais.zip",
                "browser_download_url": "https://example.com/anglais.zip",
            }],
        },
    )
    status = publish.release_status(_deck(), Settings())
    assert status["published"] is True
    assert status["download"] == "https://example.com/anglais.zip"


def test_publish_language_separate_uploads_each_apkg(monkeypatch, tmp_path):
    from app.services import publish

    calls = []
    first = tmp_path / "one.apkg"
    first.write_bytes(b"PK1")
    second = tmp_path / "two.apkg"
    second.write_bytes(b"PK2")

    def fake_gh(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if method == "GET" and url.endswith("/releases/tags/decks"):
            return {
                "id": 42,
                "html_url": "https://github.com/Thunzyy/Anki-flashcard/releases/tag/decks",
                "assets": [],
            }
        if method == "POST" and "assets" in url:
            return {"id": 100 + len(calls), "size": len(b"".join(kwargs["content"])), "state": "uploaded"}
        if method == "PATCH":
            return {"id": int(url.rsplit("/", 1)[-1]), "name": kwargs["json"]["name"], "browser_download_url": "https://example.com/final.apkg"}
        raise AssertionError(f"unexpected {method} {url}")

    monkeypatch.setattr(publish, "_github", fake_gh)
    monkeypatch.setattr(publish, "_github_token", lambda: "tok")
    result = publish.publish_language_release(
        "Anglais",
        [("NGSL.apkg", first), ("Grammaire A1.apkg", second)],
        Settings(),
        mode="separate",
    )
    assert result["mode"] == "separate"
    assert result["files"] == 2
    names = [kwargs["json"]["name"] for method, url, kwargs in calls if method == "PATCH"]
    assert names == ["anglais__ngsl.apkg", "anglais__grammaire-a1.apkg"]
    assert not any(url.endswith("anglais.zip") or "anglais.zip" in url for _, url, _ in calls)


def test_list_release_assets_classifies_folder_and_deck(monkeypatch):
    from app.services import publish

    monkeypatch.setattr(
        publish,
        "_github",
        lambda *a, **k: {
            "html_url": "https://github.com/Thunzyy/Anki-flashcard/releases/tag/decks",
            "assets": [
                {"id": 1, "name": "anglais.zip", "browser_download_url": "https://example.com/anglais.zip", "size": 10},
                {"id": 2, "name": "anglais__ngsl.apkg", "browser_download_url": "https://example.com/ngsl.apkg", "size": 4},
            ],
        },
    )
    listing = publish.list_release_assets(Settings())
    assert listing["tag"] == "decks"
    assert listing["repo"] == "Thunzyy/Anki-Learning"
    kinds = {item["name"]: item["kind"] for item in listing["assets"]}
    assert kinds["anglais.zip"] == "folder"
    assert kinds["anglais__ngsl.apkg"] == "deck"


def test_delete_release_asset_removes_named_file(monkeypatch):
    from app.services import publish

    calls = []

    def fake_gh(method, url, **kwargs):
        calls.append((method, url))
        if method == "GET":
            return {
                "html_url": "https://github.com/Thunzyy/Anki-flashcard/releases/tag/decks",
                "assets": [{"id": 9, "name": "anglais.zip"}],
            }
        if method == "DELETE" and url.endswith("/releases/assets/9"):
            return {}
        raise AssertionError(f"unexpected {method} {url}")

    monkeypatch.setattr(publish, "_github", fake_gh)
    monkeypatch.setattr(publish, "_github_token", lambda: "tok")
    result = publish.delete_release_asset("anglais.zip", Settings())
    assert result["ok"] is True
    assert result["deleted"] == "anglais.zip"
    assert any(method == "DELETE" for method, _url in calls)


def test_delete_release_asset_missing_raises(monkeypatch):
    from app.services import publish

    monkeypatch.setattr(
        publish,
        "_github",
        lambda *a, **k: {"assets": [{"id": 1, "name": "italien.zip"}]},
    )
    monkeypatch.setattr(publish, "_github_token", lambda: "tok")
    with pytest.raises(publish.PublishError):
        publish.delete_release_asset("anglais.zip", Settings())


def test_language_release_status_is_published_when_separate_decks_exist(monkeypatch):
    from app.services import publish

    monkeypatch.setattr(
        publish,
        "_github",
        lambda *a, **k: {
            "html_url": "https://github.com/Thunzyy/Anki-flashcard/releases/tag/decks",
            "assets": [{
                "name": "anglais__ngsl-general-service-list.apkg",
                "browser_download_url": "https://example.com/ngsl.apkg",
            }],
        },
    )
    status = publish.language_release_status("Anglais", Settings())
    assert status["published"] is True
    assert status["kind"] == "decks"


def test_unpublish_language_deletes_zip_and_prefixed_apkgs(monkeypatch):
    from app.services import publish

    deleted = []

    def fake_gh(method, url, **kwargs):
        if method == "GET":
            return {
                "assets": [
                    {"id": 1, "name": "anglais.zip"},
                    {"id": 2, "name": "anglais__ngsl.apkg"},
                    {"id": 3, "name": "italien.zip"},
                ]
            }
        if method == "DELETE":
            deleted.append(url)
            return {}
        raise AssertionError(f"unexpected {method} {url}")

    monkeypatch.setattr(publish, "_github", fake_gh)
    monkeypatch.setattr(publish, "_github_token", lambda: "tok")
    result = publish.unpublish_language("Anglais", Settings())
    assert result["ok"] is True
    assert set(result["deleted"]) == {"anglais.zip", "anglais__ngsl.apkg"}
    assert len(deleted) == 2
