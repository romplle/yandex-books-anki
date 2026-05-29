from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from .core import ENRICHED_PATH, FIELDS, PENDING_PATH, canonical_front, normalize_card


DEFAULT_GIGACHAT_SCOPE = "GIGACHAT_API_PERS"
DEFAULT_GIGACHAT_MODEL = "GigaChat-2"
DEFAULT_GIGACHAT_VERIFY_SSL_CERTS = False


def load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = value


def build_enrichment_prompt(card: dict[str, str]) -> str:
    source = card.get("Source", "").strip() or "unknown source"
    page_url = card.get("PageURL", "").strip()
    page_line = f"\nPage URL: {page_url}" if page_url else ""
    return (
        "You create concise Anki vocabulary card fields for an English learner.\n"
        "Return only valid JSON with exactly these string keys: Meaning, Example.\n"
        "Meaning must be a short English definition, not a Russian translation.\n"
        "Example must be one natural English sentence using the word or phrase.\n"
        "Do not include markdown, comments, or extra keys.\n\n"
        f"Word or phrase: {card['Front']}\n"
        f"Source: {source}"
        f"{page_line}"
    )


def parse_enrichment_response(content: str, front: str) -> dict[str, str]:
    text = content.strip()
    fence_match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if fence_match:
        text = fence_match.group(1).strip()

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"GigaChat returned invalid JSON for {front!r}: {content}") from exc

    if not isinstance(payload, dict):
        raise ValueError(f"GigaChat returned a non-object response for {front!r}.")

    meaning = str(payload.get("Meaning", "")).strip()
    example = str(payload.get("Example", "")).strip()
    if not meaning or not example:
        raise ValueError(f"GigaChat returned empty Meaning or Example for {front!r}.")
    return {"Meaning": meaning, "Example": example}


class GigaChatEnrichmentClient:
    def __init__(
        self,
        credentials: str | None = None,
        scope: str | None = None,
        model: str | None = None,
        verify_ssl_certs: bool | None = None,
    ) -> None:
        from gigachat import GigaChat

        load_dotenv()

        self._client = GigaChat(
            credentials=credentials or os.environ["GIGACHAT_CREDENTIALS"],
            scope=scope or os.environ.get("GIGACHAT_SCOPE", DEFAULT_GIGACHAT_SCOPE),
            model=model or os.environ.get("GIGACHAT_MODEL", DEFAULT_GIGACHAT_MODEL),
            verify_ssl_certs=verify_ssl_certs or os.environ.get("GIGACHAT_VERIFY_SSL_CERTS", DEFAULT_GIGACHAT_VERIFY_SSL_CERTS),
        )

    def generate_enrichment(self, card: dict[str, str]) -> dict[str, str]:
        response = self._client.chat(build_enrichment_prompt(card))
        content = response.choices[0].message.content
        return parse_enrichment_response(content, card["Front"])


def load_json_array(path: Path) -> list[dict[str, str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [dict(item) for item in payload]


def output_card_from_pending(item: dict[str, str]) -> dict[str, str]:
    normalized = normalize_card(item)
    return {field: normalized.get(field, "") for field in FIELDS}


def remove_completed_pending_items(
    pending_items: list[dict[str, str]],
    cards: list[dict[str, str]],
) -> list[dict[str, str]]:
    complete_fronts = {
        canonical_front(card["Front"])
        for card in cards
        if card.get("Meaning", "").strip() and card.get("Example", "").strip()
    }
    return [item for item in pending_items if canonical_front(str(item.get("Front", ""))) not in complete_fronts]


def enrich_pending_cards(
    client: Any = None,
    pending_path: Path = PENDING_PATH,
    enriched_path: Path = ENRICHED_PATH,
) -> dict[str, int]:
    pending_items = load_json_array(pending_path)
    existing_items = load_json_array(enriched_path) if enriched_path.exists() else []
    cards = [normalize_card(item) for item in existing_items]
    indexes = {canonical_front(card["Front"]): index for index, card in enumerate(cards)}

    enrichment_client = client
    seen_pending: set[str] = set()
    completed_skipped = 0
    duplicates_skipped = 0
    updated = 0
    added = 0

    for item in pending_items:
        prompt_card = {key: str(value).strip() for key, value in item.items()}
        card = output_card_from_pending(prompt_card)
        prompt_card["Front"] = card["Front"]
        normalized = canonical_front(card["Front"])
        if normalized in seen_pending:
            duplicates_skipped += 1
            continue
        seen_pending.add(normalized)

        existing_index = indexes.get(normalized)
        if existing_index is not None:
            existing = cards[existing_index]
            if existing["Meaning"] and existing["Example"]:
                completed_skipped += 1
                continue
            if enrichment_client is None:
                enrichment_client = GigaChatEnrichmentClient()
            enrichment = enrichment_client.generate_enrichment(prompt_card)
            existing["Meaning"] = enrichment["Meaning"]
            existing["Example"] = enrichment["Example"]
            if not existing["Source"]:
                existing["Source"] = card["Source"]
            updated += 1
            continue

        if enrichment_client is None:
            enrichment_client = GigaChatEnrichmentClient()
        enrichment = enrichment_client.generate_enrichment(prompt_card)
        card["Meaning"] = enrichment["Meaning"]
        card["Example"] = enrichment["Example"]
        indexes[normalized] = len(cards)
        cards.append(card)
        added += 1

    enriched_path.parent.mkdir(parents=True, exist_ok=True)
    enriched_path.write_text(json.dumps(cards, ensure_ascii=False, indent=2), encoding="utf-8")
    remaining_pending = remove_completed_pending_items(pending_items, cards)
    pending_path.write_text(json.dumps(remaining_pending, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "pending_items": len(pending_items),
        "pending_remaining": len(remaining_pending),
        "already_enriched_skipped": completed_skipped,
        "duplicates_skipped": duplicates_skipped,
        "cards_updated": updated,
        "cards_added": added,
        "enriched_written": len(cards),
    }
