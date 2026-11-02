from __future__ import annotations

from .lexicon import lookup_word, row_to_fields
from .models import Card, Deck
from .protocol import fold, qa_deck
from .store import new_id


class EditError(Exception):
    pass


ALLOWED_CARD_FIELDS = {
    "surface",
    "lemma",
    "french",
    "ipa",
    "phrase",
    "phrase_fr",
    "category",
    "image_url",
    "image_concept",
    "image_scene",
    "image_source",
    "audio_url",
    "skip_image",
}


def _lemmas(deck: Deck, exclude_id: str = "") -> set[str]:
    return {fold(card.lemma) for card in deck.cards if card.id != exclude_id}


def add_words(deck: Deck, words: list[str]) -> Deck:
    existing = _lemmas(deck)
    added = False
    for raw in words:
        word = (raw or "").strip()
        if not word:
            continue
        key = fold(word)
        if key in existing:
            continue
        row = lookup_word(deck.language, word)
        fields = row_to_fields(deck.language, row) if row else {}
        deck.cards.append(Card(
            id=new_id(),
            lemma=fields.get("lemma") or word,
            surface=fields.get("surface") or word,
            language=deck.language,
            french=fields.get("french", ""),
            category=fields.get("category", ""),
            ipa=fields.get("ipa", ""),
            phrase=fields.get("phrase", ""),
            phrase_fr=fields.get("phrase_fr", ""),
        ))
        existing.add(key)
        added = True
    if not added:
        raise EditError("aucun mot à ajouter")
    qa_deck(deck)
    return deck


def patch_card(deck: Deck, card_id: str, fields: dict) -> Card:
    card = next((item for item in deck.cards if item.id == card_id), None)
    if card is None:
        raise EditError("carte introuvable")
    incoming_lemma = fields.get("lemma")
    if incoming_lemma is not None:
        key = fold(str(incoming_lemma))
        if key in _lemmas(deck, exclude_id=card_id):
            raise EditError(f"doublon: {incoming_lemma}")
    from .image_library import snapshot, register, assign, blocked, source_of
    before = card.model_copy(deep=True)
    if fields.get("image_url") and blocked(source_of(fields["image_url"], fields.get("image_source", ""))):
        raise EditError("Cette source d'images a été désactivée")
    for name, value in fields.items():
        if name not in ALLOWED_CARD_FIELDS or value is None:
            continue
        setattr(card, name, value)
    if fields.get("surface") and not fields.get("lemma"):
        card.lemma = str(fields["surface"]).strip() or card.lemma
    image_changed = any(getattr(before, key) != getattr(card, key)
                        for key in ("image_url", "image_source", "image_concept", "image_scene"))
    text_changed = any(getattr(before, key) != getattr(card, key)
                       for key in ("lemma", "french", "phrase", "phrase_fr", "category"))
    verified_context = fields.get("image_context_verified") is True
    if text_changed and not image_changed and not verified_context and card.image_url:
        assign(card, None, "context_changed")
    if image_changed or verified_context:
        if card.image_url != before.image_url and "image_source" not in fields:
            card.image_source = "unknown"
        card.image_asset_id = ""
        card.image_provenance = {}
        if card.image_url:
            register(card, deck.target_language,
                     verified_context=verified_context or bool(card.image_concept and card.image_scene),
                     metadata={"origin": "manual_edit"})
        after = snapshot(card)
        card.image_history = before.image_history
        card.image_url, card.image_source = before.image_url, before.image_source
        card.image_asset_id, card.image_provenance = before.image_asset_id, before.image_provenance
        assign(card, {"url": after["url"], "source": after["source"],
                      "id": after["asset_id"], "metadata": after["provenance"]} if after["url"] else None,
               "manual_edit")
    qa_deck(deck)
    return card


def delete_cards(deck: Deck, ids: list[str]) -> int:
    wanted = {item for item in ids if item}
    if not wanted:
        raise EditError("aucune carte à supprimer")
    before = len(deck.cards)
    deck.cards = [card for card in deck.cards if card.id not in wanted]
    removed = before - len(deck.cards)
    if removed == 0:
        raise EditError("carte introuvable")
    qa_deck(deck)
    return removed


def rename_deck(deck: Deck, name: str) -> Deck:
    cleaned = (name or "").strip()
    if not cleaned:
        raise EditError("nom vide")
    deck.name = cleaned
    return deck
