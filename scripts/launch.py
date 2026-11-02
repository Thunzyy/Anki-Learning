"""Install Learning locally and serve its production frontend and API."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import urllib.error
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "studio" / "web"
VENV = ROOT / ".venv"
STATE = VENV / ".learning-install.json"


def digest(paths: list[Path]) -> str:
    result = hashlib.sha256()
    for path in sorted(paths):
        result.update(path.relative_to(ROOT).as_posix().encode())
        result.update(path.read_bytes())
    return result.hexdigest()


def run(args: list[str], cwd: Path = ROOT) -> None:
    subprocess.run(args, cwd=cwd, check=True)


def read_state() -> dict:
    try:
        state = json.loads(STATE.read_text(encoding="utf-8"))
        return state if isinstance(state, dict) else {}
    except (OSError, ValueError):
        return {}


def install(force: bool = False) -> Path:
    if sys.version_info < (3, 10):
        raise RuntimeError("Python 3.10 or newer is required.")
    node, npm = shutil.which("node"), shutil.which("npm")
    if not node or not npm:
        raise RuntimeError("Install Node.js 22.18+ (including npm), then run this script again.")
    version = subprocess.check_output([node, "--version"], text=True).strip().lstrip("v")
    if tuple(int(part) for part in version.split(".")) < (22, 18, 0):
        raise RuntimeError(f"Node.js 22.18+ is required; found {version}.")

    python = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.is_file():
        print("Creating the local Python environment...", flush=True)
        run([sys.executable, "-m", "venv", str(VENV)])
    state = read_state()
    requirements = ROOT / "studio" / "api" / "requirements.txt"
    backend_hash = digest([requirements])
    if force or state.get("backend") != backend_hash:
        print("Installing Python dependencies...", flush=True)
        run([str(python), "-m", "pip", "install", "-r", str(requirements)])
        state["backend"] = backend_hash

    dependency_hash = digest([WEB / "package.json", WEB / "package-lock.json"])
    if force or state.get("dependencies") != dependency_hash or not (WEB / "node_modules" / ".package-lock.json").is_file():
        print("Installing frontend dependencies...", flush=True)
        run([npm, "ci"], WEB)
        state["dependencies"] = dependency_hash

    sources = [path for path in (WEB / "src").rglob("*") if path.is_file()]
    sources += [WEB / name for name in ("index.html", "package.json", "package-lock.json", "tsconfig.json", "vite.config.ts")]
    frontend_hash = digest(sources)
    if force or state.get("frontend") != frontend_hash or not (WEB / "dist" / "index.html").is_file():
        print("Building the frontend...", flush=True)
        run([npm, "run", "build"], WEB)
        state["frontend"] = frontend_hash
    STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return python


def available_port(value: str) -> int:
    port = int(value)
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def open_when_ready(url: str, stopped: threading.Event) -> None:
    # Ignore system HTTP proxies for this local health check.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(120):
        if stopped.is_set():
            return
        try:
            with opener.open(f"{url}/api/health", timeout=1) as response:
                ready = json.load(response).get("service") == "learning"
            if ready and not stopped.is_set():
                webbrowser.open(url)
                return
        except (OSError, ValueError, urllib.error.URLError):
            pass
        stopped.wait(1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install-only", action="store_true", help="install and build without starting the server")
    parser.add_argument("--reinstall", action="store_true", help="reinstall dependencies and rebuild the frontend")
    parser.add_argument("--no-browser", action="store_true", help="do not open the browser automatically")
    parser.add_argument("--port", type=available_port, default=8700, help="local HTTP port (default: 8700)")
    args = parser.parse_args()
    try:
        if not args.install_only:
            with socket.socket() as probe:
                try:
                    probe.bind(("127.0.0.1", args.port))
                except OSError as exc:
                    raise RuntimeError(f"Port {args.port} is unavailable. Stop the existing server or use --port 8701.") from exc
        python = install(args.reinstall)
        if args.install_only:
            print("Installation complete. Run the same launcher without --install-only to start Learning.")
            return 0
        env = os.environ.copy()
        env["LEARNING_WEB_DIR"] = str(WEB / "dist")
        env["PYTHONUNBUFFERED"] = "1"
        url = f"http://127.0.0.1:{args.port}"
        print(f"Learning: {url}\nKeep this terminal open. Press Ctrl+C to stop.", flush=True)
        stopped = threading.Event()
        if not args.no_browser:
            threading.Thread(target=open_when_ready, args=(url, stopped), daemon=True).start()
        try:
            return subprocess.call([
                str(python), "-m", "uvicorn", "app.main:app", "--app-dir", str(ROOT / "studio" / "api"),
                "--host", "127.0.0.1", "--port", str(args.port),
            ], cwd=ROOT, env=env)
        finally:
            stopped.set()
    except KeyboardInterrupt:
        return 130
    except (RuntimeError, OSError, subprocess.CalledProcessError) as exc:
        print(f"Learning could not start: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
