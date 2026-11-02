from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from .lexicon import lookup_word, row_to_fields
from .models import Deck, Settings
from .protocol import (
    ProtocolError,
    STAGES,
    apply_normalize,
    can_run,
    feature_enabled,
    qa_deck,
    validate_card_image,
    validate_card_phonetic,
    validate_card_sentence,
    validate_card_translate,
    validate_normalize,
)
from .services.audio import generate_audio
from .services.images import fetch_image, image_generation_status, image_is_generated, image_is_stock, image_url_is_weak
from .services.sentences import generate_glosses, generate_ipa, generate_phrase_translations, generate_sentences

AI_CHUNK = 20
DEFAULT_CONCURRENCY = 4
MAX_CONCURRENCY = 64


def _clamp_workers(value: int | None) -> int:
    raw = DEFAULT_CONCURRENCY if value is None else int(value)
    return max(1, min(raw, MAX_CONCURRENCY))


def _chunks(items: list, size: int) -> list[list]:
    width = max(1, size)
    return [items[index:index + width] for index in range(0, len(items), width)]


def _parallel_apply(items: list, fn, workers: int) -> list:
    if not items:
        return []
    count = max(1, min(workers, len(items)))
    if count == 1:
        return [fn(item) for item in items]
    with ThreadPoolExecutor(max_workers=count) as pool:
        return list(pool.map(fn, items))


def run_stage(deck: Deck, stage: str, settings: Settings) -> Deck:
    allowed, reason = can_run(deck, stage)
    if not allowed:
        raise ProtocolError(reason)
    deck.stages[stage] = "running"
    handler = {
        "ingest": _ingest,
        "normalize": _normalize,
        "translate": _translate,
        "phonetic": _phonetic,
        "sentences": _sentences,
        "images": _images,
        "audio": _audio,
        "qa": _qa,
        "export": _export_gate,
    }[stage]
    errors = handler(deck, settings)
    if errors:
        deck.stages[stage] = "failed"
        deck.stage_errors[stage] = errors
    else:
        deck.stages[stage] = "passed"
        deck.stage_errors[stage] = []
    return deck


def card_needs_fill(deck: Deck, card) -> bool:
    if not (card.french or "").strip():
        return True
    if feature_enabled(deck, "phonetic") and not (card.ipa or "").strip():
        return True
    if feature_enabled(deck, "sentences") and (
        not (card.phrase or "").strip() or not (card.phrase_fr or "").strip()
    ):
        return True
    if feature_enabled(deck, "images"):
        grammar = (card.category or "").casefold() == "grammaire" or card.skip_image
        if not grammar and (
            not (card.image_url or "").strip()
            or (not card.image_asset_id and (
                image_url_is_weak(card.lemma, card.french, card.image_url)
                or image_is_stock(card.image_url, card.image_source)
            ))
        ):
            return True
    if feature_enabled(deck, "audio") and not (card.audio_url or "").strip():
        return True
    return False


def sync_stages(deck: Deck) -> Deck:
    """Set stage status from what the cards actually contain."""
    cards = deck.cards
    if not cards:
        deck.stages["ingest"] = "pending"
        return deck
    deck.stages["ingest"] = "passed"
    deck.stages["normalize"] = "passed" if all((card.lemma or "").strip() for card in cards) else "failed"

    def coverage(ok: bool) -> str:
        hits = sum(1 for card in cards if ok(card))
        if hits == 0:
            return "pending"
        if hits < len(cards):
            return "pending"
        return "passed"

    deck.stages["translate"] = coverage(lambda card: bool((card.french or "").strip()))
    if feature_enabled(deck, "phonetic"):
        deck.stages["phonetic"] = coverage(lambda card: bool((card.ipa or "").strip()))
    else:
        deck.stages["phonetic"] = "skipped"
    if feature_enabled(deck, "sentences"):
        deck.stages["sentences"] = coverage(
            lambda card: bool((card.phrase or "").strip() and (card.phrase_fr or "").strip())
        )
    else:
        deck.stages["sentences"] = "skipped"
    if feature_enabled(deck, "images"):
        def image_ok(card) -> bool:
            grammar = (card.category or "").casefold() == "grammaire" or card.skip_image
            return grammar or bool((card.image_url or "").strip())
        deck.stages["images"] = coverage(image_ok)
    else:
        deck.stages["images"] = "skipped"
    if feature_enabled(deck, "audio"):
        deck.stages["audio"] = coverage(lambda card: bool((card.audio_url or "").strip()))
    else:
        deck.stages["audio"] = "skipped"
    for name, errors in (deck.stage_errors or {}).items():
        if errors and deck.stages.get(name) != "passed":
            deck.stages[name] = "failed"
    return deck


