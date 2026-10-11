"""Run a rip through the same hidden-text, spotlight, and injection gates as a feed."""

from __future__ import annotations

import json
from datetime import datetime

from wt.guard import GuardVerdict, classify_text
from wt.models import Item
from wt.normalize import make_item
from wt.pipeline import run_items
from wt.rip.discover import Discovery, discover
from wt.rip.extract import RipResult, rip_url
from wt.rip.ssrf import SsrfError
from wt.rip.subscribe import save_subscription
from wt.safety import hidden_instruction
from wt.settings import Settings
from wt.store import Store
from wt.text import parse_time


class RipError(ValueError):
    def __init__(self, message: str, *, status: int = 400):
        super().__init__(message)
        self.status = status


def commit_rip(
    settings: Settings,
    store: Store,
    url: str,
    *,
    note: str = "",
    selection: str = "",
    title: str = "",
    allow_render: bool = False,
    renderer=None,
    client=None,
    resolver=None,
    indicators=None,
) -> dict:
    """Extract one URL, store it, and score it. A second rip of the same URL updates the row."""
    try:
        extracted = rip_url(
            url,
            client=client,
            resolver=resolver,
            renderer=renderer,
            allow_render=allow_render,
            selection=selection,
        )
    except SsrfError as exc:
        raise RipError(str(exc), status=400) from exc
    except Exception as exc:
        raise RipError(str(exc), status=502) from exc
    if title.strip():
        extracted.title = title.strip()
    words = len((extracted.text or "").split())
    if extracted.method == "stub" and words < 12:
        return _unstored(extracted, note)
    item = _item(extracted, note)
    if item is None:
        return _unstored(extracted, note)
    verdict = _verdict(extracted.raw_html, item.document(), settings.injection_guard)
    report = run_items(
        settings,
        [item],
        store,
        indicators=indicators,
        force_guard=verdict,
        replace_existing=True,
    )
    store.set_rip_meta(
        item.id,
        method=extracted.method,
        warnings=extracted.warnings,
        note=note,
        source_type="manual_rip",
    )
    store.record_intake(item.id, kind="upload", method=extracted.method, note=note or extracted.canonical)
    pushes = [row for row in report.results if row.action == "push"]
    return {
        "stored": True,
        "id": item.id,
        "title": item.title,
        "url": item.url,
        "method": extracted.method,
        "kind": extracted.kind,
        "warnings": extracted.warnings,
        "words": len(item.text.split()),
        "published_at": item.ts.isoformat(),
        "author": extracted.author,
        "items_new": report.items_new,
        "duplicates": report.duplicates,
        "pushes": len(pushes),
        "digests": len(report.digests),
        "hits": [
            {"indicator_id": row.indicator_id, "action": row.action, "reason": row.reason}
            for row in report.results
        ],
        "guard": {"flagged": verdict.flagged, "reason": verdict.reason, "backend": verdict.backend},
        "paste_required": False,
    }


def preview_rip(url: str, *, client=None, resolver=None, selection: str = "") -> dict:
    """Classify and check robots. Does not fetch the article and does not store it."""
    from wt.normalize import canonical_url
    from wt.rip.classify import classify
    from wt.rip.robots import robots_status
    from wt.rip.ssrf import guard_url

    try:
        guard_url(url, resolver=resolver)
    except SsrfError as exc:
        raise RipError(str(exc), status=400) from exc
    canonical = canonical_url(url) or url.strip()
    kind = classify(canonical)
    allowed, warning = True, ""
    if kind not in {"bsky_post", "x_post"}:
        try:
            allowed, warning = robots_status(canonical, client=client, resolver=resolver)
        except SsrfError as exc:
            raise RipError(str(exc), status=400) from exc
    return {
        "url": url,
        "canonical": canonical,
        "kind": kind,
        "robots": {"allowed": allowed, "warning": warning},
        "selection": (selection or "")[:4000],
        "warnings": [warning] if warning else [],
    }


