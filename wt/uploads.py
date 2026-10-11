"""Manual items. A paste or a fetched page joins the same wire as a feed."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from wt.ingest.common import client
from wt.models import Item
from wt.normalize import make_item
from wt.safety import strip_hidden
from wt.text import clean_text

_TAG = re.compile(r"(?is)<(script|style).*?>.*?</\1>")
_MARKUP = re.compile(r"(?s)<[^>]+>")


class UploadError(ValueError):
    pass


def prepare_upload(*, title: str, text: str, url: str, published_at: str) -> tuple[Item, str, str]:
    body = clean_text(strip_hidden(text or ""))
    link = (url or "").strip()
    method = "paste"
    note = link
    if link and "example.test" in link:
        raise UploadError("example.test is never fetched")
    if not body and link:
        body, method = fetch_page(link)
        note = link
    if not body and not title.strip():
        raise UploadError("need a title, some text, or a URL")
    if not body:
        body = title.strip()
        method = "paste"
    when = _published(published_at)
    item = make_item(
        url=link,
        source="upload",
        feed_id="manual",
        title=title.strip() or body[:140],
        text=body,
        ts=when,
        groups=["manual"],
    )
    if item is None:
        raise UploadError("nothing left after stripping hidden text")
    return item, method, note


def fetch_page(url: str) -> tuple[str, str]:
    extracted = _trafilatura(url)
    if extracted and len(extracted.split()) >= 12:
        return clean_text(strip_hidden(extracted))[:8000], "trafilatura"
    with client() as http:
        response = http.get(url)
        response.raise_for_status()
        html = response.text
    text = clean_text(strip_hidden(_html_text(html)))
    if len(text.split()) < 12:
        raise UploadError("the page did not yield enough visible text")
    return text[:8000], "html"


def _trafilatura(url: str) -> str:
    try:
        import trafilatura
    except ImportError:
        return ""
    downloaded = trafilatura.fetch_url(url)
    if not downloaded:
        return ""
    return trafilatura.extract(downloaded) or ""


def _html_text(html: str) -> str:
    stripped = _TAG.sub(" ", html)
    stripped = _MARKUP.sub(" ", stripped)
    return stripped


def _published(value: str) -> datetime | None:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise UploadError("published date must be a timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
