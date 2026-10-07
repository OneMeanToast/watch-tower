"""Bluesky Jetstream tail.

The public subscribe endpoint needs no account for a live sample. This adapter
reads a bounded window per cycle (it is not a gapless archive). Enable it in
config/sources.yaml. Install extras with: pip install 'watch-tower[bluesky]'.
"""

from __future__ import annotations

import json
import logging
import time
from urllib.parse import urlencode

from wt.models import Item
from wt.normalize import make_item
from wt.text import parse_time, term_in

log = logging.getLogger("wt.ingest.bluesky")

DEFAULT_ENDPOINT = "wss://jetstream2.us-east.bsky.network/subscribe"


def collect_posts(cfg: dict) -> list[Item]:
    try:
        from websockets.sync.client import connect
    except ImportError as exc:
        raise RuntimeError(
            "Bluesky Jetstream needs the websockets package. "
            "Install it with: pip install 'watch-tower[bluesky]'"
        ) from exc

    timeout = float(cfg.get("collect_seconds", 8))
    limit = int(cfg.get("max_posts", 40))
    endpoint = cfg.get("endpoint") or DEFAULT_ENDPOINT
    dids = [did for did in (cfg.get("dids") or []) if did]
    keywords = list(cfg.get("keywords_any") or [])
    skip_replies = bool(cfg.get("skip_replies", True))
    url = _endpoint(endpoint, dids)
    items: list[Item] = []
    deadline = time.time() + timeout
    with connect(url, open_timeout=10, close_timeout=2) as socket:
        while time.time() < deadline and len(items) < limit:
            remaining = deadline - time.time()
            try:
                raw = socket.recv(timeout=max(0.2, remaining))
            except TimeoutError:
                break
            event = json.loads(raw)
            item = item_from_jetstream(
                event,
                keywords=keywords,
                skip_replies=skip_replies,
                allowed_dids=set(dids),
            )
            if item is not None:
                items.append(item)
    log.info("bluesky collected %s posts", len(items))
    return items


def _endpoint(endpoint: str, dids: list[str]) -> str:
    params = [("wantedCollections", "app.bsky.feed.post")]
    for did in dids:
        params.append(("wantedDids", did))
    join = "&" if "?" in endpoint else "?"
    return endpoint + join + urlencode(params)


def item_from_jetstream(
    event: dict,
    *,
    keywords: list[str] | None = None,
    skip_replies: bool = True,
    allowed_dids: set[str] | None = None,
) -> Item | None:
    if event.get("kind") != "commit":
        return None
    did = str(event.get("did") or "")
    if allowed_dids and did not in allowed_dids:
        return None
    commit = event.get("commit") or {}
    if commit.get("collection") != "app.bsky.feed.post":
        return None
    if commit.get("operation") != "create":
        return None
    record = commit.get("record") or {}
    if skip_replies and record.get("reply"):
        return None
    text = str(record.get("text") or "").strip()
    if not text:
        return None
    if keywords and not any(term_in(text, term) for term in keywords):
        return None
    rkey = commit.get("rkey") or ""
    url = f"https://bsky.app/profile/{did}/post/{rkey}" if did and rkey else ""
    return make_item(
        url=url,
        source="bluesky",
        feed_id=did,
        title=text[:140],
        text=text,
        ts=parse_time(record.get("createdAt")),
    )
