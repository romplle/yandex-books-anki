from __future__ import annotations

import base64
import json
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from .core import (
    ANKI_CONNECT_URL,
    AUDIO_DIR,
    BACK_TEMPLATE,
    CARD_CSS,
    DECK_NAME,
    FIELDS,
    FRONT_TEMPLATE,
    MODEL_NAME,
    canonical_front,
    safe_audio_filename,
    sound_field,
    sound_filename_from_field,
)


def anki_request(action: str, **params: Any) -> Any:
    payload = json.dumps({"action": action, "version": 6, "params": params}).encode("utf-8")
    request = Request(
        ANKI_CONNECT_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
    except URLError as exc:
        raise RuntimeError("AnkiConnect is unavailable. Open Anki Desktop and enable AnkiConnect.") from exc
    if result.get("error"):
        raise RuntimeError(f"AnkiConnect {action} failed: {result['error']}")
    return result.get("result")


def ensure_anki_deck_and_model() -> None:
    deck_names = anki_request("deckNames")
    if DECK_NAME not in deck_names:
        anki_request("createDeck", deck=DECK_NAME)

    model_names = anki_request("modelNames")
    if MODEL_NAME in model_names:
        update_anki_model_templates()
        return

    anki_request(
        "createModel",
        modelName=MODEL_NAME,
        inOrderFields=FIELDS,
        css=CARD_CSS,
        cardTemplates=[
            {
                "Name": "Vocabulary",
                "Front": FRONT_TEMPLATE,
                "Back": BACK_TEMPLATE,
            }
        ],
    )
    update_anki_model_templates()


def update_anki_model_templates() -> None:
    anki_request(
        "updateModelTemplates",
        model={
            "name": MODEL_NAME,
            "templates": {
                "Vocabulary": {
                    "Front": FRONT_TEMPLATE,
                    "Back": BACK_TEMPLATE,
                }
            },
        },
    )
    anki_request(
        "updateModelStyling",
        model={
            "name": MODEL_NAME,
            "css": CARD_CSS,
        },
    )


def existing_notes_by_front() -> dict[str, dict[str, Any]]:
    note_ids = anki_request("findNotes", query=f'deck:"{DECK_NAME}" note:"{MODEL_NAME}"')
    if not note_ids:
        return {}
    notes = anki_request("notesInfo", notes=note_ids)
    result: dict[str, dict[str, Any]] = {}
    for note in notes:
        front = note.get("fields", {}).get("Front", {}).get("value", "")
        if front:
            result[canonical_front(front)] = note
    return result


def note_fields_from_info(note: dict[str, Any]) -> dict[str, str]:
    raw_fields = note.get("fields", {})
    return {field: str(raw_fields.get(field, {}).get("value", "")).strip() for field in FIELDS}


def update_anki_note_fields(note_id: int, card: dict[str, str]) -> None:
    anki_request(
        "updateNoteFields",
        note={
            "id": note_id,
            "fields": {field: card.get(field, "") for field in FIELDS},
        },
    )


def store_audio_in_anki(filename: str) -> None:
    path = AUDIO_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"Missing audio file: {path}")
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    anki_request("storeMediaFile", filename=filename, data=encoded)


def store_card_audio_in_anki(card: dict[str, str]) -> None:
    for sound_field_name in ("Sound", "Sound_Meaning", "Sound_Example"):
        filename = sound_filename_from_field(card.get(sound_field_name, ""))
        if filename:
            store_audio_in_anki(filename)


def build_anki_note(card: dict[str, str]) -> dict[str, Any]:
    fields = {field: card.get(field, "") for field in FIELDS}
    return {
        "deckName": DECK_NAME,
        "modelName": MODEL_NAME,
        "fields": fields,
        "options": {"allowDuplicate": False, "duplicateScope": "deck"},
        "tags": ["yandex-books"],
    }


def import_cards(cards: list[dict[str, str]]) -> dict[str, int]:
    ensure_anki_deck_and_model()
    existing_notes = existing_notes_by_front()
    added = 0
    skipped_existing = 0
    updated_existing = 0
    skipped_incomplete = 0

    for card in cards:
        normalized = canonical_front(card["Front"])
        if not card["Meaning"] or not card["Example"]:
            skipped_incomplete += 1
            continue
        if not card["Sound"]:
            card["Sound"] = sound_field(safe_audio_filename(card["Front"]))
        desired_fields = {field: card.get(field, "") for field in FIELDS}

        existing_note = existing_notes.get(normalized)
        if existing_note:
            existing_fields = note_fields_from_info(existing_note)
            if existing_fields == desired_fields:
                skipped_existing += 1
                continue
            store_card_audio_in_anki(card)
            update_anki_note_fields(int(existing_note["noteId"]), card)
            updated_existing += 1
            continue

        store_card_audio_in_anki(card)
        try:
            anki_request("addNote", note=build_anki_note(card))
        except RuntimeError as exc:
            if "duplicate" in str(exc).lower():
                skipped_existing += 1
                continue
            raise
        existing_notes[normalized] = {"noteId": 0, "fields": {field: {"value": card.get(field, "")} for field in FIELDS}}
        added += 1

    return {
        "enriched_items": len(cards),
        "existing_cards_skipped": skipped_existing,
        "existing_cards_updated": updated_existing,
        "incomplete_cards_skipped": skipped_incomplete,
        "new_cards_added": added,
    }
