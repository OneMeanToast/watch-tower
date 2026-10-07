"""Canonical items, hashes, and near-duplicate detection."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from wt.models import Item
from wt.text import clean_text, guess_lang, sha256_text, utcnow

_WS = re.compile(r"\s+")
SIMHASH_BITS = 64
INGEST_HAMMING = 3
INGEST_COSINE = 0.95


def canonical_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    parts = urlsplit(raw)
    scheme = (parts.scheme or "https").lower()
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid"}
    ]
    path = parts.path or "/"
    if path != "/":
        path = path.rstrip("/")
    return urlunsplit((scheme, host, path, urlencode(query), ""))


def content_hash(title: str, text: str) -> str:
    norm = _WS.sub(" ", f"{title} {text}".lower()).strip()
    return sha256_text(norm)


def url_hash(url: str) -> str | None:
    canonical = canonical_url(url)
    if not canonical:
        return None
    return sha256_text(canonical)


def simhash(text: str, *, bits: int = SIMHASH_BITS) -> int:
    from wt.text import tokenize

    tokens = tokenize(text) or ["empty"]
    weights = [0] * bits
    for token in tokens:
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        number = int.from_bytes(digest, "little")
        for bit in range(bits):
            weights[bit] += 1 if (number >> bit) & 1 else -1
    out = 0
    for bit, weight in enumerate(weights):
        if weight >= 0:
            out |= 1 << bit
    return out


def make_item(
    *,
    url: str,
    source: str,
    title: str,
    text: str,
    ts: datetime | None = None,
    feed_id: str = "",
    authors: list[str] | None = None,
    lang: str | None = None,
) -> Item | None:
    title_c = clean_text(title)
    text_c = clean_text(text)
    if not title_c and not text_c:
        return None
    canonical = canonical_url(url)
    basis = canonical or content_hash(title_c, text_c)
    item_id = sha256_text(basis)[:16]
    document = f"{title_c}\n{text_c}"
    return Item(
        id=item_id,
        url=canonical,
        source=source,
        feed_id=feed_id,
        ts=ts or utcnow(),
        title=title_c or text_c[:120],
        text=text_c,
        lang=lang or guess_lang(document),
        authors=list(authors or []),
    )
