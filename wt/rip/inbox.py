"""A private ntfy topic can carry one URL. Anything else is ignored."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator

import httpx

_URL = re.compile(r"https?://[^\s<>'\"]+")


def accept_inbox_message(text: str) -> dict | None:
    """Return {url, mode} when the message is exactly one http(s) URL."""
    raw = (text or "").strip()
    if not raw:
        return None
    urls = _URL.findall(raw)
    if len(urls) != 1:
        return None
    url = urls[0].rstrip(").,;]")
    # A message that is only the URL, or the URL plus the word subscribe.
    leftover = _URL.sub(" ", raw)
    words = re.findall(r"[A-Za-z]+", leftover)
    if any(word.lower() not in {"subscribe", "rip"} for word in words):
        return None
    mode = "subscribe" if any(word.lower() == "subscribe" for word in words) else "rip"
    return {"url": url, "mode": mode}


def iter_ntfy(topic_url: str, *, timeout: float = 60.0) -> Iterator[str]:
    """Yield message bodies from an ntfy JSON stream. One line, one message."""
    headers = {"Accept": "application/json"}
    with httpx.stream("GET", topic_url, headers=headers, timeout=timeout) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if payload.get("event") not in {None, "message"}:
                continue
            message = payload.get("message") or payload.get("title") or ""
            if message:
                yield str(message)
