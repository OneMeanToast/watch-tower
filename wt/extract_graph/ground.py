"""Dates and numbers stay in code. Jev never compares them."""

from __future__ import annotations

import re
from datetime import datetime

_ISO = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")
_MONEY = re.compile(
    r"\$\s*(\d{1,3}(?:,\d{3})*|\d+)(?:\.(\d+))?\s*(million|billion|thousand)?",
    re.I,
)
_SCALES = {"thousand": 1_000, "million": 1_000_000, "billion": 1_000_000_000}
_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}
_LONG_DATE = re.compile(
    r"\b(\d{1,2})\s+(January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\s+(20\d{2})\b",
    re.I,
)


def normalise_dates(text: str, published: datetime | None = None) -> list[dict]:
    """Return {surface, start, end, iso}. Regex dates win over a wider parser hit."""
    tight = _regex_dates(text)
    loose = _dateparser_dates(text, published)
    kept = list(tight)
    occupied = [(row["start"], row["end"]) for row in tight]
    for row in loose:
        surface = str(row["surface"]).strip()
        if not surface:
            continue
        if " " not in surface and surface[:1].islower():
            continue
        if any(not (row["end"] <= left or row["start"] >= right) for left, right in occupied):
            continue
        kept.append(row)
        occupied.append((row["start"], row["end"]))
    kept.sort(key=lambda row: row["start"])
    return kept


def normalise_numbers(text: str) -> list[dict]:
    rows = []
    for match in _MONEY.finditer(text or ""):
        whole = match.group(1).replace(",", "")
        frac = match.group(2) or "0"
        scale = _SCALES.get((match.group(3) or "").lower(), 1)
        value = (float(f"{whole}.{frac}")) * scale
        rows.append(
            {
                "surface": match.group(0),
                "start": match.start(),
                "end": match.end(),
                "value": value,
            }
        )
    return rows


def code_facts(text: str, published: datetime | None = None) -> str:
    lines = ["CODE FACTS (computed locally; do not recompute dates or amounts):"]
    if published is not None:
        lines.append(f"published={published.date().isoformat()}")
    for row in normalise_dates(text, published):
        lines.append(f"{row['surface']} = {row['iso']}")
    for row in normalise_numbers(text):
        lines.append(f"{row['surface']} = {row['value']:.0f}")
    if len(lines) == 1:
        lines.append("none")
    return "\n".join(lines)


def _dateparser_dates(text: str, published: datetime | None) -> list[dict]:
    try:
        from dateparser.search import search_dates
    except ImportError:
        return []
    settings = {"RETURN_AS_TIMEZONE_AWARE": False, "STRICT_PARSING": False}
    if published is not None:
        settings["RELATIVE_BASE"] = published
    try:
        matches = search_dates(text or "", languages=["en"], settings=settings) or []
    except Exception:
        return []
    rows = []
    cursor = 0
    for surface, when in matches:
        if when is None or not surface:
            continue
        start = (text or "").find(surface, cursor)
        if start < 0:
            continue
        rows.append(
            {
                "surface": surface,
                "start": start,
                "end": start + len(surface),
                "iso": when.date().isoformat(),
            }
        )
        cursor = start + len(surface)
    return rows


def _regex_dates(text: str) -> list[dict]:
    rows = []
    for match in _ISO.finditer(text or ""):
        year, month, day = (int(part) for part in match.groups())
        if not _real_day(year, month, day):
            continue
        rows.append(
            {
                "surface": match.group(0),
                "start": match.start(),
                "end": match.end(),
                "iso": f"{year:04d}-{month:02d}-{day:02d}",
            }
        )
    for match in _LONG_DATE.finditer(text or ""):
        day = int(match.group(1))
        month = _MONTHS[match.group(2).lower()]
        year = int(match.group(3))
        if not _real_day(year, month, day):
            continue
        rows.append(
            {
                "surface": match.group(0),
                "start": match.start(),
                "end": match.end(),
                "iso": f"{year:04d}-{month:02d}-{day:02d}",
            }
        )
    return rows


def _real_day(year: int, month: int, day: int) -> bool:
    try:
        datetime(year, month, day)
    except ValueError:
        return False
    return True
