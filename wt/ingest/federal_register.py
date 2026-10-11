"""Federal Register articles JSON. The RSS feed in sources.yaml stays as well."""

from __future__ import annotations

from wt.ingest.common import client
from wt.models import Item
from wt.normalize import make_item
from wt.text import parse_time

FR_URL = "https://www.federalregister.gov/api/v1/articles.json"


def fetch_federal_register(cfg: dict, http=None) -> list[Item]:
    if cfg.get("enabled") is False:
        return []
    term = (cfg.get("term") or "artificial intelligence").strip()
    per_page = int(cfg.get("per_page", 10))
    own = http is None
    if own:
        http = client()
    try:
        response = http.get(
            FR_URL,
            params={
                "conditions[term]": term,
                "per_page": per_page,
                "order": "newest",
            },
        )
        response.raise_for_status()
        payload = response.json()
    finally:
        if own:
            http.close()
    return items_from_federal_register(payload)


def items_from_federal_register(payload: dict) -> list[Item]:
    items: list[Item] = []
    for article in payload.get("results") or []:
        url = article.get("html_url") or article.get("url") or ""
        title = article.get("title") or ""
        text = article.get("abstract") or article.get("title") or ""
        item = make_item(
            url=url,
            source="federal_register",
            feed_id="articles",
            title=title,
            text=text,
            ts=parse_time(article.get("publication_date")),
        )
        if item is not None:
            items.append(item)
    return items
