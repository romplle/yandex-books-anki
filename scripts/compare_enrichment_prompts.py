from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from yandex_books_anki.enricher import (
    DEFAULT_GIGACHAT_MAX_TOKENS,
    DEFAULT_GIGACHAT_MODEL,
    DEFAULT_GIGACHAT_SCOPE,
    DEFAULT_GIGACHAT_TEMPERATURE,
    DEFAULT_GIGACHAT_VERIFY_SSL_CERTS,
    build_enrichment_prompt,
    load_dotenv,
    parse_enrichment_response,
)


CARDS_PATH = Path("data/cards_enriched.json")
RESULTS_PATH = Path("data/enrichment_prompt_comparison.json")

DEFAULT_FRONTS = ["precipice", "derailment", "accolade", "cobweb", "pitiless", "confide", "slovenly"]
DEFAULT_TIMEOUT = 90

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "Meaning": {"type": "string"},
        "Example": {"type": "string"},
    },
    "required": ["Meaning", "Example"],
    "additionalProperties": False,
}

def build_few_shot_prompt(front: str, source: str) -> str:
    return build_enrichment_prompt({"Front": front, "Source": source})


PROMPTS: dict[str, str | Callable[[str, str], str]] = {
    "baseline": (
        "Create concise Anki vocabulary card fields for a B2 English learner.\n"
        "Return only valid JSON with exactly these keys: Meaning, Example.\n"
        "Meaning must be a short English definition, not a Russian translation.\n"
        "Example must be one natural English sentence using the word or phrase.\n"
        "Do not include markdown, comments, or extra keys.\n\n"
        "Word or phrase: {front}\n"
        "Source: {source}"
    ),
    "limited": (
        "Create an Anki vocabulary card for an English learner.\n"
        "Return only one JSON object with exactly these keys: Meaning, Example.\n"
        "Use the exact key name Meaning, not Meanings.\n"
        "Meaning: 4-10 words, A2-B2 English.\n"
        "Example: 7-14 words, natural English, include the word or phrase.\n"
        "Do not use Russian. Do not add markdown. Do not add text before or after JSON.\n\n"
        "Word or phrase: {front}\n"
        "Source: {source}"
    ),
    "few_shot": build_few_shot_prompt,
}

PROMPT_NAMES = tuple(PROMPTS)


def build_configs() -> list[dict[str, Any]]:
    configs: list[dict[str, Any]] = [{"name": f"{name}_default", "prompt": name} for name in PROMPT_NAMES]

    for temperature in (DEFAULT_GIGACHAT_TEMPERATURE, 0.5, 0.7, 1):
        suffix = str(temperature).replace(".", "")
        for name in PROMPT_NAMES:
            configs.append(
                {
                    "name": f"{name}_{suffix}",
                    "prompt": name,
                    "temperature": temperature,
                    "max_tokens": DEFAULT_GIGACHAT_MAX_TOKENS,
                }
            )

    for name in PROMPT_NAMES:
        configs.append(
            {
                "name": f"{name}_03_100",
                "prompt": name,
                "temperature": DEFAULT_GIGACHAT_TEMPERATURE,
                "max_tokens": 100,
            }
        )

    for max_tokens, suffix in ((DEFAULT_GIGACHAT_MAX_TOKENS, "03_schema"), (100, "03_100_schema")):
        for name in PROMPT_NAMES:
            configs.append(
                {
                    "name": f"{name}_{suffix}",
                    "prompt": name,
                    "temperature": DEFAULT_GIGACHAT_TEMPERATURE,
                    "max_tokens": max_tokens,
                    "schema": True,
                }
            )

    return configs


CONFIGS = build_configs()


def env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "y"}


def load_card_sources(path: Path = CARDS_PATH) -> dict[str, str]:
    if not path.exists():
        return {}
    cards = json.loads(path.read_text(encoding="utf-8"))
    return {str(card.get("Front", "")).strip(): str(card.get("Source", "")).strip() for card in cards}


def parse_response(text: str, front: str) -> tuple[dict[str, str] | None, str]:
    try:
        return parse_enrichment_response(text, front), ""
    except ValueError as exc:
        return None, str(exc)


def word_count(value: str) -> int:
    return len(re.findall(r"[A-Za-z]+(?:['’-][A-Za-z]+)?", value))


