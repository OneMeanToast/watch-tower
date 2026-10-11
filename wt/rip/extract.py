"""Fallback chain for one URL. No paywall bypass and no archive mirrors."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from io import BytesIO
from urllib.parse import quote, urlsplit

from wt.rip.classify import classify
from wt.rip.fetch import HTML_MAX_BYTES, PDF_MAX_BYTES, guarded_get, render_page
from wt.rip.robots import robots_status
from wt.rip.ssrf import SsrfError, guard_url
from wt.safety import strip_hidden
from wt.text import clean_text

THIN_WORDS = 40
TEXT_CAP = 200_000
_TAG = re.compile(r"(?is)<(script|style|noscript).*?>.*?</\1>")
_MARKUP = re.compile(r"(?s)<[^>]+>")
_TITLE = re.compile(r"(?is)<title[^>]*>(.*?)</title>")


@dataclass
class RipResult:
    title: str
    author: str
    published: str
    text: str
    url: str
    canonical: str
    method: str
    warnings: list[str] = field(default_factory=list)
    kind: str = "html"
    raw_html: str = ""


def rip_url(
    url: str,
    *,
    client=None,
    resolver=None,
    renderer=None,
    allow_render: bool = False,
    selection: str = "",
) -> RipResult:
    """Fetch and extract. `renderer` replaces Playwright in tests."""
    from wt.normalize import canonical_url

    guard_url(url, resolver=resolver)
    canonical = canonical_url(url) or url.strip()
    kind = classify(canonical)
    own = client is None
    http = client
    if own:
        from wt.rip.fetch import HttpxClient

        http = HttpxClient()
    try:
        if kind == "bsky_post":
            return _bsky(canonical, client=http, resolver=resolver)
        if kind == "x_post":
            return _x_post(canonical, client=http, resolver=resolver, selection=selection)
        allowed, robots_warning = robots_status(canonical, client=http, resolver=resolver)
        warnings = [robots_warning] if (robots_warning and not allowed) else []
        max_bytes = PDF_MAX_BYTES if kind == "pdf" else HTML_MAX_BYTES
        result = guarded_get(canonical, client=http, resolver=resolver, max_bytes=max_bytes)
        if kind == "pdf" or result.content_type == "application/pdf" or result.body.startswith(b"%PDF"):
            extracted = _pdf(canonical, result.body, warnings)
            extracted.kind = "pdf"
            return extracted
        return _html_chain(
            canonical,
            result.body.decode("utf-8", errors="replace"),
            warnings=warnings,
            client=http,
            resolver=resolver,
            renderer=renderer,
            allow_render=allow_render,
            selection=selection,
        )
    finally:
        if own and http is not None:
            http.close()


def _html_chain(
    url: str,
    raw: str,
    *,
    warnings: list[str],
    client,
    resolver,
    renderer,
    allow_render: bool,
    selection: str,
) -> RipResult:
    kind = classify(url)
    cleaned = strip_hidden(raw)
    title, author, published, text, method = _html_extract(cleaned)
    if _words(text) < THIN_WORDS:
        rendered = _try_render(url, renderer=renderer, allow_render=allow_render)
        if rendered:
            title, author, published, text, _method = _html_extract(strip_hidden(rendered))
            method = "render"
            raw = rendered
        elif renderer is None and allow_render:
            warnings.append("render-unavailable")
    if _words(text) < THIN_WORDS and (selection or "").strip():
        text = clean_text(selection)[:TEXT_CAP]
        method = "paste"
        warnings.append("paywalled")
        if not title:
            title = text[:140]
    elif _words(text) < THIN_WORDS:
        method = "stub"
        warnings.append("paywalled" if _words(text) else "thin")
        if not text:
            text = title
    from wt.normalize import canonical_url

    return RipResult(
        title=title or _title_tag(cleaned) or url,
        author=author,
        published=published,
        text=(text or "")[:TEXT_CAP],
        url=url,
        canonical=canonical_url(url) or url,
        method=method,
        warnings=_uniq(warnings),
        kind=kind,
        raw_html=raw,
    )


def _html_extract(html: str) -> tuple[str, str, str, str, str]:
    meta = _trafilatura_meta(html)
    strict = _trafilatura(html, recall=False)
    if _words(strict) >= THIN_WORDS:
        return _title_from(meta, html, strict), meta.get("author") or "", meta.get("date") or "", strict, "trafilatura"
    recall = _trafilatura(html, recall=True)
    if _words(recall) >= THIN_WORDS:
        return _title_from(meta, html, recall), meta.get("author") or "", meta.get("date") or "", recall, "recall"
    title, readable = _readability(html)
    if _words(readable) >= THIN_WORDS:
        return title or _title_from(meta, html, readable), meta.get("author") or "", meta.get("date") or "", readable, "readability"
    best = strict if _words(strict) >= _words(recall) else recall
    if _words(readable) > _words(best):
        best = readable
    return _title_from(meta, html, best), meta.get("author") or "", meta.get("date") or "", best, "stub"


def _trafilatura(html: str, *, recall: bool) -> str:
    try:
        import trafilatura
    except ImportError:
        return ""
    kwargs = {"include_comments": False, "include_tables": False}
    if recall:
        kwargs["favor_recall"] = True
    else:
        kwargs["favor_precision"] = True
    return clean_text(trafilatura.extract(html, **kwargs) or "")


def _trafilatura_meta(html: str) -> dict:
    try:
        import trafilatura
    except ImportError:
        return {}
    raw = trafilatura.extract(
        html,
        output_format="json",
        with_metadata=True,
        favor_precision=True,
        include_comments=False,
    )
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _readability(html: str) -> tuple[str, str]:
    try:
        from readability import Document
    except ImportError:
        return "", ""
    document = Document(html)
    summary = document.summary() or ""
    text = clean_text(_MARKUP.sub(" ", _TAG.sub(" ", summary)))
    return clean_text(document.title() or ""), text


def _try_render(url: str, *, renderer, allow_render: bool) -> str:
    if renderer is not None:
        return renderer(url) or ""
    if not allow_render:
        return ""
    try:
        return render_page(url)
    except Exception:
        return ""


def _pdf(url: str, body: bytes, warnings: list[str]) -> RipResult:
    from pypdf import PdfReader

    from wt.normalize import canonical_url

    reader = PdfReader(BytesIO(body))
    pages = list(reader.pages)
    if len(pages) > 200:
        pages = pages[:200]
        warnings.append("truncated-pages")
    text = clean_text("\n".join((page.extract_text() or "") for page in pages))[:TEXT_CAP]
    title = ""
    if reader.metadata and reader.metadata.title:
        title = clean_text(str(reader.metadata.title))
    if not title:
        title = text[:140] or url
    return RipResult(
        title=title,
        author="",
        published="",
        text=text,
        url=url,
        canonical=canonical_url(url) or url,
        method="pdf",
        warnings=_uniq(warnings),
        kind="pdf",
    )


def _bsky(url: str, *, client, resolver) -> RipResult:
    from wt.normalize import canonical_url

    parts = urlsplit(url)
    chunks = [part for part in parts.path.split("/") if part]
    # /profile/<handle>/post/<rkey>
    if len(chunks) < 4 or chunks[0] != "profile" or chunks[2] != "post":
        raise SsrfError("not a Bluesky post URL")
    handle, rkey = chunks[1], chunks[3]
    ident = guarded_get(
        "https://public.api.bsky.app/xrpc/com.atproto.identity.resolveHandle?handle=" + quote(handle),
        client=client,
        resolver=resolver,
    )
    did = json.loads(ident.body.decode("utf-8") or "{}").get("did") or ""
    if not did:
        return _stub(url, "Bluesky post", "bsky", ["bsky-empty"])
    uri = f"at://{did}/app.bsky.feed.post/{rkey}"
    thread = guarded_get(
        "https://public.api.bsky.app/xrpc/app.bsky.feed.getPostThread?uri=" + quote(uri, safe=""),
        client=client,
        resolver=resolver,
    )
    payload = json.loads(thread.body.decode("utf-8") or "{}")
    post = ((payload.get("thread") or {}).get("post") or {})
    record = post.get("record") or {}
    author = ((post.get("author") or {}).get("handle")) or handle
    text = clean_text(record.get("text") or "")
    published = str(record.get("createdAt") or "")
    if not text:
        return _stub(url, author, "bsky", ["bsky-empty"])
    return RipResult(
        title=text[:140],
        author=author,
        published=published,
        text=text[:TEXT_CAP],
        url=url,
        canonical=canonical_url(url) or url,
        method="bsky",
        kind="bsky_post",
    )


def _x_post(url: str, *, client, resolver, selection: str) -> RipResult:
    from wt.normalize import canonical_url

    endpoint = "https://publish.twitter.com/oembed?url=" + quote(url, safe="")
    try:
        result = guarded_get(endpoint, client=client, resolver=resolver)
    except SsrfError:
        raise
    except Exception:
        result = None
    text = ""
    author = ""
    if result is not None and result.status < 400 and result.body:
        try:
            payload = json.loads(result.body.decode("utf-8"))
        except json.JSONDecodeError:
            payload = {}
        author = str(payload.get("author_name") or "")
        html = str(payload.get("html") or "")
        text = clean_text(_MARKUP.sub(" ", html))
    warnings: list[str] = []
    method = "x"
    if _words(text) < 5:
        if (selection or "").strip():
            text = clean_text(selection)
            method = "paste"
            warnings.append("paywalled")
        else:
            method = "stub"
            warnings.append("x-oembed-empty")
    return RipResult(
        title=text[:140] or url,
        author=author,
        published="",
        text=(text or "")[:TEXT_CAP],
        url=url,
        canonical=canonical_url(url) or url,
        method=method,
        warnings=warnings,
        kind="x_post",
    )


def _stub(url: str, title: str, method: str, warnings: list[str]) -> RipResult:
    from wt.normalize import canonical_url

    return RipResult(
        title=title or url,
        author="",
        published="",
        text="",
        url=url,
        canonical=canonical_url(url) or url,
        method=method,
        warnings=warnings,
        kind=classify(url),
    )


def _title_from(meta: dict, html: str, text: str) -> str:
    title = clean_text(str(meta.get("title") or ""))
    if title:
        return title
    tagged = _title_tag(html)
    if tagged:
        return tagged
    return (text or "").split("\n", 1)[0][:140]


def _title_tag(html: str) -> str:
    match = _TITLE.search(html or "")
    if not match:
        return ""
    return clean_text(_MARKUP.sub(" ", match.group(1)))


def _words(text: str) -> int:
    return len((text or "").split())


def _uniq(warnings: list[str]) -> list[str]:
    found: list[str] = []
    for warning in warnings:
        if warning and warning not in found:
            found.append(warning)
    return found
