import os
import time
from types import SimpleNamespace

import pytest
from PIL import Image

from app.models import Card, Settings
from app.services import codex_cli, sentences


def test_availability_requires_cli_and_authenticated_session(monkeypatch):
    monkeypatch.setattr(codex_cli.shutil, "which", lambda _: None)
    assert not codex_cli.availability()[0]
    monkeypatch.setattr(codex_cli.shutil, "which", lambda _: "codex.exe")
    monkeypatch.setattr(codex_cli, "provider_status", lambda *a, **k: SimpleNamespace(connected=False))
    assert not codex_cli.availability()[0]
    monkeypatch.setattr(codex_cli, "provider_status", lambda *a, **k: SimpleNamespace(connected=True))
    assert codex_cli.availability() == (True, "")


def test_native_image_copies_verified_bytes_and_preserves_original(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    source = tmp_path / "codex/generated_images/job/image.png"
    source.parent.mkdir(parents=True)
    Image.new("RGB", (256, 256), "green").save(source)
    monkeypatch.setattr(codex_cli, "_run", lambda *a, **k: ({"image_path": str(source), "error": ""}, time.time()))
    result = codex_cli.generate_image("A pear on a plate", tmp_path / "media/pear.jpg")
    assert result.suffix == ".png"
    assert result.read_bytes() == source.read_bytes()


@pytest.mark.parametrize("case", ["outside", "stale", "invalid", "tiny", "error"])
def test_image_failure_never_applies_output(case, tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    source = tmp_path / ("outside.png" if case == "outside" else "codex/generated_images/image.png")
    source.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (32, 32) if case == "tiny" else (256, 256)).save(source)
    if case == "stale":
        os.utime(source, (1, 1))
    if case == "invalid":
        source.write_text("not an image")
    monkeypatch.setattr(codex_cli, "_run", lambda *a, **k: ({"image_path": str(source), "error": "quota" if case == "error" else ""}, time.time()))
    with pytest.raises(RuntimeError):
        codex_cli.generate_image("scene", tmp_path / "media/output.jpg")
    assert not (tmp_path / "media").exists()


def test_oauth_text_uses_cli_and_does_not_fall_back_to_templates(monkeypatch):
    monkeypatch.setattr(sentences, "resolve_for_provider", lambda *a: SimpleNamespace(source="oauth", value="private-token"))
    monkeypatch.setattr(sentences, "_openai_compat", lambda *a, **k: pytest.fail("OAuth sent to API"))
    monkeypatch.setattr(sentences, "_local", lambda *a: pytest.fail("silent template fallback"))
    monkeypatch.setattr(codex_cli, "ask_json", lambda *a: (_ for _ in ()).throw(RuntimeError("quota reached")))
    with pytest.raises(RuntimeError, match="quota reached"):
        sentences.generate_sentences([Card(id="p", lemma="pera", surface="pera", language="pt")], "codex", Settings())


def test_rejects_malformed_linguistic_result(monkeypatch):
    monkeypatch.setattr(codex_cli, "_run", lambda *a, **k: ({"items": [{"word": "pear"}]}, 0))
    with pytest.raises(RuntimeError, match="linguistique"):
        codex_cli.ask_json("translate pear")


def test_concurrent_equivalent_languages_generate_once_with_codex_provenance(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from app import paths
    from app.services import images
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    monkeypatch.setattr(paths, "MEDIA_DIR", tmp_path / "media")
    monkeypatch.setattr(images, "MEDIA_DIR", tmp_path / "media")
    monkeypatch.setattr(codex_cli, "availability", lambda: (True, ""))
    calls = []
    def generate(prompt, destination):
        calls.append(prompt)
        target = destination.with_suffix(".png")
        target.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (256, 256), "green").save(target)
        return target
    monkeypatch.setattr(codex_cli, "generate_image", generate)
    monkeypatch.setattr(images, "_generate", lambda *a: pytest.fail("wrong image provider"))
    cards = [Card(id=lang, language=lang, lemma="pera", surface="pera", french="poire",
                  phrase_fr="Cette poire est mûre et très sucrée.", category="Nom") for lang in ["pt", "es"]]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda c: images.fetch_image(c, "codex", Settings(), c.language,
                                                            allow_generate=True), cards))
    assert len(calls) == 1
    assert results[0] == results[1]
    assert results[0][1] == "openai"
    assert cards[0].image_asset_id == cards[1].image_asset_id
    assert cards[0].image_provenance["origin"] == "codex_cli"
    assert cards[0].image_provenance["visual_review"] == "not_reviewed"
