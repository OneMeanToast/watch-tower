"""Full text for likely hits, mainly GDELT headlines.

Extraction is off in demo mode. Hosts under example.test are never fetched.
Extracted text is untrusted and is stripped before it replaces the body.
"""

from __future__ import annotations

import logging

from wt.models import Item
from wt.safety import strip_hidden
from wt.text import clean_text

log = logging.getLogger("wt.extract")

_SHORT_WORDS = 40


def needs_fulltext(item: Item) -> bool:
    if "example.test" in (item.url or ""):
        return False
    if not item.url:
        return False
    words = len((item.text or "").split())
    if item.source == "gdelt":
        return True
    return words < _SHORT_WORDS


def enrich_item(item: Item, *, enabled: bool, fetcher=None) -> Item:
    if not enabled or not needs_fulltext(item):
        return item
    fetch = fetcher or _trafilatura_fetch
    try:
        raw = fetch(item.url)
    except Exception as exc:
        log.info("full text skipped for %s (%s)", item.url, exc)
        return item
    if not raw:
        return item
    visible = clean_text(strip_hidden(raw))
    if len(visible.split()) < 12:
        return item
    return item.model_copy(update={"text": visible[:8000]})


def _trafilatura_fetch(url: str) -> str:
    try:
        import trafilatura
    except ImportError:
        log.info("trafilatura is not installed; leaving the headline in place")
        return ""
    downloaded = trafilatura.fetch_url(url)
    if not downloaded:
        return ""
    return trafilatura.extract(downloaded) or ""
