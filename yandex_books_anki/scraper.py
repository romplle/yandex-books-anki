from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from .core import (
    CSV_QUOTES_DIR,
    PENDING_PATH,
    PROFILE_LOGIN,
    PROFILE_URL,
    QuoteCandidate,
    is_generic_source,
    is_likely_english_vocabulary,
    is_quote_meta_line,
    normalize_front,
)


BOOK_QUOTES_GRAPHQL_QUERY = """
    query GetUserBookQuotes($login: String, $params: UserQuotesParamsInput!) {
  user(login: $login) {
    quotes(params: $params) {
      total
      cursor
      page {
        uuid
        createdAt
        message
        color
        progress {
          ... on QuoteTextProgress {
            cfi {
              start
              end
            }
            percent
          }
          ... on QuoteTextSerialProgress {
            cfi {
              start
              end
            }
            percent
            episode {
              uuid
              position
            }
          }
        }
        likes {
          liked
          total
        }
        comments(cursor: "", step: 0) {
          total
        }
        creator {
          ...FUser
        }
        book {
          __typename
          ... on TextBook {
            availability {
              ...FAvailability
            }
            progress {
              ...FProgress
            }
            book {
              ...FBook
            }
            readersCount
          }
          ... on TextSerial {
            episodes {
              total
            }
            availability {
              ...FAvailability
            }
            progress {
              ...FProgress
            }
            book {
              ...FBook
            }
            readersCount
          }
        }
      }
    }
  }
}
    fragment FUser on User {
  avatar {
    ...FCover
    __typename
  }
  login
  name
  uuid
  __typename
}
    fragment FCover on Cover {
  url
  ratio
  backgroundColorHex
  fromShedevrum
  __typename
}
    fragment FAvailability on Availability {
  __typename
  ageRestriction {
    __typename
    name
    value
  }
  state {
    ... on Unavailable {
      __typename
      reason
    }
    ... on Available {
      __typename
      full
    }
  }
}
    fragment FProgress on Progress {
  inLibrary
  finished
  progress
  __typename
}
    fragment FBook on Book {
  uuid
  initUuid
  name
  annotation
  ageRestriction
  editorAnnotation
  cover {
    ...FCover
  }
  topics {
    name
    totalBook
    uuid
  }
  publisher {
    uuid
    name
    avatar {
      ...FCover
    }
  }
  translators {
    uuid
    name
    narrator {
      totalBook
    }
    author {
      totalBook
    }
    author {
      totalBook
    }
    translator {
      totalBook
    }
    avatar {
      ...FCover
    }
  }
  authors {
    ...FPerson
  }
}
    fragment FPerson on Person {
  uuid
  hidden
  avatar {
    ...FCover
    __typename
  }
  author {
    totalBook
    __typename
  }
  name
  roles
  narrator {
    totalBook
    __typename
  }
  translator {
    totalBook
    __typename
  }
  __typename
}
    """


def fetch_html(url: str) -> str:
    response = requests.get(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36"
            )
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.text


def extract_book_quote_links(html_text: str, base_url: str = PROFILE_URL) -> list[str]:
    soup = BeautifulSoup(html_text, "html.parser")
    links: set[str] = set()
    for link in soup.find_all("a", href=True):
        href = link["href"]
        if re.search(r"/@b8582783786/books/[^/]+/quotes/?$", href):
            links.add(urljoin(base_url, href))
    return sorted(links)


def extract_book_uuid_from_quote_url(url: str) -> str | None:
    parts = [part for part in urlparse(url).path.split("/") if part]
    try:
        book_index = parts.index("books")
    except ValueError:
        return None
    if book_index + 1 >= len(parts):
        return None
    return parts[book_index + 1]


def extract_source(html_text: str) -> str:
    soup = BeautifulSoup(html_text, "html.parser")
    lines = [re.sub(r"\s+", " ", item).strip() for item in soup.stripped_strings]
    for index, line in enumerate(lines):
        if line == ": цитаты из книги" and index + 3 < len(lines):
            book = lines[index + 1].strip(" ,")
            author = lines[index + 3].strip(" ,")
            if book and author:
                return f"{author} — {book}"

    title = soup.find("title")
    if title and title.get_text(strip=True):
        return re.sub(r"\s+", " ", title.get_text(strip=True))

    heading = soup.find(["h1", "h2"])
    if heading and heading.get_text(strip=True):
        return re.sub(r"\s+", " ", heading.get_text(strip=True))

    return "Yandex Books"


def extract_quote_texts(html_text: str) -> list[str]:
    soup = BeautifulSoup(html_text, "html.parser")
    lines = [re.sub(r"\s+", " ", item).strip() for item in soup.stripped_strings]
    quotes: list[str] = []

    for index, line in enumerate(lines):
        if "процитировал" not in line:
            continue
        for candidate in lines[index + 1 : index + 8]:
            if not candidate or candidate == "недоступно":
                continue
            if is_quote_meta_line(candidate):
                continue
            if candidate.startswith("@") or "процитировал" in candidate:
                continue
            if candidate in {"Нравится", "Комментировать", "Поделиться", "Пожаловаться"}:
                continue
            quotes.append(candidate)
            break

    return quotes


