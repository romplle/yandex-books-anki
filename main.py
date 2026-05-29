from __future__ import annotations

import argparse
import asyncio
from typing import Any

from yandex_books_anki.anki import import_cards
from yandex_books_anki.core import (
    ENRICHED_PATH,
    PENDING_PATH,
    filter_pending_candidates,
    generate_audio_for_cards,
    load_cards,
    profile_url_for_login,
)
from yandex_books_anki.enricher import enrich_pending_cards
from yandex_books_anki.scraper import (
    collect_all_candidates,
    collect_csv_candidates,
    merge_candidates,
    write_pending,
)


def print_report(title: str, report: dict[str, Any]) -> None:
    print(title)
    for key, value in report.items():
        print(f"- {key}: {value}")


def print_pending_next_step(pending_count: int) -> None:
    if pending_count:
        print(f"Next: run `python main.py enrich` to generate Meaning and Example from {PENDING_PATH}.")
    else:
        print("No pending cards. Everything found already has Meaning and Example.")


def cmd_scrape(args: argparse.Namespace) -> int:
    profile_url = profile_url_for_login(args.login)
    candidates, report = collect_all_candidates(profile_url)
    candidates, skipped_enriched = filter_pending_candidates(candidates)
    report["already_enriched_skipped"] = skipped_enriched
    report["pending_written"] = len(candidates)
    write_pending(candidates)
    print_report("Scrape report", report)
    print_pending_next_step(len(candidates))
    return 0


def cmd_csv(_: argparse.Namespace) -> int:
    candidates, report = collect_csv_candidates()
    candidates = merge_candidates([candidates])
    candidates, skipped_enriched = filter_pending_candidates(candidates)
    report["already_enriched_skipped"] = skipped_enriched
    report["pending_written"] = len(candidates)
    write_pending(candidates)
    print_report("CSV report", report)
    print_pending_next_step(len(candidates))
    return 0


def cmd_audio(_: argparse.Namespace) -> int:
    cards = load_cards()
    report = asyncio.run(generate_audio_for_cards(cards))
    print_report("Audio report", report)
    return 0


def cmd_enrich(_: argparse.Namespace) -> int:
    report = enrich_pending_cards()
    print_report("Enrich report", report)
    return 0


def cmd_import(_: argparse.Namespace) -> int:
    cards = load_cards()
    report = import_cards(cards)
    print_report("Anki import report", report)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Yandex Books quotes to Anki vocabulary cards.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scrape_parser = subparsers.add_parser("scrape", help="Fetch public Yandex Books quotes into data/quotes_pending.json")
    scrape_parser.add_argument("login", help="Yandex Books public profile login, with or without leading @")
    subparsers.add_parser("csv", help="Read exported CSV quotes from data/quotes into data/quotes_pending.json")
    subparsers.add_parser("enrich", help="Generate Meaning and Example with GigaChat")
    subparsers.add_parser("audio", help="Generate audio for Front, Meaning, and Example")
    subparsers.add_parser("import", help="Import enriched cards into Anki")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    commands = {
        "scrape": cmd_scrape,
        "csv": cmd_csv,
        "enrich": cmd_enrich,
        "audio": cmd_audio,
        "import": cmd_import,
    }
    return commands[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
