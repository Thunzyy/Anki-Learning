from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


StageName = Literal[
    "ingest",
    "normalize",
    "translate",
    "phonetic",
    "sentences",
    "images",
    "audio",
    "qa",
    "export",
]

StageStatus = Literal["pending", "running", "passed", "failed", "blocked", "skipped"]
ImageBank = Literal["grok", "codex", "openai", "wikimedia", "unsplash", "pexels"]
AiProvider = Literal["anthropic", "openai", "xai", "claude", "codex", "grok", "local"]


class DeckFeatures(BaseModel):
    phrase: bool = True
    phonetic: bool = True
    audio: bool = False
    image: bool = True


class Card(BaseModel):
    id: str
    lemma: str
    surface: str
    language: str
    french: str = ""
    category: str = ""
    ipa: str = ""
    phrase: str = ""
    phrase_fr: str = ""
    validated_example: dict = Field(default_factory=dict)
    image_url: str = ""
    image_source: str = ""
    image_concept: str = ""
    image_scene: str = ""
    image_asset_id: str = ""
    image_provenance: dict = Field(default_factory=dict)
    image_history: list[dict] = Field(default_factory=list)
    audio_url: str = ""
    skip_image: bool = False
    flags: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class QaReport(BaseModel):
    ok: bool
    checked: int
    failed: int
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class Deck(BaseModel):
    id: str
    name: str
    language: str
    language_label: str = ""
    target_language: str = "fr"
    target_label: str = "Français"
    image_bank: ImageBank = "grok"
    ai_provider: AiProvider = "grok"
    source: str = "manual"
    kind: Literal["vocab", "grammar"] = "vocab"
    features: DeckFeatures = Field(default_factory=DeckFeatures)
    cards: list[Card] = Field(default_factory=list)
    card_count: int = 0
    stages: dict[str, StageStatus] = Field(default_factory=dict)
    stage_errors: dict[str, list[str]] = Field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""


class DeckCreate(BaseModel):
    name: str
    language: str
    language_label: str = ""
    target_language: str = ""
    target_label: str = ""
    words: list[str] = Field(default_factory=list)
    source_id: str = ""
    limit: int | None = None
    features: DeckFeatures = Field(default_factory=DeckFeatures)


class DeckPatch(BaseModel):
    name: str | None = None
    features: DeckFeatures | None = None


class CardsAdd(BaseModel):
    words: list[str] = Field(default_factory=list)
    enrich: bool = True


class CardsEnrich(BaseModel):
    ids: list[str] = Field(default_factory=list)
    limit: int | None = 25
    features: DeckFeatures | None = None
    concurrency: int = 4
    retry: bool = False
    generate_images: bool = False
    replace_images: bool = False


class CardPatch(BaseModel):
    surface: str | None = None
    lemma: str | None = None
    french: str | None = None
    ipa: str | None = None
    phrase: str | None = None
    phrase_fr: str | None = None
    category: str | None = None
    image_url: str | None = None
    image_source: str | None = None
    image_concept: str | None = None
    image_scene: str | None = None
    image_context_verified: bool = False
    audio_url: str | None = None
    skip_image: bool | None = None


class CardsBulkDelete(BaseModel):
    ids: list[str] = Field(default_factory=list)


class ImagesReplace(BaseModel):
    source: str = "all"
    destination: Literal["library", "grok", "codex", "gemini", "openai", "wikimedia", "unsplash", "pexels", "remove"] = "library"
    generate_missing: bool = False
    ids: list[str] = Field(default_factory=list)
    limit: int = Field(default=20, ge=1, le=100)
    preview: bool = True
    expected_revision: str = ""


class Settings(BaseModel):
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    xai_api_key: str = ""
    unsplash_access_key: str = ""
    pexels_api_key: str = ""
    default_image_bank: ImageBank = "grok"
    default_ai_provider: AiProvider = "grok"
    default_target_language: str = "fr"
    default_target_label: str = "Français"
    github_repo: str = "Thunzyy/Anki-Learning"
    github_release: str = "decks"
    anki_connect_url: str = "http://127.0.0.1:8765"
    image_prompt: str = ""
    image_concurrency: int = Field(default=4, ge=1, le=64)
