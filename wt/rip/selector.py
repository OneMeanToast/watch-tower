"""Suggest a repeating CSS selector when a page has no feed."""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urljoin

_CANDIDATES = (
    "article",
    "div.post",
    ".post",
    "li",
    "div.entry",
    ".entry",
    "main h2",
)


@dataclass
class SelectorSuggestion:
    selector: str
    link_selector: str = "a"
    title_selector: str = ""
    items: list[dict] = field(default_factory=list)


def suggest_selector(html: str, *, base_url: str) -> SelectorSuggestion | None:
    if not (html or "").strip():
        return None
    from lxml import html as lhtml

    try:
        document = lhtml.fromstring(html)
    except Exception:
        return None
    best: SelectorSuggestion | None = None
    for selector in _CANDIDATES:
        try:
            nodes = document.cssselect(selector)
        except Exception:
            continue
        items = _items_from(nodes, base_url)
        if best is None or len(items) > len(best.items):
            best = SelectorSuggestion(selector=selector, items=items)
    if best is None or len(best.items) < 2:
        return best if best and best.items else None
    return best


def apply_selector(
    html: str,
    *,
    base_url: str,
    selector: str,
    link_selector: str = "a",
    title_selector: str = "",
) -> list[dict]:
    from lxml import html as lhtml

    document = lhtml.fromstring(html)
    nodes = document.cssselect(selector) if selector else []
    return _items_from(nodes, base_url, link_selector=link_selector or "a", title_selector=title_selector)


def _items_from(nodes, base_url: str, *, link_selector: str = "a", title_selector: str = "") -> list[dict]:
    items: list[dict] = []
    seen: set[str] = set()
    for node in nodes:
        link_nodes = node.cssselect(link_selector) if link_selector else []
        href = ""
        link_text = ""
        if link_nodes:
            href = link_nodes[0].get("href") or ""
            link_text = " ".join(link_nodes[0].text_content().split())
        title = ""
        if title_selector:
            titled = node.cssselect(title_selector)
            if titled:
                title = " ".join(titled[0].text_content().split())
        title = title or link_text or " ".join(node.text_content().split())[:140]
        if not title:
            continue
        absolute = urljoin(base_url, href) if href else ""
        key = absolute or title
        if key in seen:
            continue
        seen.add(key)
        items.append({"title": title, "url": absolute})
    return items
