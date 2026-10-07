"""GDELT DOC 2.0 artlist. Free, no key, about one request every five seconds."""

from __future__ import annotations

from wt.ingest.common import client
from wt.models import Item
from wt.normalize import make_item
from wt.text import parse_time

DOC_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
MIN_INTERVAL = 5.0


def fetch_gdelt(cfg: dict, store) -> list[Item]:
    queries = cfg.get("queries") or []
    limit = int(cfg.get("max_records", 15))
    if not queries:
        return []
    items: list[Item] = []
    with client() as http:
        for index, query in enumerate(queries):
            if store is not None:
                store.wait_rate_limit("gdelt", MIN_INTERVAL)
            response = http.get(
                DOC_URL,
                params={
                    "query": query,
                    "mode": "artlist",
                    "maxrecords": limit,
                    "format": "json",
                    "sort": "datedesc",
                },
            )
            if response.status_code == 429:
                raise RuntimeError("GDELT returned 429; backing off. Try again later.")
            response.raise_for_status()
            try:
                payload = response.json()
            except Exception as exc:
                raise RuntimeError(f"GDELT did not return JSON ({exc})") from exc
            items.extend(items_from_gdelt(payload, feed_id=f"q{index + 1}"))
    return items


def items_from_gdelt(payload: dict, *, feed_id: str = "gdelt") -> list[Item]:
    items: list[Item] = []
    for article in payload.get("articles") or []:
        lang = str(article.get("language") or "")
        item = make_item(
            url=article.get("url") or "",
            source="gdelt",
            feed_id=feed_id,
            title=article.get("title") or "",
            text=article.get("title") or "",
            ts=parse_time(article.get("seendate")),
            lang="en" if lang.lower().startswith("en") else None,
        )
        if item is not None:
            items.append(item)
    return items
