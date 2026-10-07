"""arXiv Atom API (export.arxiv.org). No API key. Be polite with max_results."""

from __future__ import annotations

from urllib.parse import urlencode

from wt.ingest.common import client
from wt.ingest.rss import items_from_xml
from wt.models import Item


def fetch_arxiv(cfg: dict) -> list[Item]:
    categories = cfg.get("categories") or ["cs.AI"]
    limit = int(cfg.get("max_results", 25))
    search = " OR ".join(f"cat:{category}" for category in categories)
    query = urlencode(
        {
            "search_query": search,
            "start": 0,
            "max_results": limit,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
    )
    url = f"https://export.arxiv.org/api/query?{query}"
    with client() as http:
        response = http.get(url)
        response.raise_for_status()
        body = response.content
    feed_id = ",".join(categories)
    return items_from_xml(body, source="arxiv", feed_id=feed_id, limit=limit)
