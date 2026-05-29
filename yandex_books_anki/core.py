from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


YANDEX_BOOKS_BASE_URL = "https://books.yandex.ru"

DECK_NAME = "Yandex Books Vocabulary"
MODEL_NAME = "YandexBooksVocabulary"
ANKI_CONNECT_URL = "http://localhost:8765"

EDGE_TTS_DEFAULT_VOICE = "en-US-JennyNeural"
EDGE_TTS_EXAMPLE_VOICE = "en-US-GuyNeural"

DATA_DIR = Path("data")
PENDING_PATH = DATA_DIR / "quotes_pending.json"
ENRICHED_PATH = DATA_DIR / "cards_enriched.json"
AUDIO_DIR = DATA_DIR / "audio"
CSV_QUOTES_DIR = DATA_DIR / "quotes"

FIELDS = [
    "Front",
    "Meaning",
    "Example",
    "Sound",
    "Sound_Meaning",
    "Sound_Example",
    "Source",
]

AUDIO_FIELD_SPECS = [
    ("Front", "Sound", "front"),
    ("Meaning", "Sound_Meaning", "meaning"),
    ("Example", "Sound_Example", "example"),
]

FRONT_TEMPLATE = """<div style='font-family: Arial; font-size: 70px;color:#FF80DD;'>{{Front}}</div>
<div style='font-size: 0px; text-align: right;'>{{Sound}}</div>"""

BACK_TEMPLATE = """{{FrontSide}}
<br><br>
<div style='box-sizing: border-box; width: 100%; padding: 0 42px;'>
  <span>
    <table style='width: 100%; max-width: 900px; margin: 0 auto;'>
      <tr>
        <td style='font-family: Arial; font-size: 30px;color:#00aaaa; text-align:left;'>
          {{Meaning}}
          <br><br>
          <div style='font-family: Arial; font-size: 30px;color:#9CFFFA; text-align:left;'>
            &nbsp;→&nbsp;{{Example}}
          </div>
        </td>
      </tr>
    </table>
  </span>
</div>
<div style='font-size: 0px; text-align: right;'>{{Sound_Meaning}}</div>
<div style='font-size: 0px; text-align: right;'>{{Sound_Example}}</div>
<div style='box-sizing: border-box; width: 100%; padding: 0 42px;'>
  <div style='font-family: Arial; font-size:20px;color:#aaaa00; text-align: left; max-width: 900px; margin: 0 auto;'>{{Source}}</div>
</div>"""

CARD_CSS = """.card {
  font-family: arial;
  font-size: 20px;
  text-align: center;
  color: Black;
  background-color: Black;
}"""


@dataclass(frozen=True)
class QuoteCandidate:
    front: str
    source: str
    page_url: str


def profile_url_for_login(login: str) -> str:
    normalized = login.strip().lstrip("@")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", normalized):
        raise ValueError("Yandex Books login must contain only letters, digits, underscores, or hyphens.")
    return f"{YANDEX_BOOKS_BASE_URL}/@{normalized}/quotes"


SPACY_MODEL = "en_core_web_sm"
_SPACY_NLP: Any = None


def set_spacy_nlp_for_tests(nlp: Any) -> None:
    global _SPACY_NLP
    _SPACY_NLP = nlp


def spacy_nlp() -> Any:
    global _SPACY_NLP
    if _SPACY_NLP is not None:
        return _SPACY_NLP

    import spacy

    _SPACY_NLP = spacy.load(SPACY_MODEL, disable=["parser", "ner"])
    return _SPACY_NLP


def canonical_token_text(token: Any) -> str:
    lemma = str(getattr(token, "lemma_", "") or "").strip()
    text = str(getattr(token, "text", "") or "").strip()
    if not lemma or lemma == "-PRON-":
        lemma = text
    return lemma.lower()


def canonical_front(value: str) -> str:
    value = html.unescape(value).replace("\u00a0", " ")
    value = value.strip(" \t\r\n\"'`.,;:!?")
    value = re.sub(r"\s+", " ", value).lower()
    if not value:
        return ""

    lemmas: list[str] = []
    for token in spacy_nlp()(value):
        if getattr(token, "is_space", False) or getattr(token, "is_punct", False):
            continue
        lemma = canonical_token_text(token).strip(" \t\r\n\"'`.,;:!?")
        if lemma:
            lemmas.append(lemma)
    return re.sub(r"\s+", " ", " ".join(lemmas)).strip()


