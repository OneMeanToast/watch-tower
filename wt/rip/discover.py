"""Find a real feed for a page. Rewrites first, then links, then common paths."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

import feedparser

from wt.rip.classify import classify
from wt.rip.fetch import guarded_get
from wt.rip.robots import subscription_allowed
from wt.rip.selector import SelectorSuggestion, suggest_selector
from wt.rip.ssrf import SsrfError, guard_url

_COMMON = ("/feed", "/rss", "/feed.xml", "/rss.xml", "/atom.xml", "/index.xml")
_ALT = re.compile(r"(?i)application/(rss|atom)\+xml|application/xml|text/xml")


@dataclass
class FeedCandidate:
    url: str
    title: str
    n_items: int
    latest: str
    kind: str = "rss"


@dataclass
class Discovery:
    candidates: list[FeedCandidate] = field(default_factory=list)
    selector: SelectorSuggestion | None = None
    warnings: list[str] = field(default_factory=list)
    kind: str = "homepage"


def discover(url: str, *, client=None, resolver=None) -> Discovery:
    guard_url(url, resolver=resolver)
    kind = classify(url)
    found = Discovery(kind=kind)
    page_html = ""
    page_url = url
    if kind not in {"bsky_post", "x_post", "pdf"}:
        allowed, reason = subscription_allowed(url, client=client, resolver=resolver)
        if not allowed:
            found.warnings.append(reason)
        else:
            try:
                page = guarded_get(url, client=client, resolver=resolver)
                page_html = page.text
                page_url = page.url or url
            except SsrfError:
                raise
            except Exception as exc:
                found.warnings.append(f"page: {exc}")
    seeds = _rewrites(url, kind)
    seeds.extend(_alternate_links(page_html, page_url))
    if not seeds:
        seeds.extend(_common_paths(page_url))
        seeds.extend(_anchor_feeds(page_html, page_url))
    seen: set[str] = set()
    for seed in seeds:
        if seed in seen:
            continue
        seen.add(seed)
        if len(found.candidates) >= 5:
            break
        try:
            guard_url(seed, resolver=resolver)
        except SsrfError as exc:
            found.warnings.append(str(exc))
            continue
        allowed, reason = subscription_allowed(seed, client=client, resolver=resolver)
        if not allowed:
            found.warnings.append(reason)
            continue
        try:
            response = guarded_get(seed, client=client, resolver=resolver)
        except SsrfError as exc:
            found.warnings.append(str(exc))
            continue
        except Exception as exc:
            found.warnings.append(str(exc))
            continue
        candidate = validate_feed(response.body, seed)
        if candidate is not None:
            found.candidates.append(candidate)
    if not found.candidates and page_html:
        found.selector = suggest_selector(page_html, base_url=page_url)
    return found


def validate_feed(body: bytes | str, url: str) -> FeedCandidate | None:
    parsed = feedparser.parse(body)
    entries = list(parsed.entries or [])
    if not entries:
        return None
    title = str((parsed.feed or {}).get("title") or url)
    latest = ""
    first = entries[0]
    for key in ("published", "updated"):
        if first.get(key):
            latest = str(first.get(key))
            break
    return FeedCandidate(url=url, title=title, n_items=len(entries), latest=latest)


def _rewrites(url: str, kind: str) -> list[str]:
    parts = urlsplit(url)
    path = parts.path or ""
    if kind == "bsky_profile":
        handle = _segment(path, "profile")
        if handle:
            return [f"https://bsky.app/profile/{handle}/rss"]
    if kind == "youtube":
        channel = _youtube_channel(path)
        if channel:
            return [f"https://www.youtube.com/feeds/videos.xml?channel_id={channel}"]
    if kind == "reddit":
        bare = url.split("?", 1)[0].rstrip("/")
        if not bare.endswith(".rss"):
            return [bare + "/.rss"]
    return []


def _youtube_channel(path: str) -> str:
    parts = [part for part in path.split("/") if part]
    if len(parts) >= 2 and parts[0] == "channel":
        return parts[1]
    return ""


def _segment(path: str, marker: str) -> str:
    parts = [part for part in path.split("/") if part]
    if marker in parts:
        index = parts.index(marker)
        if index + 1 < len(parts):
            return parts[index + 1]
    return ""


def _alternate_links(html: str, base: str) -> list[str]:
    if not html:
        return []
    from lxml import html as lhtml

    try:
        document = lhtml.fromstring(html)
    except Exception:
        return []
    found: list[str] = []
    for link in document.xpath("//link[@href]"):
        rel = " ".join(str(link.get("rel") or "").lower().split())
        kind = str(link.get("type") or "").lower()
        href = str(link.get("href") or "")
        if "alternate" in rel and (_ALT.search(kind) or href):
            if "alternate" in rel and ("rss" in kind or "atom" in kind or href.lower().endswith((".rss", ".xml")) or "feed" in href.lower()):
                found.append(urljoin(base, href))
    return found


def _anchor_feeds(html: str, base: str) -> list[str]:
    if not html:
        return []
    try:
        from trafilatura.feeds import is_potential_feed
    except ImportError:
        return []
    from lxml import html as lhtml

    try:
        document = lhtml.fromstring(html)
    except Exception:
        return []
    found: list[str] = []
    for href in document.xpath("//a/@href"):
        absolute = urljoin(base, str(href))
        try:
            if is_potential_feed(absolute):
                found.append(absolute)
        except Exception:
            continue
    return found[:8]


def _common_paths(url: str) -> list[str]:
    parts = urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}"
    return [origin + path for path in _COMMON]
