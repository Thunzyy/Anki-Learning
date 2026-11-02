from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from pydantic import BaseModel, Field


HOME = Path.home()

PROVIDER_FILES = {
    "claude": HOME / ".claude" / ".credentials.json",
    "codex": HOME / ".codex" / "auth.json",
    "grok": HOME / ".grok" / "auth.json",
}

LOGIN_COMMANDS = {
    "claude": ["claude", "auth", "login"],
    "codex": ["codex", "login"],
    "grok": ["grok", "login"],
}

PROVIDER_LABELS = {
    "claude": "Claude (Anthropic)",
    "codex": "Codex (ChatGPT)",
    "grok": "Grok Build (xAI)",
}

ALIASES = {
    "anthropic": "claude",
    "openai": "codex",
    "xai": "grok",
    "claude": "claude",
    "codex": "codex",
    "grok": "grok",
}


class SessionStatus(BaseModel):
    provider: str
    label: str
    connected: bool = False
    source: str = "none"
    account: str = ""
    expires_at: str = ""
    path: str = ""
    login_command: str = ""


class ResolvedSecret(BaseModel):
    value: str
    source: str
    extra_headers: dict[str, str] = Field(default_factory=dict)


def canonical(provider: str) -> str:
    return ALIASES.get(provider, provider)


def _load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def parse_claude(path: Path) -> SessionStatus:
    status = SessionStatus(
        provider="claude",
        label=PROVIDER_LABELS["claude"],
        path=str(path),
        login_command=" ".join(LOGIN_COMMANDS["claude"]),
    )
    data = _load_json(path)
    if not data:
        return status
    oauth = data.get("claudeAiOauth") or {}
    token = str(oauth.get("accessToken") or "").strip()
    if not token:
        return status
    status.connected = True
    status.source = "cli-oauth"
    status.account = str(oauth.get("subscriptionType") or "claude")
    expires = oauth.get("expiresAt")
    if expires:
        status.expires_at = str(expires)
    return status


def parse_codex(path: Path) -> SessionStatus:
    status = SessionStatus(
        provider="codex",
        label=PROVIDER_LABELS["codex"],
        path=str(path),
        login_command=" ".join(LOGIN_COMMANDS["codex"]),
    )
    data = _load_json(path)
    if not data:
        return status
    tokens = data.get("tokens") or {}
    token = str(tokens.get("access_token") or "").strip()
    if not token:
        return status
    account = str(tokens.get("account_id") or "").strip()
    status.connected = True
    status.source = "cli-oauth"
    status.account = f"chatgpt:{account[:8]}" if account else str(data.get("auth_mode") or "chatgpt")
    status.expires_at = str(data.get("last_refresh") or "")
    return status


def parse_grok(path: Path) -> SessionStatus:
    status = SessionStatus(
        provider="grok",
        label=PROVIDER_LABELS["grok"],
        path=str(path),
        login_command=" ".join(LOGIN_COMMANDS["grok"]),
    )
    data = _load_json(path)
    if not data:
        return status
    block = _grok_block(data)
    token = str(block.get("key") or block.get("access_token") or "").strip()
    if not token:
        return status
    status.connected = True
    status.source = "cli-oauth"
    status.account = str(block.get("email") or block.get("user_id") or "grok")
    status.expires_at = str(block.get("expires_at") or "")
    return status


def _grok_block(data: dict) -> dict:
    if "key" in data or "access_token" in data:
        return data
    for value in data.values():
        if isinstance(value, dict) and (value.get("key") or value.get("access_token") or value.get("email")):
            return value
    return {}


PARSERS = {
    "claude": parse_claude,
    "codex": parse_codex,
    "grok": parse_grok,
}


def read_oauth_token(provider: str, path: Path | None = None) -> tuple[str, dict[str, str]]:
    name = canonical(provider)
    target = path or PROVIDER_FILES.get(name)
    if not target:
        return "", {}
    data = _load_json(target)
    if not data:
        return "", {}
    if name == "claude":
        token = str((data.get("claudeAiOauth") or {}).get("accessToken") or "").strip()
        return token, {}
    if name == "codex":
        tokens = data.get("tokens") or {}
        token = str(tokens.get("access_token") or "").strip()
        account = str(tokens.get("account_id") or "").strip()
        headers = {"ChatGPT-Account-Id": account} if account else {}
        return token, headers
    if name == "grok":
        block = _grok_block(data)
        token = str(block.get("key") or block.get("access_token") or "").strip()
        return token, {}
    return "", {}


def provider_status(provider: str, path: Path | None = None) -> SessionStatus:
    name = canonical(provider)
    parser = PARSERS.get(name)
    if not parser:
        return SessionStatus(provider=name, label=name, source="none")
    return parser(path or PROVIDER_FILES[name])


def scan_all() -> list[SessionStatus]:
    return [provider_status(name) for name in ("claude", "codex", "grok")]


def resolve_secret(provider: str, api_key: str = "", oauth_token: str = "", extra_headers: dict[str, str] | None = None) -> ResolvedSecret:
    key = (api_key or "").strip()
    if key:
        return ResolvedSecret(value=key, source="api-key", extra_headers={})
    token = (oauth_token or "").strip()
    if token:
        return ResolvedSecret(value=token, source="cli-oauth", extra_headers=extra_headers or {})
    return ResolvedSecret(value="", source="none")


def resolve_for_provider(provider: str, settings) -> ResolvedSecret:
    name = canonical(provider)
    api_key = ""
    if name == "claude":
        api_key = getattr(settings, "anthropic_api_key", "") or os.getenv("ANTHROPIC_API_KEY", "")
    elif name == "codex":
        api_key = getattr(settings, "openai_api_key", "") or os.getenv("OPENAI_API_KEY", "")
    elif name == "grok":
        api_key = getattr(settings, "xai_api_key", "") or os.getenv("XAI_API_KEY", "")
    token, headers = read_oauth_token(name)
    return resolve_secret(name, api_key=api_key, oauth_token=token, extra_headers=headers)


def start_official_login(provider: str) -> dict:
    name = canonical(provider)
    command = list(LOGIN_COMMANDS.get(name) or [])
    if not command:
        raise ValueError(f"fournisseur inconnu: {provider}")
    executable = shutil.which(command[0]) or shutil.which(f"{command[0]}.exe") or shutil.which(f"{command[0]}.cmd")
    if not executable:
        return {
            "started": False,
            "provider": name,
            "command": " ".join(command),
            "hint": f"Installe le CLI « {command[0]} », puis relance la connexion.",
        }
    args = [executable, *command[1:]]
    creationflags = 0
    if sys.platform == "win32":
        creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    subprocess.Popen(args, creationflags=creationflags)
    return {
        "started": True,
        "provider": name,
        "command": " ".join(command),
        "hint": "Valide la fenêtre du CLI, puis reviens ici. La session officielle sera détectée automatiquement.",
    }
