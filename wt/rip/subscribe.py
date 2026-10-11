"""Write a subscription into config/sources.local.yaml and keep the curated file."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from wt.normalize import canonical_url
from wt.text import sha256_text


def local_path(sources_path: Path) -> Path:
    return sources_path.parent / "sources.local.yaml"


def save_subscription(
    sources_path: Path,
    *,
    page_url: str,
    feed_url: str = "",
    selector: str = "",
    link_selector: str = "a",
    title_selector: str = "",
    interval_min: int = 60,
    via: str = "web",
) -> dict:
    """Append one feed. Returns the catalog-shaped row that was written."""
    interval = max(15, int(interval_min or 60))
    target = local_path(sources_path)
    data = _read(target)
    when = datetime.now(timezone.utc).isoformat()
    if feed_url:
        url = canonical_url(feed_url) or feed_url.strip()
        feed_id = _feed_id(url)
        feeds = data.setdefault("rss", {}).setdefault("feeds", [])
        existing = _find(feeds, url)
        if existing:
            return _row(existing, kind="rss")
        entry = {
            "id": feed_id,
            "url": url,
            "group": "manual",
            "enabled": True,
            "interval_min": interval,
            "added_via": via,
            "added_at": when,
            "page": page_url,
        }
        feeds.append(entry)
        _write(target, data)
        return _row(entry, kind="rss")
    if not selector:
        raise ValueError("a feed URL or a CSS selector is required")
    url = canonical_url(page_url) or page_url.strip()
    feed_id = _feed_id(url)
    feeds = data.setdefault("selector", {}).setdefault("feeds", [])
    existing = _find(feeds, url)
    if existing:
        return _row(existing, kind="selector")
    entry = {
        "id": feed_id,
        "url": url,
        "selector": selector,
        "link_selector": link_selector or "a",
        "title_selector": title_selector or "",
        "group": "manual",
        "enabled": True,
        "interval_min": interval,
        "added_via": via,
        "added_at": when,
    }
    feeds.append(entry)
    _write(target, data)
    return _row(entry, kind="selector")


def _row(entry: dict, *, kind: str) -> dict:
    return {
        "id": f"{kind}:{entry.get('id')}",
        "feed_id": entry.get("id") or "",
        "kind": kind,
        "url": entry.get("url") or "",
        "selector": entry.get("selector") or "",
        "interval_min": int(entry.get("interval_min") or 60),
        "added_via": entry.get("added_via") or "",
        "group": entry.get("group") or "manual",
    }


def _find(feeds: list, url: str) -> dict | None:
    for feed in feeds:
        if isinstance(feed, dict) and (feed.get("url") or "") == url:
            return feed
    return None


def _feed_id(url: str) -> str:
    parts = urlsplit(url)
    host = (parts.hostname or "feed").lower()
    if host.startswith("www."):
        host = host[4:]
    slug = re.sub(r"[^a-z0-9]+", "-", host).strip("-") or "feed"
    tail = sha256_text(canonical_url(url) or url)[:6]
    return f"{slug}-{tail}"[:48]


def _read(path: Path) -> dict:
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must be a YAML mapping")
    return data


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
