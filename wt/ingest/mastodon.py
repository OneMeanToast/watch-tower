"""Public Mastodon hashtag timelines. No token required on most servers."""

from __future__ import annotations

from wt.ingest.common import client
from wt.models import Item
from wt.normalize import make_item
from wt.text import parse_time


def fetch_mastodon(cfg: dict) -> tuple[list[Item], list[str]]:
    instances = cfg.get("instances") or ["https://mastodon.social"]
    hashtags = cfg.get("hashtags") or []
    limit = int(cfg.get("limit", 20))
    items: list[Item] = []
    errors: list[str] = []
    with client() as http:
        for instance in instances:
            for tag in hashtags:
                url = instance.rstrip("/") + f"/api/v1/timelines/tag/{tag}"
                try:
                    response = http.get(url, params={"limit": limit})
                    response.raise_for_status()
                    items.extend(
                        items_from_mastodon(response.json(), instance=instance, tag=tag)
                    )
                except Exception as exc:
                    errors.append(f"mastodon:{instance}:{tag}: {exc}")
    return items, errors


def items_from_mastodon(payload: list, *, instance: str, tag: str) -> list[Item]:
    items: list[Item] = []
    host = instance.rstrip("/").split("//")[-1]
    for status in payload or []:
        account = status.get("account") or {}
        acct = account.get("acct") or ""
        item = make_item(
            url=status.get("url") or "",
            source="mastodon",
            feed_id=f"{host}:{tag}",
            title=acct or tag,
            text=status.get("content") or "",
            ts=parse_time(status.get("created_at")),
            authors=[acct] if acct else [],
        )
        if item is not None:
            items.append(item)
    return items
