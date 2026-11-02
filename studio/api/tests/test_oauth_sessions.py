import json
from pathlib import Path

from app.auth.sessions import (
    parse_claude,
    parse_codex,
    parse_grok,
    provider_status,
    resolve_secret,
)


def test_parse_claude_reports_subscription_not_token(tmp_path: Path):
    path = tmp_path / ".credentials.json"
    path.write_text(json.dumps({
        "claudeAiOauth": {
            "accessToken": "sk-ant-oat01-SECRETTOKENVALUE",
            "refreshToken": "sk-ant-ort01-SECRETREFRESH",
            "expiresAt": 1777337012345,
            "subscriptionType": "max",
        }
    }), encoding="utf-8")
    status = parse_claude(path)
    assert status.connected is True
    assert status.source == "cli-oauth"
    assert status.account == "max"
    assert "SECRET" not in json.dumps(status.model_dump())


def test_parse_codex_hides_jwt(tmp_path: Path):
    path = tmp_path / "auth.json"
    path.write_text(json.dumps({
        "auth_mode": "chatgpt",
        "tokens": {
            "access_token": "eyJhbGciOiSECRET",
            "refresh_token": "rt.1.SECRET",
            "account_id": "ae960728-eeee-eeee-eeee-eeeeeeeeeeee",
        },
        "last_refresh": "2026-08-14T00:00:00Z",
    }), encoding="utf-8")
    status = parse_codex(path)
    assert status.connected is True
    dumped = json.dumps(status.model_dump())
    assert "eyJ" not in dumped
    assert "SECRET" not in dumped
    assert status.account.endswith("eeee") or "ae960728" in status.account


def test_parse_grok_uses_email(tmp_path: Path):
    path = tmp_path / "auth.json"
    path.write_text(json.dumps({
        "https://auth.x.ai::client": {
            "key": "eyJ0eXAiOiSECRETJWT",
            "auth_mode": "oidc",
            "email": "user@example.com",
            "refresh_token": "refresh-secret",
            "expires_at": "2026-08-15T00:00:00Z",
            "oidc_issuer": "https://auth.x.ai",
        }
    }), encoding="utf-8")
    status = parse_grok(path)
    assert status.connected is True
    assert status.account == "user@example.com"
    assert "SECRETJWT" not in json.dumps(status.model_dump())


def test_missing_file_is_disconnected(tmp_path: Path):
    status = parse_claude(tmp_path / "nope.json")
    assert status.connected is False
    assert status.source == "none"


def test_resolve_prefers_api_key_over_oauth():
    secret = resolve_secret(
        provider="claude",
        api_key="sk-ant-api03-FROMSETTINGS",
        oauth_token="sk-ant-oat01-FROMOAUTH",
    )
    assert secret.value == "sk-ant-api03-FROMSETTINGS"
    assert secret.source == "api-key"


def test_resolve_falls_back_to_oauth():
    secret = resolve_secret(
        provider="claude",
        api_key="",
        oauth_token="sk-ant-oat01-FROMOAUTH",
    )
    assert secret.value == "sk-ant-oat01-FROMOAUTH"
    assert secret.source == "cli-oauth"


def test_auth_status_never_leaks_bearer_prefixes(tmp_path, monkeypatch):
    from app.auth import sessions
    from app.main import auth_status

    path = tmp_path / "credentials.json"
    path.write_text(json.dumps({"claudeAiOauth": {
        "accessToken": "sk-ant-oat01-TEST", "refreshToken": "rt.1.TEST",
    }}))
    monkeypatch.setattr(sessions, "PROVIDER_FILES", {
        "claude": path, "codex": tmp_path / "missing-codex", "grok": tmp_path / "missing-grok",
    })
    payload = auth_status()
    blob = json.dumps(payload)
    assert "sk-ant-oat" not in blob
    assert "eyJ" not in blob
    assert "rt.1." not in blob
