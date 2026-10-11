"""GDELT GKG and events export parsers.

The live zip files are large, so download stays off unless gkg_enabled or
events_enabled is set. The parsers run on a local TSV either way.
"""

from __future__ import annotations

from datetime import datetime, timezone

from wt.models import Item
from wt.normalize import make_item

# Last-30-days English GKG. Left unused while the flag is false.
GKG_URL = "http://data.gdeltproject.org/gdeltv2/lastupdate.txt"


def fetch_gdelt_graphs(cfg: dict) -> list[Item]:
    """Live download is intentionally off. Parsing a provided TSV is separate."""
    if not cfg or not (cfg.get("gkg_enabled") or cfg.get("events_enabled")):
        return []
    return []


def items_from_gkg(text: str, *, limit: int = 20) -> list[Item]:
    items: list[Item] = []
    for line in (text or "").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        cols = line.split("\t")
        if len(cols) < 5:
            continue
        url = cols[4].strip()
        if not url.startswith("http"):
            continue
        themes = cols[7] if len(cols) > 7 else ""
        title = themes.split(";")[0].replace("_", " ")[:180] or url
        item = make_item(
            url=url,
            source="gdelt_gkg",
            feed_id="gkg",
            title=title,
            text=themes.replace(";", " "),
            ts=_gkg_time(cols[1]),
        )
        if item is not None:
            items.append(item)
        if len(items) >= limit:
            break
    return items


def items_from_events(text: str, *, limit: int = 20) -> list[Item]:
    items: list[Item] = []
    for line in (text or "").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        cols = line.split("\t")
        url = next((col.strip() for col in cols if col.strip().startswith("http")), "")
        if not url:
            continue
        when = _gkg_time(cols[1]) if len(cols) > 1 else None
        actor = cols[6].strip() if len(cols) > 6 else ""
        item = make_item(
            url=url,
            source="gdelt_events",
            feed_id="events",
            title=actor or url,
            text=" ".join(col for col in cols[6:12] if col.strip()) or url,
            ts=when,
        )
        if item is not None:
            items.append(item)
        if len(items) >= limit:
            break
    return items


def _gkg_time(value: str):
    raw = (value or "").strip()
    if len(raw) >= 8 and raw[:8].isdigit():
        stamp = raw[:14].ljust(14, "0")
        try:
            return datetime.strptime(stamp, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None
