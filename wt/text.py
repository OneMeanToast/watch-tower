"""Token and text helpers shared by the embedder, prefilter, and lexical decider."""

from __future__ import annotations

import hashlib
import html
import re
from datetime import datetime, timezone

_TAG = re.compile(r"(?is)<(script|style).*?>.*?</\1>")
_TAGS = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
_TOKEN = re.compile(r"[a-z0-9][a-z0-9+\-\.]{1,}")

STOPWORDS = frozenset(
    """
    a an the of and or to for with on in is are was were be been being this that
    these those it its as by from at into than then just also about over after
    before but if so we they you our your their them he she his her not no nor
    do does did doing have has had what when where who which how why who
    can could should would will may might must than via per
    """.split()
)


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    text = html.unescape(str(value))
    text = _TAG.sub(" ", text)
    text = _TAGS.sub(" ", text)
    return _WS.sub(" ", text).strip()


def tokenize(text: str) -> list[str]:
    words = [w for w in _TOKEN.findall(text.lower()) if w not in STOPWORDS and len(w) > 1]
    bigrams = [f"{a}_{b}" for a, b in zip(words, words[1:])]
    return words + bigrams


def token_set(text: str) -> set[str]:
    return set(tokenize(text))


def example_overlap(item_tokens: set[str], example: str) -> float:
    """Share of an example's tokens that appear in the item (containment)."""
    example_tokens = token_set(example)
    if not example_tokens:
        return 0.0
    return len(item_tokens & example_tokens) / len(example_tokens)


def max_overlap(item_tokens: set[str], examples: list[str]) -> float:
    if not examples:
        return 0.0
    return max(example_overlap(item_tokens, example) for example in examples)


def term_in(text: str, term: str) -> bool:
    """Case-insensitive match. Short alphabetic terms use a word boundary."""
    hay = text.lower()
    needle = term.lower().strip()
    if not needle:
        return False
    if len(needle) <= 5 and re.fullmatch(r"[a-z0-9]+", needle):
        return re.search(rf"\b{re.escape(needle)}\b", hay) is not None
    return needle in hay


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_time(value: str | None) -> datetime:
    if not value:
        return utcnow()
    text = value.strip()
    if re.fullmatch(r"\d{8}T\d{6}Z", text):
        return datetime.strptime(text, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return utcnow()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def guess_lang(text: str) -> str:
    tokens = set(_TOKEN.findall(text.lower()))
    english = tokens & {
        "the", "and", "of", "to", "in", "for", "on", "with", "is", "was", "a", "an",
    }
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return "und"
    ascii_ratio = sum(ch.isascii() for ch in letters) / len(letters)
    if len(english) >= 2 or (ascii_ratio > 0.9 and english):
        return "en"
    if ascii_ratio > 0.95:
        return "en"
    return "und"