def quality_warnings(front: str, parsed: dict[str, str] | None) -> list[str]:
    if not parsed:
        return []

    meaning = parsed["Meaning"]
    example = parsed["Example"]
    warnings: list[str] = []

    if meaning.lower() in {front.lower(), "short, clear, b1-b2 english"}:
        warnings.append("bad_meaning")
    if word_count(meaning) > 12:
        warnings.append("meaning_too_long")
    if word_count(example) < 7:
        warnings.append("example_too_short")
    if word_count(example) > 18:
        warnings.append("example_too_long")
    if front.lower() not in example.lower():
        warnings.append("example_missing_front")
    if re.search(r"[\u0400-\u04ff]", meaning + example):
        warnings.append("contains_cyrillic")
    return warnings


def build_prompt(front: str, source: str, prompt_name: str) -> str:
    source = source or "unknown source"
    prompt = PROMPTS[prompt_name]
    if callable(prompt):
        return prompt(front, source)
    return prompt.format(front=front, source=source)


def build_chat_payload(front: str, source: str, config: dict[str, Any]) -> dict[str, Any]:
    payload = {"messages": [{"role": "user", "content": build_prompt(front, source, config["prompt"])}]}
    if "temperature" in config:
        payload["temperature"] = config["temperature"]
    if "max_tokens" in config:
        payload["max_tokens"] = config["max_tokens"]
    if config.get("schema"):
        payload["response_format"] = {"type": "json_schema", "schema": RESPONSE_SCHEMA, "strict": True}
    return payload


def create_client() -> Any:
    from gigachat import GigaChat

    load_dotenv()
    return GigaChat(
        credentials=os.environ["GIGACHAT_CREDENTIALS"],
        scope=os.environ.get("GIGACHAT_SCOPE", DEFAULT_GIGACHAT_SCOPE),
        model=os.environ.get("GIGACHAT_MODEL", DEFAULT_GIGACHAT_MODEL),
        verify_ssl_certs=env_bool("GIGACHAT_VERIFY_SSL_CERTS", DEFAULT_GIGACHAT_VERIFY_SSL_CERTS),
        timeout=DEFAULT_TIMEOUT,
    )


def print_summary(results: list[dict[str, Any]]) -> None:
    by_config: dict[str, dict[str, int]] = {}
    for result in results:
        name = result["config"]["name"]
        stats = by_config.setdefault(name, {"total": 0, "parsed": 0, "request_errors": 0, "warning_items": 0})
        stats["total"] += 1
        stats["parsed"] += int(bool(result["parsed"]))
        stats["request_errors"] += int(bool(result["request_error"]))
        stats["warning_items"] += int(bool(result["warnings"]))

    print("\n# Summary")
    for name, stats in by_config.items():
        print(
            f"- {name}: parsed {stats['parsed']}/{stats['total']}, "
            f"request errors {stats['request_errors']}/{stats['total']}, "
            f"warnings {stats['warning_items']}/{stats['total']}"
        )


def run_comparison(fronts: list[str]) -> list[dict[str, Any]]:
    sources = load_card_sources()
    client = create_client()
    results: list[dict[str, Any]] = []

    for front in fronts:
        source = sources.get(front, "")
        print(f"\n# {front}")
        for config in CONFIGS:
            raw = ""
            request_error = ""
            try:
                response = client.chat(build_chat_payload(front, source, config))
                raw = response.choices[0].message.content
            except Exception as exc:
                request_error = f"{type(exc).__name__}: {exc}"

            parsed, parse_error = parse_response(raw, front) if raw else (None, "")
            warnings = quality_warnings(front, parsed)
            results.append(
                {
                    "front": front,
                    "source": source,
                    "config": config,
                    "raw": raw,
                    "parsed": parsed,
                    "parse_error": parse_error,
                    "request_error": request_error,
                    "warnings": warnings,
                }
            )

            print(f"\n[{config['name']}]")
            if request_error:
                print(f"Request error: {request_error}")
            elif parsed:
                print(f"Meaning: {parsed['Meaning']}")
                print(f"Example: {parsed['Example']}")
                if warnings:
                    print(f"Warnings: {', '.join(warnings)}")
            else:
                print(f"Parse error: {parse_error}")
                print(raw)

    print_summary(results)
    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compare GigaChat enrichment prompts and generation parameters.")
    parser.add_argument("fronts", nargs="*", help="Words or phrases to compare. Defaults to a small built-in list.")
    parser.add_argument("--output", type=Path, default=RESULTS_PATH, help="Where to save JSON results.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    results = run_comparison(args.fronts or DEFAULT_FRONTS)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            {
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nSaved results to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