def commit_subscribe(
    settings: Settings,
    url: str,
    *,
    feed_url: str = "",
    selector: str = "",
    link_selector: str = "a",
    title_selector: str = "",
    interval_min: int = 60,
    via: str = "web",
    client=None,
    resolver=None,
    pick: int | None = None,
) -> dict:
    """Discover feeds, or save the one the person picked. robots.txt is enforced."""
    if feed_url or selector:
        return _save(
            settings,
            page_url=url,
            feed_url=feed_url,
            selector=selector,
            link_selector=link_selector,
            title_selector=title_selector,
            interval_min=interval_min,
            via=via,
            client=client,
            resolver=resolver,
        )
    try:
        found = discover(url, client=client, resolver=resolver)
    except SsrfError as exc:
        raise RipError(str(exc), status=400) from exc
    if pick is not None:
        if pick < 0 or pick >= len(found.candidates):
            raise RipError("that feed is not in the list", status=400)
        chosen = found.candidates[pick]
        return _save(
            settings,
            page_url=url,
            feed_url=chosen.url,
            interval_min=interval_min,
            via=via,
            client=client,
            resolver=resolver,
        )
    if len(found.candidates) == 1:
        chosen = found.candidates[0]
        saved = _save(
            settings,
            page_url=url,
            feed_url=chosen.url,
            interval_min=interval_min,
            via=via,
            client=client,
            resolver=resolver,
        )
        saved["discovery"] = _discovery_payload(found)
        return saved
    return {
        "stored": False,
        "saved": False,
        "discovery": _discovery_payload(found),
    }


def _save(
    settings: Settings,
    *,
    page_url: str,
    feed_url: str = "",
    selector: str = "",
    link_selector: str = "a",
    title_selector: str = "",
    interval_min: int = 60,
    via: str = "web",
    client=None,
    resolver=None,
) -> dict:
    from wt.rip.robots import subscription_allowed

    target = feed_url or page_url
    try:
        allowed, reason = subscription_allowed(target, client=client, resolver=resolver)
    except SsrfError as exc:
        raise RipError(str(exc), status=400) from exc
    if not allowed:
        raise RipError(reason or "robots.txt disallows a subscription", status=403)
    try:
        row = save_subscription(
            settings.sources_path,
            page_url=page_url,
            feed_url=feed_url,
            selector=selector,
            link_selector=link_selector,
            title_selector=title_selector,
            interval_min=interval_min,
            via=via,
        )
    except ValueError as exc:
        raise RipError(str(exc), status=400) from exc
    return {"stored": False, "saved": True, "source": row}


def _discovery_payload(found: Discovery) -> dict:
    selector = None
    if found.selector is not None:
        selector = {
            "selector": found.selector.selector,
            "link_selector": found.selector.link_selector,
            "title_selector": found.selector.title_selector,
            "items": found.selector.items,
        }
    return {
        "kind": found.kind,
        "warnings": found.warnings,
        "candidates": [
            {
                "url": row.url,
                "title": row.title,
                "n_items": row.n_items,
                "latest": row.latest,
                "kind": row.kind,
            }
            for row in found.candidates
        ],
        "selector": selector,
    }


def _verdict(raw_html: str, visible: str, mode: str) -> GuardVerdict:
    if hidden_instruction(raw_html):
        return GuardVerdict(True, "instruction-shaped text was hidden in the page", "heuristic")
    return classify_text(visible, mode=mode)


def _item(extracted: RipResult, note: str) -> Item | None:
    authors = [extracted.author] if extracted.author else []
    when = _published(extracted.published)
    pasted = extracted.method == "paste"
    return make_item(
        url=extracted.canonical or extracted.url,
        source="upload",
        feed_id="manual",
        title=extracted.title,
        text=extracted.text,
        ts=when,
        authors=authors,
        groups=["manual"],
        local_only=pasted,
    )


def _published(value: str) -> datetime | None:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        return parse_time(raw)
    except Exception:
        return None


def _unstored(extracted: RipResult, note: str) -> dict:
    return {
        "stored": False,
        "id": "",
        "title": extracted.title,
        "url": extracted.canonical or extracted.url,
        "method": extracted.method,
        "kind": extracted.kind,
        "warnings": extracted.warnings,
        "words": len((extracted.text or "").split()),
        "published_at": "",
        "author": extracted.author,
        "items_new": 0,
        "duplicates": 0,
        "pushes": 0,
        "digests": 0,
        "hits": [],
        "guard": {"flagged": False, "reason": "", "backend": ""},
        "paste_required": True,
        "note": note,
    }


def job_result_json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)
