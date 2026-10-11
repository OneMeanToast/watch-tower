"""CSS-selector subscriptions. robots.txt is enforced. The interval is at least 15 minutes."""

from __future__ import annotations

import time

from wt.models import Item
from wt.normalize import make_item
from wt.rip.fetch import guarded_get
from wt.rip.robots import subscription_allowed
from wt.rip.selector import apply_selector
from wt.rip.ssrf import SsrfError


def fetch_selector(cfg: dict, store) -> tuple[list[Item], list[str]]:
    feeds = cfg.get("feeds") or []
    items: list[Item] = []
    errors: list[str] = []
    if not feeds:
        return items, errors
    now = time.time()
    for feed in feeds:
        if not isinstance(feed, dict):
            continue
        if feed.get("enabled") is False:
            continue
        feed_id = str(feed.get("id") or "selector")
        url = str(feed.get("url") or "")
        selector = str(feed.get("selector") or "")
        if not url or not selector:
            errors.append(f"selector:{feed_id}: missing url or selector")
            continue
        interval = max(15, int(feed.get("interval_min") or 60))
        key = f"sub-ran:{feed_id}"
        last = store.meta_get(key) if store is not None else None
        if last and now - float(last) < interval * 60:
            continue
        try:
            allowed, reason = subscription_allowed(url)
            if not allowed:
                errors.append(f"selector:{feed_id}: {reason}")
                _stamp(store, key, now)
                continue
            page = guarded_get(url)
            found = apply_selector(
                page.text,
                base_url=url,
                selector=selector,
                link_selector=str(feed.get("link_selector") or "a"),
                title_selector=str(feed.get("title_selector") or ""),
            )
        except SsrfError as exc:
            errors.append(f"selector:{feed_id}: {exc}")
            _stamp(store, key, now)
            continue
        except Exception as exc:
            errors.append(f"selector:{feed_id}: {exc}")
            _stamp(store, key, now)
            continue
        for row in found:
            item = make_item(
                url=row.get("url") or url,
                source="selector",
                feed_id=feed_id,
                title=row.get("title") or "",
                text=row.get("title") or "",
                groups=["manual"],
            )
            if item is not None:
                items.append(item)
        _stamp(store, key, now)
    return items, errors


def due_feeds(feeds: list, store) -> list:
    """Skip user-added feeds that ran inside their interval. Curated feeds always run."""
    kept = []
    now = time.time()
    for feed in feeds:
        if not isinstance(feed, dict) or not feed.get("added_via"):
            kept.append(feed)
            continue
        interval = max(15, int(feed.get("interval_min") or 60))
        key = f"sub-ran:{feed.get('id')}"
        last = store.meta_get(key) if store is not None else None
        if last and now - float(last) < interval * 60:
            continue
        kept.append(feed)
    return kept


def stamp_feeds(feeds: list, store) -> None:
    if store is None:
        return
    now = time.time()
    for feed in feeds:
        if isinstance(feed, dict) and feed.get("added_via") and feed.get("id"):
            store.meta_set(f"sub-ran:{feed.get('id')}", str(now))


def _stamp(store, key: str, now: float) -> None:
    if store is not None:
        store.meta_set(key, str(now))