def _reset_invalid_generated_fields(deck: Deck, cards: list) -> None:
    """Clear generated fields that fail QA so the next fill can retry them."""
    kind = getattr(deck, "kind", "vocab")
    for card in cards:
        if feature_enabled(deck, "sentences") and (card.phrase or "").strip():
            if validate_card_sentence(card, kind):
                card.phrase = ""
                card.phrase_fr = ""
        card.errors = []


def fill_missing(
    deck: Deck,
    settings: Settings,
    ids: list[str] | None = None,
    limit: int | None = None,
    concurrency: int | None = None,
    on_progress=None,
    retry: bool = False,
    generate_images: bool = False,
    replace_images: bool = False,
) -> Deck:
    """Fill empty fields without replaying the whole protocol."""
    original = list(deck.cards)
    wanted = set(ids) if ids else None
    pool = [card for card in original if wanted is None or card.id in wanted]
    deck.stage_errors = {}
    from .cache import apply_card, remember_deck
    target = deck.target_language or "fr"
    for card in pool:
        apply_card(card, deck.language, target)
    if retry:
        _reset_invalid_generated_fields(deck, pool)
    incomplete = [
        card for card in pool
        if card_needs_fill(deck, card) or _needs_image_replace(deck, card, replace_images)
    ]
    if retry:
        incomplete.sort(key=lambda card: (
            0 if not (card.french or "").strip() else 1,
            0 if not (card.phrase or "").strip() else 1,
            0 if not (card.ipa or "").strip() else 1,
        ))
    if limit and limit > 0:
        incomplete = incomplete[:limit]
    workers = _clamp_workers(concurrency)
    run_errors: dict[str, list[str]] = {}

    def publish() -> None:
        if on_progress is None:
            return
        updated = {card.id: card for card in deck.cards}
        hold = deck.cards
        deck.cards = [updated.get(card.id, card) for card in original]
        sync_stages(deck)
        try:
            on_progress(deck)
        finally:
            deck.cards = hold

    batch_ids = {card.id for card in incomplete}
    deck.cards = [card for card in original if card.id in batch_ids] if batch_ids else []
    try:
        if deck.cards:
            deck.cards = [apply_normalize(card) for card in deck.cards]
            try:
                errors = _translate(deck, settings, workers, publish)
                if errors:
                    deck.stage_errors["translate"] = errors[:20]
            except Exception as exc:
                _record_fill_error(deck, "translate", exc, run_errors)
            if feature_enabled(deck, "phonetic"):
                try:
                    errors = _phonetic(deck, settings, workers, publish)
                    if errors:
                        deck.stage_errors["phonetic"] = errors[:20]
                except Exception as exc:
                    _record_fill_error(deck, "phonetic", exc, run_errors)
            if feature_enabled(deck, "sentences"):
                try:
                    errors = _sentences(deck, settings, workers, publish)
                    if errors:
                        deck.stage_errors["sentences"] = errors[:20]
                except Exception as exc:
                    _record_fill_error(deck, "sentences", exc, run_errors)
            if feature_enabled(deck, "images"):
                try:
                    ready, reason = image_generation_status(_image_provider(deck, settings), settings)
                    errors = _images(
                        deck,
                        settings,
                        workers,
                        publish,
                        allow_generate=bool(generate_images and ready),
                        replace_images=replace_images,
                    )
                    if errors:
                        deck.stage_errors["images"] = errors[:20]
                    still_need = [
                        card for card in deck.cards
                        if not card.skip_image
                        and card.category.casefold() != "grammaire"
                        and (
                            image_url_is_weak(card.lemma, card.french, card.image_url)
                            or (
                                replace_images
                                and not image_is_generated(card.image_url, card.image_source)
                            )
                        )
                    ]
                    if generate_images and still_need and not ready:
                        deck.stage_errors.setdefault("images", [])
                        if reason not in deck.stage_errors["images"]:
                            deck.stage_errors["images"].insert(0, reason)
                except Exception as exc:
                    _record_fill_error(deck, "images", exc, run_errors)
            if feature_enabled(deck, "audio"):
                try:
                    errors = _audio(deck, settings, workers, publish)
                    if errors:
                        deck.stage_errors["audio"] = errors[:20]
                except Exception as exc:
                    _record_fill_error(deck, "audio", exc, run_errors)
    finally:
        updated = {card.id: card for card in deck.cards}
        deck.cards = [updated.get(card.id, card) for card in original]
    remember_deck(deck)
    report = qa_deck(deck)
    for card in deck.cards:
        for err in run_errors.get(card.id, []):
            if err not in card.errors:
                card.errors.append(err)
    sync_stages(deck)
    if report.ok and not run_errors:
        deck.stages["qa"] = "passed"
    elif report.failed or run_errors:
        deck.stages["qa"] = "failed"
    return deck


