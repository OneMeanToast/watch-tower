"""CISA Known Exploited Vulnerabilities catalog. No API key."""

from __future__ import annotations

from wt.ingest.common import client
from wt.models import Item
from wt.normalize import make_item
from wt.text import parse_time

KEV_URL = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"


def fetch_cisa_kev(cfg: dict, http=None) -> list[Item]:
    if cfg.get("enabled") is False:
        return []
    limit = int(cfg.get("limit", 20))
    own = http is None
    if own:
        http = client()
    try:
        response = http.get(KEV_URL)
        response.raise_for_status()
        payload = response.json()
    finally:
        if own:
            http.close()
    return items_from_kev(payload, limit=limit)


def items_from_kev(payload: dict, *, limit: int = 20) -> list[Item]:
    items: list[Item] = []
    for row in (payload.get("vulnerabilities") or [])[:limit]:
        cve = str(row.get("cveID") or "").strip()
        name = str(row.get("vulnerabilityName") or "").strip()
        text = str(row.get("shortDescription") or "").strip()
        if not cve and not name:
            continue
        item = make_item(
            url=f"https://www.cisa.gov/known-exploited-vulnerabilities-catalog?search_api_fulltext={cve}",
            source="cisa_kev",
            feed_id="kev",
            title=f"{cve} {name}".strip(),
            text=text,
            ts=parse_time(row.get("dateAdded")),
        )
        if item is not None:
            items.append(item)
    return items
