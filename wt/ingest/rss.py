"""RSS and Atom adapters. feedparser does the format handling."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import feedparser

from wt.ingest.common import client
from wt.models import Item
from wt.normalize import make_item


def fetch_rss(cfg: dict) -> tuple[list[Item], list[str]]:
    feeds = cfg.get("feeds") or []
    limit = int(cfg.get("per_feed", 30))
    items: list[Item] = []
    errors: list[str] = []
    if not feeds:
        return items, errors
    with client() as http:
        for feed in feeds:
            feed_id = str(feed.get("id") or "rss")
            url = feed.get("url")
            if not url:
                errors.append(f"rss:{feed_id}: missing url")
                continue
            groups = _groups(feed)
            if feed.get("added_via"):
                from wt.rip.robots import subscription_allowed
                from wt.rip.ssrf import SsrfError

                try:
                    allowed, reason = subscription_allowed(str(url))
                except SsrfError as exc:
                    errors.append(f"rss:{feed_id}: {exc}")
                    continue
                if not allowed:
                    errors.append(f"rss:{feed_id}: {reason}")
                    continue
            try:
                response = http.get(url)
                response.raise_for_status()
                items.extend(
                    items_from_xml(
                        response.content,
                        source="rss",
                        feed_id=feed_id,
                        limit=limit,
                        groups=groups,
                    )
                )
            except Exception as exc:
                errors.append(f"rss:{feed_id}: {exc}")
    return items, errors


def items_from_xml(
    raw: str | bytes,
    *,
    source: str,
    feed_id: str,
    limit: int = 30,
    groups: list[str] | None = None,
) -> list[Item]:
    parsed = feedparser.parse(raw)
    entries = list(parsed.entries or [])
    if not entries and getattr(parsed, "bozo", False):
        raise RuntimeError(getattr(parsed, "bozo_exception", "unreadable feed"))
    items: list[Item] = []
    for entry in entries[:limit]:
        item = _entry_to_item(entry, source=source, feed_id=feed_id, groups=groups)
        if item is not None:
            items.append(item)
    return items


def _groups(feed: dict) -> list[str]:
    found: list[str] = []
    single = feed.get("group")
    if single:
        found.append(str(single))
    extra = feed.get("groups") or []
    if isinstance(extra, str):
        extra = [extra]
    for group in extra:
        text = str(group).strip()
        if text and text not in found:
            found.append(text)
    return found


def _entry_to_item(
    entry: Any, *, source: str, feed_id: str, groups: list[str] | None = None
) -> Item | None:
    content = ""
    blocks = entry.get("content") or []
    if blocks and isinstance(blocks, list):
        content = blocks[0].get("value") or ""
    if not content:
        content = entry.get("summary") or entry.get("description") or ""
    authors: list[str] = []
    for author in entry.get("authors") or []:
        name = author.get("name") if isinstance(author, dict) else str(author)
        if name:
            authors.append(str(name))
    if not authors and entry.get("author"):
        authors.append(str(entry.get("author")))
    stamp = entry.get("published_parsed") or entry.get("updated_parsed")
    ts = None
    if stamp:
        ts = datetime(*stamp[:6], tzinfo=timezone.utc)
    return make_item(
        url=entry.get("link") or "",
        source=source,
        feed_id=feed_id,
        title=entry.get("title") or "",
        text=content,
        ts=ts,
        authors=authors,
        groups=groups,
    )
