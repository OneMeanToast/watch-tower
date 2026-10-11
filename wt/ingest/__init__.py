"""Fetch items from configured sources. One bad source does not stop the others."""

from __future__ import annotations

import logging

from wt.ingest.arxiv import fetch_arxiv
from wt.ingest.bluesky import collect_posts
from wt.ingest.gdelt import fetch_gdelt
from wt.ingest.mastodon import fetch_mastodon
from wt.ingest.openalex import fetch_openalex
from wt.ingest.rss import fetch_rss
from wt.ingest.selector import due_feeds, fetch_selector, stamp_feeds
from wt.ingest.worldmonitor import load_catalog
from wt.ingest.x_paid import XPaidDisabled, fetch_x
from wt.models import Item

log = logging.getLogger("wt.ingest")


def ingest_all(sources: dict, store) -> tuple[list[Item], list[str]]:
    items: list[Item] = []
    errors: list[str] = []
    rss_cfg = sources.get("rss") or {}
    rss_feeds = list(rss_cfg.get("feeds") or [])
    due = due_feeds(rss_feeds, store)
    try:
        found, feed_errors = fetch_rss({**rss_cfg, "feeds": due})
        items.extend(found)
        errors.extend(feed_errors)
        stamp_feeds(due, store)
    except Exception as exc:
        errors.append(f"rss: {exc}")

    try:
        found, selector_errors = fetch_selector(sources.get("selector") or {}, store)
        items.extend(found)
        errors.extend(selector_errors)
    except Exception as exc:
        errors.append(f"selector: {exc}")

    try:
        items.extend(fetch_arxiv(sources.get("arxiv") or {}))
    except Exception as exc:
        errors.append(f"arxiv: {exc}")

    try:
        items.extend(fetch_gdelt(sources.get("gdelt") or {}, store))
    except Exception as exc:
        errors.append(f"gdelt: {exc}")

    try:
        items.extend(fetch_openalex(sources.get("openalex") or {}))
    except Exception as exc:
        errors.append(f"openalex: {exc}")

    bluesky_cfg = sources.get("bluesky") or {}
    if bluesky_cfg.get("enabled"):
        try:
            items.extend(collect_posts(bluesky_cfg))
        except Exception as exc:
            errors.append(f"bluesky: {exc}")

    mastodon_cfg = sources.get("mastodon") or {}
    if mastodon_cfg.get("hashtags"):
        try:
            found, tag_errors = fetch_mastodon(mastodon_cfg)
            items.extend(found)
            errors.extend(tag_errors)
        except Exception as exc:
            errors.append(f"mastodon: {exc}")

    worldmonitor_cfg = sources.get("worldmonitor") or {}
    if worldmonitor_cfg.get("enabled"):
        try:
            feeds = load_catalog(worldmonitor_cfg)
            found, feed_errors = fetch_rss(
                {"feeds": feeds, "per_feed": int(worldmonitor_cfg.get("per_feed", 5))}
            )
            items.extend(found)
            errors.extend(f"worldmonitor: {err}" for err in feed_errors)
        except Exception as exc:
            errors.append(f"worldmonitor: {exc}")

    x_cfg = sources.get("x") or {}
    if x_cfg.get("enabled"):
        try:
            items.extend(fetch_x(x_cfg))
        except XPaidDisabled as exc:
            errors.append(f"x: {exc}")

    for error in errors:
        log.warning("ingest: %s", error)
    return items, errors
