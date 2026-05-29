from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Any

from yandex_books_anki.anki import (
    anki_request,
    build_anki_note,
    ensure_anki_deck_and_model,
    existing_notes_by_front,
    import_cards,
    note_fields_from_info,
    store_audio_in_anki,
    store_card_audio_in_anki,
    update_anki_model_templates,
    update_anki_note_fields,
)
from yandex_books_anki.core import (
    ANKI_CONNECT_URL,
    AUDIO_DIR,
    AUDIO_FIELD_SPECS,
    BACK_TEMPLATE,
    CARD_CSS,
    CSV_QUOTES_DIR,
    DATA_DIR,
    DECK_NAME,
    EDGE_TTS_EXAMPLE_VOICE,
    EDGE_TTS_VOICE,
    ENRICHED_PATH,
    FIELDS,
    FRONT_TEMPLATE,
    MODEL_NAME,
    PENDING_PATH,
    QuoteCandidate,
    canonical_front,
    set_spacy_nlp_for_tests,
    filter_pending_candidates,
    generate_audio_for_cards,
    is_generic_source,
    is_likely_english_vocabulary,
    is_quote_meta_line,
    load_cards,
    normalize_card,
    profile_url_for_login,
    safe_audio_filename,
    sound_field,
    sound_filename_from_field,
    tts_voice_for_label,
)
from yandex_books_anki.enricher import (
    GigaChatMeaningClient,
    build_enrichment_prompt,
    enrich_pending_cards,
    env_bool,
    load_dotenv,
    parse_enrichment_response,
)
from yandex_books_anki.scraper import (
    BOOK_QUOTES_GRAPHQL_QUERY,
    collect_all_candidates,
    collect_candidates,
    collect_csv_candidates,
    extract_book_quote_links,
    extract_book_uuid_from_quote_url,
    extract_quote_texts,
    extract_source,
    fetch_book_quote_texts_graphql,
    fetch_html,
    merge_candidates,
    write_pending,
)


def print_report(title: str, report: dict[str, Any]) -> None:
    print(title)
    for key, value in report.items():
        print(f"- {key}: {value}")


def print_pending_next_step(pending_count: int) -> None:
    if pending_count:
        print(f"Next: fill Meaning and Example in {ENRICHED_PATH} using {PENDING_PATH} as input.")
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
    subparsers.add_parser("audio", help="Generate Front audio for data/cards_enriched.json")
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
    try:
        return commands[args.command](args)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
