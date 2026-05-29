from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Protocol

from .core import ENRICHED_PATH, FIELDS, PENDING_PATH, normalize_card, normalize_front


DEFAULT_GIGACHAT_SCOPE = "GIGACHAT_API_PERS"
DEFAULT_GIGACHAT_MODEL = "GigaChat-2"


class MeaningClient(Protocol):
    def generate(self, card: dict[str, str]) -> dict[str, str]:
        ...


def load_dotenv(path: Path = Path(".env")) -> int:
    if not path.exists():
        return 0

    loaded = 0
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
            loaded += 1
    return loaded


def env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


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


class GigaChatMeaningClient:
    def __init__(
        self,
        credentials: str | None = None,
        scope: str | None = None,
        model: str | None = None,
        verify_ssl_certs: bool | None = None,
    ) -> None:
        load_dotenv()
        credentials = credentials or os.environ.get("GIGACHAT_CREDENTIALS")
        if not credentials:
            raise RuntimeError("Missing GIGACHAT_CREDENTIALS. Set it in the environment or in .env.")

        try:
            from gigachat import GigaChat
        except ImportError as exc:
            raise RuntimeError("Install GigaChat SDK with: pip install -r requirements.txt") from exc

        if verify_ssl_certs is None:
            verify_ssl_certs = env_bool("GIGACHAT_VERIFY_SSL_CERTS", False)

        self._client = GigaChat(
            credentials=credentials,
            scope=scope or os.environ.get("GIGACHAT_SCOPE", DEFAULT_GIGACHAT_SCOPE),
            model=model or os.environ.get("GIGACHAT_MODEL", DEFAULT_GIGACHAT_MODEL),
            verify_ssl_certs=verify_ssl_certs,
        )

    def generate(self, card: dict[str, str]) -> dict[str, str]:
        response = self._client.chat(build_enrichment_prompt(card))
        content = response.choices[0].message.content
        return parse_enrichment_response(content, card["Front"])


def load_json_array(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"Missing {path}. Run scrape or csv first.")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{path} must contain a JSON array.")
    return [dict(item) for item in payload]


def output_card_from_pending(item: dict[str, str]) -> dict[str, str]:
    normalized = normalize_card(item)
    return {field: normalized.get(field, "") for field in FIELDS}


def enrich_pending_cards(
    client: MeaningClient | None = None,
    pending_path: Path = PENDING_PATH,
    enriched_path: Path = ENRICHED_PATH,
) -> dict[str, int]:
    pending_items = load_json_array(pending_path)
    existing_items = load_json_array(enriched_path) if enriched_path.exists() else []
    cards = [normalize_card(item) for item in existing_items]
    indexes = {normalize_front(card["Front"]): index for index, card in enumerate(cards)}

    client = client or GigaChatMeaningClient()
    seen_pending: set[str] = set()
    completed_skipped = 0
    duplicates_skipped = 0
    updated = 0
    added = 0

    for item in pending_items:
        prompt_card = {key: str(value).strip() for key, value in item.items()}
        card = output_card_from_pending(prompt_card)
        normalized = normalize_front(card["Front"])
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
            enrichment = client.generate(prompt_card)
            existing["Meaning"] = enrichment["Meaning"]
            existing["Example"] = enrichment["Example"]
            if not existing["Source"]:
                existing["Source"] = card["Source"]
            updated += 1
            continue

        enrichment = client.generate(prompt_card)
        card["Meaning"] = enrichment["Meaning"]
        card["Example"] = enrichment["Example"]
        indexes[normalized] = len(cards)
        cards.append(card)
        added += 1

    enriched_path.parent.mkdir(parents=True, exist_ok=True)
    enriched_path.write_text(json.dumps(cards, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "pending_items": len(pending_items),
        "already_enriched_skipped": completed_skipped,
        "duplicates_skipped": duplicates_skipped,
        "cards_updated": updated,
        "cards_added": added,
        "enriched_written": len(cards),
    }