def _record_fill_error(
    deck: Deck, stage: str, exc: Exception, run_errors: dict[str, list[str]] | None = None
) -> None:
    message = str(exc).strip() or exc.__class__.__name__
    deck.stages[stage] = "failed"
    deck.stage_errors.setdefault(stage, [])
    if message not in deck.stage_errors[stage]:
        deck.stage_errors[stage].append(message)
    for card in deck.cards:
        if message not in card.errors:
            card.errors.append(message)
        if run_errors is not None:
            bucket = run_errors.setdefault(card.id, [])
            if message not in bucket:
                bucket.append(message)


def run_until_blocked(deck: Deck, settings: Settings) -> Deck:
    for stage in STAGES:
        if stage == "export":
            continue
        if not feature_enabled(deck, stage):
            deck.stages[stage] = "skipped"
            deck.stage_errors[stage] = []
            continue
        if deck.stages.get(stage) in {"passed", "skipped"}:
            continue
        deck = run_stage(deck, stage, settings)
        if deck.stages.get(stage) != "passed":
            break
    return deck


def _ingest(deck: Deck, settings: Settings) -> list[str]:
    if not deck.cards:
        return ["aucune carte à traiter"]
    return []


def _normalize(deck: Deck, settings: Settings) -> list[str]:
    deck.cards = [apply_normalize(card) for card in deck.cards]
    return validate_normalize(deck.cards)


def _hint(deck: Deck) -> str:
    return deck.language_label or deck.language


def _target(deck: Deck) -> str:
    return deck.target_label or deck.target_language or "français"


def _provider(deck: Deck, settings: Settings) -> str:
    aliases = {"anthropic": "claude", "openai": "codex", "xai": "grok"}
    raw = settings.default_ai_provider or deck.ai_provider
    return aliases.get(raw, raw)


def _image_provider(deck: Deck, settings: Settings) -> str:
    """An explicit AI image bank takes precedence over the text provider."""
    bank = deck.image_bank
    if bank in {"codex", "openai"}:
        return "codex"
    if bank == "grok":
        return "grok"
    return _provider(deck, settings)


def _merge_lookups(maps: list[dict]) -> dict:
    merged: dict = {}
    for item in maps:
        if item:
            merged.update(item)
    return merged


def _translate(deck: Deck, settings: Settings, workers: int = 1, on_wave=None) -> list[str]:
    for card in deck.cards:
        if not card.french:
            row = lookup_word(card.language, card.lemma)
            if row:
                fields = row_to_fields(card.language, row)
                card.french = fields["french"]
                if not card.category:
                    card.category = fields["category"]
    missing = [card for card in deck.cards if not card.french]
    if missing:
        def one(batch):
            return generate_glosses(batch, _provider(deck, settings), settings, _hint(deck), _target(deck))

        lookup: dict = {}
        for wave in _chunks(_chunks(missing, AI_CHUNK), max(1, workers)):
            lookup.update(_merge_lookups(_parallel_apply(wave, one, workers)))
            for card in missing:
                hit = lookup.get(card.lemma.casefold())
                if hit:
                    card.french = hit["french"]
                    if hit.get("category"):
                        card.category = hit["category"]
            if on_wave:
                on_wave()
    return [err for card in deck.cards for err in validate_card_translate(card)]


def _phonetic(deck: Deck, settings: Settings, workers: int = 1, on_wave=None) -> list[str]:
    for card in deck.cards:
        if not card.ipa:
            row = lookup_word(card.language, card.lemma)
            if row:
                card.ipa = row_to_fields(card.language, row)["ipa"]
    pending = [card for card in deck.cards if not (card.ipa or "").strip()]
    if pending:
        def one(batch):
            return generate_ipa(batch, _provider(deck, settings), settings, _hint(deck))

        lookup: dict = {}
        for wave in _chunks(_chunks(pending, AI_CHUNK), max(1, workers)):
            lookup.update(_merge_lookups(_parallel_apply(wave, one, workers)))
            for card in pending:
                if not card.ipa:
                    card.ipa = lookup.get(card.lemma.casefold(), "")
            if on_wave:
                on_wave()
    return [err for card in deck.cards for err in validate_card_phonetic(card)]