def fetch_book_quote_texts_graphql(book_uuid: str, login: str = PROFILE_LOGIN, step: int = 100) -> list[str]:
    cursor = ""
    quotes: list[str] = []
    seen_cursors: set[str] = set()

    while True:
        response = requests.post(
            "https://books.yandex.ru/graphql-proxy",
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36"
                ),
                "Content-Type": "application/json",
                "Referer": f"https://books.yandex.ru/@{login}/books/{book_uuid}/quotes",
            },
            json={
                "query": BOOK_QUOTES_GRAPHQL_QUERY,
                "variables": {
                    "login": login,
                    "params": {
                        "cursor": cursor,
                        "step": step,
                        "filter": {"uuid": book_uuid, "type": "TEXTBOOK"},
                    },
                },
            },
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("errors"):
            raise RuntimeError(f"Yandex Books GraphQL error: {payload['errors']}")

        quote_data = payload.get("data", {}).get("user", {}).get("quotes", {})
        page = quote_data.get("page") or []
        quotes.extend(str(item.get("message", "")).strip() for item in page if item.get("message"))

        next_cursor = str(quote_data.get("cursor") or "")
        if not next_cursor or next_cursor in seen_cursors or not page:
            break
        seen_cursors.add(next_cursor)
        cursor = next_cursor

    return quotes


def collect_candidates(profile_url: str = PROFILE_URL) -> tuple[list[QuoteCandidate], dict[str, int]]:
    profile_html = fetch_html(profile_url)
    pages: list[tuple[str, str, list[str]]] = []
    graphql_pages = 0
    html_fallback_pages = 0

    for link in extract_book_quote_links(profile_html, profile_url):
        page_html = fetch_html(link)
        book_uuid = extract_book_uuid_from_quote_url(link)
        try:
            quote_texts = fetch_book_quote_texts_graphql(book_uuid) if book_uuid else extract_quote_texts(page_html)
            graphql_pages += 1 if book_uuid else 0
        except Exception:
            quote_texts = extract_quote_texts(page_html)
            html_fallback_pages += 1
        pages.append((link, page_html, quote_texts))
    pages.append((profile_url, profile_html, extract_quote_texts(profile_html)))

    seen: set[str] = set()
    candidates: list[QuoteCandidate] = []
    found = 0
    skipped = 0

    for page_url, page_html, quote_texts in pages:
        source = extract_source(page_html)
        for quote in quote_texts:
            found += 1
            normalized = normalize_front(quote)
            if normalized in seen:
                continue
            seen.add(normalized)
            if not is_likely_english_vocabulary(quote):
                skipped += 1
                continue
            candidates.append(QuoteCandidate(front=quote.strip(), source=source, page_url=page_url))

    return candidates, {
        "pages_fetched": len(pages),
        "quotes_found": found,
        "skipped_non_vocabulary": skipped,
        "pending_written": len(candidates),
        "graphql_pages": graphql_pages,
        "html_fallback_pages": html_fallback_pages,
    }


def collect_csv_candidates(csv_dir: Path = CSV_QUOTES_DIR) -> tuple[list[QuoteCandidate], dict[str, int]]:
    if not csv_dir.exists():
        return [], {"csv_files": 0, "csv_quotes_found": 0, "csv_skipped_non_vocabulary": 0}

    candidates: list[QuoteCandidate] = []
    found = 0
    skipped = 0
    files = sorted(csv_dir.glob("*.csv"))

    for path in files:
        rows = csv.DictReader(path.read_text(encoding="utf-8-sig").splitlines())
        for row in rows:
            found += 1
            front = str(row.get("content", "")).strip()
            if not is_likely_english_vocabulary(front):
                skipped += 1
                continue
            book_title = str(row.get("book_title", "")).strip()
            authors = str(row.get("book_authors", "")).strip()
            source = " — ".join(part for part in [authors, book_title] if part) or path.stem
            candidates.append(QuoteCandidate(front=front, source=source, page_url=str(path)))

    return candidates, {
        "csv_files": len(files),
        "csv_quotes_found": found,
        "csv_skipped_non_vocabulary": skipped,
    }


def merge_candidates(candidate_groups: list[list[QuoteCandidate]]) -> list[QuoteCandidate]:
    merged: list[QuoteCandidate] = []
    indexes: dict[str, int] = {}
    for candidates in candidate_groups:
        for candidate in candidates:
            normalized = normalize_front(candidate.front)
            if normalized in indexes:
                existing_index = indexes[normalized]
                if is_generic_source(merged[existing_index].source) and not is_generic_source(candidate.source):
                    merged[existing_index] = candidate
                continue
            indexes[normalized] = len(merged)
            merged.append(candidate)
    return merged


def collect_all_candidates() -> tuple[list[QuoteCandidate], dict[str, int]]:
    web_candidates, web_report = collect_candidates()
    csv_candidates, csv_report = collect_csv_candidates()
    merged = merge_candidates([web_candidates, csv_candidates])
    report = {**web_report, **csv_report}
    report["pending_written"] = len(merged)
    report["duplicates_skipped"] = len(web_candidates) + len(csv_candidates) - len(merged)
    return merged, report


def write_pending(candidates: list[QuoteCandidate], path: Path = PENDING_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [
        {
            "Front": candidate.front,
            "Meaning": "",
            "Example": "",
            "Source": candidate.source,
            "PageURL": candidate.page_url,
        }
        for candidate in candidates
    ]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
