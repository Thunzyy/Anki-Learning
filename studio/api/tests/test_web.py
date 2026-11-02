import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.web import mount_frontend


def test_frontend_serves_build_without_hiding_api_or_exposing_parent(tmp_path):
    web = tmp_path / "dist"
    web.mkdir()
    (web / "index.html").write_text("<h1>Learning</h1>", encoding="utf-8")
    (web / "assets").mkdir()
    (web / "assets" / "app.js").write_text("console.log('ready')", encoding="utf-8")
    (tmp_path / "settings.json").write_text('{"secret":"test"}', encoding="utf-8")
    app = FastAPI()

    @app.get("/api/health")
    def health():
        return {"ok": True}

    mount_frontend(app, str(web))
    client = TestClient(app)
    assert client.get("/").text == "<h1>Learning</h1>"
    assert client.get("/assets/app.js").status_code == 200
    assert client.get("/api/health").json() == {"ok": True}
    assert client.get("/api/unknown").status_code == 404
    assert client.get("/settings.json").status_code == 404
    assert client.get("/%2e%2e/settings.json").status_code == 404


def test_frontend_is_optional_and_bad_build_fails_early(tmp_path):
    app = FastAPI()
    mount_frontend(app, "")
    assert TestClient(app).get("/").status_code == 404
    with pytest.raises(RuntimeError, match="Frontend build missing"):
        mount_frontend(app, str(tmp_path))