def is_likely_english_vocabulary(value: str) -> bool:
    text = value.strip()
    if not text or len(text) > 80:
        return False
    if re.search(r"[\u0400-\u04ff]", text):
        return False
    if re.search(r"[.!?;:]", text):
        return False
    words = re.findall(r"[A-Za-z]+(?:['’-][A-Za-z]+)?", text)
    if not words or len(words) > 5:
        return False
    if len(words) > 1 and len(words[-1]) == 1:
        return False
    compact = re.sub(r"[\s'’\-,]", "", text)
    return bool(compact) and compact.isascii() and compact.isalpha()


def is_quote_meta_line(value: str) -> bool:
    if value in {"сегодня", "вчера", "позавчера", "в прошлом месяце"}:
        return True
    return bool(re.fullmatch(r"\d+\s+(?:день|дня|дней|месяц|месяца|месяцев|год|года|лет)\s+назад", value))


def is_generic_source(source: str) -> bool:
    return source.startswith("Цитаты ") or source == "Yandex Books"


def load_cards(path: Path = ENRICHED_PATH) -> list[dict[str, str]]:
    cards = json.loads(path.read_text(encoding="utf-8"))
    return [normalize_card(item) for item in cards]


def clean_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def normalize_card(item: dict[str, Any]) -> dict[str, str]:
    card = {field: clean_text(item.get(field, "")) for field in FIELDS}
    card["Front"] = card["Front"] or clean_text(item.get("front", ""))
    card["Front"] = canonical_front(card["Front"])
    card["Meaning"] = card["Meaning"] or clean_text(item.get("meaning", ""))
    card["Example"] = card["Example"] or clean_text(item.get("example", ""))
    card["Source"] = card["Source"] or clean_text(item.get("source", ""))
    if not card["Front"]:
        raise ValueError(f"Card is missing Front: {item}")
    return card


def filter_pending_candidates(
    candidates: list[QuoteCandidate],
    enriched_path: Path = ENRICHED_PATH,
) -> tuple[list[QuoteCandidate], int]:
    if not enriched_path.exists():
        return candidates, 0

    complete_fronts = {
        card["Front"]
        for card in load_cards(enriched_path)
        if card["Meaning"] and card["Example"]
    }
    pending = [candidate for candidate in candidates if canonical_front(candidate.front) not in complete_fronts]
    return pending, len(candidates) - len(pending)


def tts_voice_for_label(label: str) -> str:
    if label == "example":
        return EDGE_TTS_EXAMPLE_VOICE
    return EDGE_TTS_DEFAULT_VOICE


def safe_audio_filename(value: str, label: str = "front") -> str:
    text = value.strip()
    normalized = canonical_front(value)
    slug = re.sub(r"[^a-z0-9]+", "_", normalized).strip("_")[:40] or "audio"
    digest_source = f"{label}:{tts_voice_for_label(label)}:{text}"
    digest = hashlib.sha1(digest_source.encode("utf-8")).hexdigest()[:10]
    prefix = "yb" if label == "front" else f"yb_{label}"
    return f"{prefix}_{slug}_{digest}.mp3"


def sound_field(filename: str) -> str:
    return f"[sound:{filename}]"


def sound_filename_from_field(value: str) -> str | None:
    match = re.fullmatch(r"\[sound:([^\]]+)\]", value.strip())
    return match.group(1) if match else None


async def generate_audio_for_cards(cards: list[dict[str, str]]) -> dict[str, int]:
    import edge_tts

    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    generated = 0
    reused = 0

    for card in cards:
        for text_field, sound_field_name, label in AUDIO_FIELD_SPECS:
            text = card.get(text_field, "").strip()
            if not text:
                continue
            filename = safe_audio_filename(text, label)
            output_path = AUDIO_DIR / filename
            card[sound_field_name] = sound_field(filename)
            if output_path.exists():
                reused += 1
                continue
            communicate = edge_tts.Communicate(text, tts_voice_for_label(label))
            await communicate.save(str(output_path))
            generated += 1

    ENRICHED_PATH.parent.mkdir(parents=True, exist_ok=True)
    ENRICHED_PATH.write_text(json.dumps(cards, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"audio_generated": generated, "audio_reused": reused}
