from __future__ import annotations

import argparse
from dataclasses import dataclass

from .models import Deck, Settings
from .pipeline import MAX_CONCURRENCY, card_needs_fill, fill_missing, sync_stages, _image_provider
from .protocol import qa_deck
from .services.images import image_generation_status, image_is_generated, image_is_stock, image_url_is_weak


@dataclass
class CompleteReport:
    ok: bool
    leftover: int
    images: int
    reason: str = ""
    batches: int = 0


def _needs_image(card, replace: bool = False) -> bool:
    if card.skip_image or (card.category or "").casefold() == "grammaire":
        return False
    if card.image_asset_id and card.image_url and not replace:
        return False
    if replace or image_is_stock(card.image_url, card.image_source):
        return not image_is_generated(card.image_url, card.image_source)
    return image_url_is_weak(card.lemma, card.french, card.image_url)


def _image_count(deck: Deck) -> int:
    return sum(1 for card in deck.cards if (card.image_url or "").strip() and not _needs_image(card))


def complete_deck(
    deck: Deck,
    settings: Settings,
    *,
    text_batch: int = 80,
    image_batch: int | None = None,
    persist: bool = True,
    max_batches: int = 10_000,
    replace_images: bool = False,
) -> CompleteReport:
    """Fill a deck until leftover is 0, or stop with a reason if the selected image provider is unavailable."""
    from .store import save_deck

    def persist_now() -> None:
        if persist:
            save_deck(deck)

    batches = 0
    if not replace_images:
        stalled = 0
        while batches < max_batches:
            leftover = [card for card in deck.cards if card_needs_fill(deck, card)]
            text_todo = [card for card in leftover if not _only_image_gap(deck, card)]
            if not text_todo:
                break
            before = len(text_todo)
            wave = text_todo[:text_batch]
            fill_missing(
                deck,
                settings,
                ids=[card.id for card in wave],
                limit=len(wave),
                generate_images=False,
            )
            persist_now()
            batches += 1
            remaining = sum(
                1
                for card in deck.cards
                if card_needs_fill(deck, card) and not _only_image_gap(deck, card)
            )
            if remaining >= before:
                stalled += 1
                if stalled >= 2:
                    break
            else:
                stalled = 0

    # Reuse reviewed contextual assets even while generation is offline.
    if deck.features.image and not replace_images:
        from .image_library import assign, lookup
        for card in deck.cards:
            if _needs_image(card):
                asset = lookup(card, deck.target_language)
                if asset:
                    assign(card, asset, "complete:contextual-reuse")
    image_todo = [card for card in deck.cards if deck.features.image and _needs_image(card, replace_images)]
    if image_todo:
        ready, reason = image_generation_status(_image_provider(deck, settings), settings)
        if not ready:
            sync_stages(deck)
            persist_now()
            return CompleteReport(
                ok=False,
                leftover=len(image_todo),
                images=_image_count(deck),
                reason=reason,
                batches=batches,
            )
        stalled = 0
        total = len(image_todo)
        image_workers = max(1, min(image_batch or settings.image_concurrency, MAX_CONCURRENCY))
        while image_todo and batches < max_batches:
            before = len(image_todo)
            wave = image_todo[:image_workers]
            print(
                f"images {total - before + 1}-{total - before + len(wave)}/{total} leftover={before}",
                flush=True,
            )
            fill_missing(
                deck,
                settings,
                ids=[card.id for card in wave],
                limit=len(wave),
                generate_images=True,
                replace_images=replace_images,
                concurrency=image_workers,
            )
            persist_now()
            batches += 1
            image_todo = [card for card in deck.cards if _needs_image(card, replace_images)]
            if len(image_todo) >= before:
                stalled += 1
                if stalled >= 2:
                    break
            else:
                stalled = 0

    leftover = sum(1 for card in deck.cards if card_needs_fill(deck, card))
    sync_stages(deck)
    report = qa_deck(deck)
    if report.ok and leftover == 0:
        deck.stages["qa"] = "passed"
    persist_now()
    return CompleteReport(
        ok=leftover == 0,
        leftover=leftover,
        images=_image_count(deck),
        reason="" if leftover == 0 else next(
            (errors[0] for errors in deck.stage_errors.values() if errors),
            f"{leftover} carte(s) encore incomplètes",
        ),
        batches=batches,
    )


def _only_image_gap(deck: Deck, card) -> bool:
    if not (card.french or "").strip():
        return False
    if deck.features.phonetic and not (card.ipa or "").strip():
        return False
    if deck.features.phrase and (not (card.phrase or "").strip() or not (card.phrase_fr or "").strip()):
        return False
    if deck.features.audio and not (card.audio_url or "").strip():
        return False
    return bool(deck.features.image) and _needs_image(card)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Remplit un deck Learning jusqu'à complétion.")
    parser.add_argument("deck_id", help="id du deck (fichier studio/data/decks/<id>.json)")
    parser.add_argument("--text-batch", type=int, default=80)
    parser.add_argument(
        "--image-batch",
        type=int,
        default=None,
        help="Nombre de workers image (utilise le réglage enregistré par défaut).",
    )
    parser.add_argument(
        "--replace-images",
        action="store_true",
        help="Remplace les photos Wikimedia par des illustrations avec le fournisseur IA choisi.",
    )
    args = parser.parse_args(argv)
    from .store import load_deck, load_settings, save_deck

    deck = load_deck(args.deck_id)
    settings = load_settings()
    print(f"deck {deck.id} — {len(deck.cards)} cartes")
    report = complete_deck(
        deck,
        settings,
        text_batch=args.text_batch,
        image_batch=args.image_batch,
        persist=True,
        replace_images=args.replace_images,
    )
    save_deck(deck)
    print(f"ok={report.ok} leftover={report.leftover} images={report.images} batches={report.batches}")
    if report.reason:
        print(report.reason)
    return 0 if report.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