def _sentences(deck: Deck, settings: Settings, workers: int = 1, on_wave=None) -> list[str]:
    from .cache import lookup_scene

    for card in deck.cards:
        if deck.target_language == "fr" and not card.phrase.strip() and not card.phrase_fr.strip() and card.french.strip():
            scene = lookup_scene(card.french)
            if scene.get("phrase_fr"):
                card.phrase_fr = scene["phrase_fr"]
    missing = [card for card in deck.cards if not (card.phrase or "").strip()]
    kind = getattr(deck, "kind", "vocab")

    def one(batch):
        return generate_sentences(
            batch, _provider(deck, settings), settings, _hint(deck), _target(deck), kind=kind,
        )

    lookup: dict = {}
    for wave in _chunks(_chunks(missing, AI_CHUNK), max(1, workers)):
        lookup.update(_merge_lookups(_parallel_apply(wave, one, workers)))
        for card in deck.cards:
            hit = lookup.get(card.lemma.casefold())
            if not hit:
                continue
            if not card.phrase:
                card.phrase = hit["sentence"]
            if not card.phrase_fr:
                card.phrase_fr = hit["translation"]
        if on_wave:
            on_wave()
    leftover = [card for card in deck.cards if card.phrase and not card.phrase_fr]
    if leftover:
        def one_tr(batch):
            return generate_phrase_translations(batch, _provider(deck, settings), settings, _target(deck))

        extra: dict = {}
        for wave in _chunks(_chunks(leftover, AI_CHUNK), max(1, workers)):
            extra.update(_merge_lookups(_parallel_apply(wave, one_tr, workers)))
            for card in leftover:
                if extra.get(card.lemma.casefold()):
                    card.phrase_fr = extra[card.lemma.casefold()]
            if on_wave:
                on_wave()
    errors: list[str] = []
    for card in deck.cards:
        errors.extend(validate_card_sentence(card, kind))
    return errors


def _needs_image_replace(deck: Deck, card, replace_images: bool) -> bool:
    if not replace_images or not feature_enabled(deck, "images"):
        return False
    if card.skip_image or (card.category or "").casefold() == "grammaire":
        return False
    return not image_is_generated(card.image_url, card.image_source)


def _keep_existing_image(card, replace_images: bool) -> bool:
    if card.skip_image or (card.category or "").casefold() == "grammaire":
        return False
    if card.image_asset_id and card.image_url and not replace_images:
        return True
    if image_is_stock(card.image_url, card.image_source):
        return False
    if replace_images:
        return image_is_generated(card.image_url, card.image_source)
    return bool(card.image_url) and not image_url_is_weak(card.lemma, card.french, card.image_url)


def _images(
    deck: Deck,
    settings: Settings,
    workers: int = 1,
    on_wave=None,
    allow_generate: bool = False,
    replace_images: bool = False,
) -> list[str]:
    errors: list[str] = []

    def one(card):
        if card.skip_image or card.category.casefold() == "grammaire":
            return card.id, "", "", ""
        if _keep_existing_image(card, replace_images):
            return card.id, card.image_url, card.image_source, ""
        try:
            url, source = fetch_image(
                card,
                deck.image_bank,
                settings,
                deck.id,
                allow_generate=allow_generate,
                force_generate=replace_images,
                generation_provider=_image_provider(deck, settings),
                target_language=deck.target_language,
            )
            return card.id, url or card.image_url, source or card.image_source, ""
        except Exception as exc:
            return card.id, "", "", f"{card.lemma}: {exc}"

    todo = [
        card for card in deck.cards
        if card.skip_image
        or card.category.casefold() == "grammaire"
        or not _keep_existing_image(card, replace_images)
    ]
    by_id = {card.id: card for card in deck.cards}
    for wave in _chunks(todo, max(1, workers)):
        for card_id, url, source, err in _parallel_apply(wave, one, workers):
            card = by_id[card_id]
            if card.skip_image or card.category.casefold() == "grammaire":
                card.image_url = ""
                card.image_source = ""
            elif url:
                card.image_url = url
                card.image_source = source
            if err:
                errors.append(err)
        if on_wave:
            on_wave()
    for card in deck.cards:
        errors.extend(validate_card_image(card))
    return errors


def _audio(deck: Deck, settings: Settings, workers: int = 1, on_wave=None) -> list[str]:
    errors: list[str] = []
    todo = [card for card in deck.cards if not card.audio_url]
    by_id = {card.id: card for card in deck.cards}

    def one(card):
        try:
            return card.id, generate_audio(card, deck.id, deck.language), ""
        except Exception as exc:
            return card.id, "", f"{card.lemma}: {exc}"

    for wave in _chunks(todo, max(1, workers)):
        for card_id, url, err in _parallel_apply(wave, one, workers):
            card = by_id[card_id]
            if url:
                card.audio_url = url
            if err:
                errors.append(err)
        if on_wave:
            on_wave()
    return errors


def _qa(deck: Deck, settings: Settings) -> list[str]:
    report = qa_deck(deck)
    return report.errors


def _export_gate(deck: Deck, settings: Settings) -> list[str]:
    report = qa_deck(deck)
    if not report.ok:
        return ["export bloqué: le QA n'est pas vert"] + report.errors[:8]
    return []
