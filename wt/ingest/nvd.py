"""NVD CVE 2.0. Keyless requests stay inside the public rate limit."""

from __future__ import annotations

from wt.ingest.common import client
from wt.models import Item
from wt.normalize import make_item
from wt.text import parse_time

NVD_URL = "https://services.nvd.nist.gov/rest/json/cves/2.0"


def fetch_nvd(cfg: dict, http=None) -> list[Item]:
    if cfg.get("enabled") is False:
        return []
    keyword = (cfg.get("keyword") or "artificial intelligence").strip()
    per_page = int(cfg.get("results_per_page", 10))
    own = http is None
    if own:
        http = client()
    try:
        response = http.get(
            NVD_URL,
            params={"keywordSearch": keyword, "resultsPerPage": per_page},
        )
        response.raise_for_status()
        payload = response.json()
    finally:
        if own:
            http.close()
    return items_from_nvd(payload)


def items_from_nvd(payload: dict) -> list[Item]:
    items: list[Item] = []
    for wrapper in payload.get("vulnerabilities") or []:
        cve = wrapper.get("cve") or {}
        cve_id = str(cve.get("id") or "").strip()
        if not cve_id:
            continue
        text = ""
        for desc in cve.get("descriptions") or []:
            if desc.get("lang") == "en" and desc.get("value"):
                text = str(desc["value"])
                break
        published = cve.get("published")
        item = make_item(
            url=f"https://nvd.nist.gov/vuln/detail/{cve_id}",
            source="nvd",
            feed_id="cve",
            title=cve_id,
            text=text,
            ts=parse_time(published),
        )
        if item is not None:
            items.append(item)
    return items
