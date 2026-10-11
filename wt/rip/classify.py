"""Guess what a URL is before choosing an extractor."""

from __future__ import annotations

from urllib.parse import urlsplit

_X_HOSTS = {"x.com", "twitter.com", "mobile.twitter.com", "mobile.x.com"}


def classify(url: str) -> str:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = parts.path or "/"
    lower = path.lower()
    if host == "bsky.app" or host.endswith(".bsky.app"):
        if "/post/" in lower:
            return "bsky_post"
        return "bsky_profile"
    if host in _X_HOSTS and "/status/" in lower:
        return "x_post"
    if host in {"youtube.com", "m.youtube.com", "youtu.be"} or host.endswith(".youtube.com"):
        return "youtube"
    if host == "reddit.com" or host.endswith(".reddit.com"):
        return "reddit"
    if lower.endswith(".pdf"):
        return "pdf"
    if path in {"", "/"}:
        return "homepage"
    return "html"
