"""Optional serving of the production frontend alongside the API."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles


def mount_frontend(app: FastAPI, directory: str) -> None:
    if not directory:
        return
    root = Path(directory).resolve()
    if not (root / "index.html").is_file():
        raise RuntimeError(f"Frontend build missing in {root}; run the launcher first.")
    # Register last: the API routes take precedence over the static mount.
    # Navigation uses hash routes, so no catch-all HTML fallback is needed.
    app.mount("/", StaticFiles(directory=root, html=True), name="frontend")
