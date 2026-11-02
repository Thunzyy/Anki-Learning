from __future__ import annotations

import asyncio
import re
from uuid import uuid4

from ..languages import find_language
from ..models import Card
from ..paths import MEDIA_DIR, ensure_dirs


def safe_name(word: str) -> str:
    return re.sub(r"[^\w\-]+", "_", word.strip().lower()) or "word"


def generate_audio(card: Card, deck_id: str, language: str) -> str:
    spec = find_language(language)
    voice = spec.get("voice") or ""
    if not voice:
        raise RuntimeError(f"pas de voix TTS pour {spec.get('label') or language}")
    ensure_dirs()
    folder = MEDIA_DIR / deck_id
    folder.mkdir(parents=True, exist_ok=True)
    filename = f"{safe_name(card.lemma)}.mp3"
    path = folder / filename
    if not path.exists() or path.stat().st_size == 0:
        try:
            import edge_tts
        except ImportError as exc:
            raise RuntimeError("edge-tts n'est pas installé") from exc
        temporary = folder / f".{filename}.{uuid4().hex}.part"
        try:
            asyncio.run(_save(card.lemma, str(temporary), voice))
            if not temporary.exists() or temporary.stat().st_size == 0:
                raise RuntimeError("La génération audio n'a produit aucun son.")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    return f"/api/media/{deck_id}/{filename}"


async def _save(text: str, path: str, voice: str) -> None:
    import edge_tts
    await edge_tts.Communicate(text, voice).save(path)
