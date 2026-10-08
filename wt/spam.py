"""Drop repeated Bluesky posts before they reach the classifier.

A firehose sample often contains the same text from many accounts. Those
clusters are discarded. Other sources are left alone.
"""

from __future__ import annotations

import re
from collections import Counter

from wt.models import Item

_URL = re.compile(r"https?://\S+")
_NOISE = re.compile(r"[^a-z0-9\s]+")
_WS = re.compile(r"\s+")


def spam_key(text: str) -> str:
    cleaned = _URL.sub(" ", (text or "").lower())
    cleaned = _NOISE.sub(" ", cleaned)
    return _WS.sub(" ", cleaned).strip()


def filter_spam(items: list[Item], *, min_cluster: int = 3) -> tuple[list[Item], int]:
    if min_cluster < 2:
        return list(items), 0
    bluesky = [item for item in items if item.source == "bluesky"]
    others = [item for item in items if item.source != "bluesky"]
    counts = Counter(spam_key(item.document()) for item in bluesky)
    kept: list[Item] = []
    dropped = 0
    for item in bluesky:
        key = spam_key(item.document())
        if key and counts[key] >= min_cluster:
            dropped += 1
            continue
        kept.append(item)
    return others + kept, dropped
