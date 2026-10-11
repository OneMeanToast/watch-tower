"""Local desk API. The browser talks to this; the pipeline stays in wt."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from wt import __version__
from wt.catalog import build_catalog, parse_bound, probe_sources
from wt.cost import resolve_daily_warn, resolve_monthly, resolve_rate, summary as cost_summary
from wt.desks import get_desk, load_desks, upsert_desk
from wt.exports import filter_rows, to_csv, to_json, to_kev, to_markdown, to_opml, to_tether
from wt.extract_graph.api import router as extract_router
from wt.rip.api import router as rip_router
from wt.rip.pipeline import RipError, commit_rip
from wt.uploads import UploadError, prepare_upload
from wt.feedback import record_feedback
from wt.indicators import IndicatorError, load_indicators, update_indicator
from wt.pipeline import run_demo, run_items, run_live
from wt.settings import Settings, project_root
from wt.store import Store

app = FastAPI(title="Watch Tower desk")
app.include_router(rip_router)
app.include_router(extract_router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:47221",
        "http://localhost:47221",
        "http://127.0.0.1:8641",
        "http://localhost:8641",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)


class WireBody(BaseModel):
    mode: str = "demo"


class LabelBody(BaseModel):
    label: str


class IndicatorPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    keywords_any: list[str] | None = None
    min_cosine: float | None = None
    alert_if: str | None = None
    digest_if: str | None = None
    cooldown: str | None = None
    push_min_confidence: float | None = None
    uncertain_min: float | None = None


class UploadBody(BaseModel):
    title: str = ""
    text: str = ""
    url: str = ""
    published_at: str = ""


class CostBody(BaseModel):
    usd_per_mtok: float | None = None
    monthly_usd: float | None = None
    daily_warn_usd: float | None = None


class DeskBody(BaseModel):
    id: str
    name: str
    brief: str = ""
    indicators: list[str] = []
    exports: list[str] = ["markdown", "csv", "json", "kev"]
    digest_seen_at: str = ""


def desk_db(root: Path | None = None) -> Path:
    root = root or project_root()
    return root / "data" / "desk.db"


def desk_settings(live: bool, root: Path | None = None) -> Settings:
    root = root or project_root()
    if live:
        settings = Settings.from_env(root)
    else:
        settings = Settings.for_demo(root, db_path=desk_db(root))
    settings.db_path = desk_db(root)
    settings.echo = False
    return settings


def _bounds(since: str | None, until: str | None) -> tuple[str | None, str | None]:
    try:
        return parse_bound(since), parse_bound(until)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _rows(root: Path | None = None, *, since: str | None = None, until: str | None = None) -> list[dict]:
    store = Store(desk_db(root))
    try:
        return [_match_dict(row) for row in store.list_matches(since=since, until=until, limit=400)]
    finally:
        store.close()


def _match_dict(row) -> dict:
    keys = set(row.keys())
    snippets = json.loads(row["snippets_json"] or "[]")
    trace = json.loads(row["trace_json"] or "[]") if "trace_json" in keys else []
    return {
        "id": row["id"],
        "item_id": row["item_id"],
        "indicator_id": row["indicator_id"],
        "title": row["item_title"],
        "url": row["item_url"] or "",
        "source": row["item_source"],
        "feed_id": row["item_feed"] or "",
        "text": row["item_text"] or "",
        "cosine": row["cosine"],
        "keyword_hit": bool(row["keyword_hit"]),
        "p_hit": row["p_hit"],
        "severity": row["severity"],
        "category": row["category"],
        "confidence": row["confidence"],
        "action": row["action"],
        "reason": row["reason"],
        "backend": row["backend"],
        "explanation": row["explanation"] or "",
        "snippets": snippets,
        "trace": trace,
        "event_id": row["item_event"] if "item_event" in keys else "",
        "published_at": row["item_ts"] if "item_ts" in keys else "",
        "created_at": row["created_at"],
    }


def _visible(rows: list[dict], desk_id: str | None, root: Path | None = None) -> list[dict]:
    if not desk_id:
        return rows
    desk = get_desk(desk_id, root)
    if desk is None:
        raise HTTPException(status_code=404, detail=f"unknown desk {desk_id}")
    return filter_rows(rows, desk)


@app.get("/status")
def status() -> dict:
    root = project_root()
    settings = desk_settings(False, root)
    store = Store(settings.db_path)
    try:
        counts = store.counts()
        vector = store.vector_backend
        last = store.meta_get("desk_last_report")
    finally:
        store.close()
    return {
        "version": __version__,
        "db": str(settings.db_path),
        "counts": counts,
        "vector_backend": vector,
        "last_report": json.loads(last) if last else None,
        "desks": load_desks(root),
    }


@app.get("/cost")
def cost() -> dict:
    settings = desk_settings(True)
    store = Store(settings.db_path)
    try:
        rate = resolve_rate(store, settings.jev_usd_per_mtok)
        monthly = resolve_monthly(store, settings.jev_monthly_usd)
        daily = resolve_daily_warn(store, settings.jev_daily_warn_usd)
        return cost_summary(store, rate=rate, monthly=monthly, daily_warn=daily)
    finally:
        store.close()


@app.post("/cost")
def save_cost(body: CostBody) -> dict:
    settings = desk_settings(True)
    store = Store(settings.db_path)
    try:
        if body.usd_per_mtok is not None:
            if body.usd_per_mtok < 0:
                raise HTTPException(status_code=400, detail="usd_per_mtok must be >= 0")
            store.meta_set("jev_usd_per_mtok", str(body.usd_per_mtok))
        if body.monthly_usd is not None:
            if body.monthly_usd < 0:
                raise HTTPException(status_code=400, detail="monthly_usd must be >= 0")
            store.meta_set("jev_monthly_usd", str(body.monthly_usd))
        if body.daily_warn_usd is not None:
            if body.daily_warn_usd < 0:
                raise HTTPException(status_code=400, detail="daily_warn_usd must be >= 0")
            store.meta_set("jev_daily_warn_usd", str(body.daily_warn_usd))
        rate = resolve_rate(store, settings.jev_usd_per_mtok)
        monthly = resolve_monthly(store, settings.jev_monthly_usd)
        daily = resolve_daily_warn(store, settings.jev_daily_warn_usd)
        return cost_summary(store, rate=rate, monthly=monthly, daily_warn=daily)
    finally:
        store.close()


@app.post("/wire")
def wire(body: WireBody) -> dict:
    root = project_root()
    mode = body.mode.strip().lower()
    if mode not in {"demo", "live"}:
        raise HTTPException(status_code=400, detail="mode must be demo or live")
    settings = desk_settings(mode == "live", root)
    try:
        report = run_demo(settings) if mode == "demo" else run_live(settings)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    payload = {
        "mode": mode,
        "items_seen": report.items_seen,
        "items_new": report.items_new,
        "duplicates": report.duplicates,
        "spam_dropped": report.spam_dropped,
        "pushes": len(report.pushes),
        "digests": len(report.digests),
        "backend": report.backend,
        "embedder": report.embedder,
        "vector_backend": report.vector_backend,
        "errors": report.errors,
    }
    store = Store(settings.db_path)
    try:
        store.meta_set("desk_last_report", json.dumps(payload))
    finally:
        store.close()
    return payload


@app.get("/matches")
def matches(desk: str | None = None, since: str | None = None, until: str | None = None) -> list[dict]:
    start, end = _bounds(since, until)
    return _visible(_rows(since=start, until=end), desk)


@app.get("/catalog")
def catalog(since: str | None = None, until: str | None = None) -> dict:
    root = project_root()
    start, end = _bounds(since, until)
    store = Store(desk_db(root))
    try:
        payload = build_catalog(store, root=root, since=start, until=end)
        if since or until:
            payload["items"] = store.list_items(since=start, until=end, limit=80)
        else:
            payload["items"] = store.list_items(limit=80)
    finally:
        store.close()
    return payload


@app.get("/catalog/items")
def catalog_items(
    source: str | None = None,
    feed_id: str | None = None,
    since: str | None = None,
    until: str | None = None,
) -> list[dict]:
    start, end = _bounds(since, until)
    store = Store(desk_db())
    try:
        return store.list_items(source=source, feed_id=feed_id, since=start, until=end, limit=30)
    finally:
        store.close()


@app.post("/catalog/probe")
def catalog_probe() -> dict:
    root = project_root()
    store = Store(desk_db(root))
    try:
        found = probe_sources(store, root=root)
    finally:
        store.close()
    return {"probed": len(found), "live": sum(1 for row in found.values() if row.get("ok"))}


@app.post("/catalog/upload")
def catalog_upload(body: UploadBody) -> dict:
    link = (body.url or "").strip()
    pasted = (body.text or "").strip()
    if link and not pasted:
        settings = desk_settings(False)
        store = Store(settings.db_path)
        try:
            payload = commit_rip(
                settings,
                store,
                link,
                note=body.title.strip(),
                title=body.title.strip(),
            )
        except RipError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        finally:
            store.close()
        if not payload.get("stored"):
            raise HTTPException(
                status_code=400,
                detail="the page did not yield enough text; paste it on the rip desk",
            )
        return {
            "id": payload["id"],
            "title": payload["title"],
            "method": payload["method"],
            "published_at": payload["published_at"],
            "items_new": payload["items_new"],
            "duplicates": payload["duplicates"],
            "pushes": payload["pushes"],
            "digests": payload["digests"],
        }
    try:
        item, method, note = prepare_upload(
            title=body.title,
            text=body.text,
            url=body.url,
            published_at=body.published_at,
        )
    except UploadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    settings = desk_settings(False)
    store = Store(settings.db_path)
    try:
        report = run_items(settings, [item], store)
        store.record_intake(item.id, kind="upload", method=method, note=note)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        store.close()
    return {
        "id": item.id,
        "title": item.title,
        "method": method,
        "published_at": item.ts.isoformat(),
        "items_new": report.items_new,
        "duplicates": report.duplicates,
        "pushes": len(report.pushes),
        "digests": len(report.digests),
    }


@app.get("/matches/{match_id}")
def match_one(match_id: str, desk: str | None = None) -> dict:
    found = [row for row in _visible(_rows(), desk) if row["id"] == match_id]
    if not found:
        raise HTTPException(status_code=404, detail="match not on this desk")
    return found[0]


@app.post("/matches/{match_id}/label")
def label_match(match_id: str, body: LabelBody) -> dict:
    settings = desk_settings(False)
    try:
        return record_feedback(
            settings.db_path,
            match_id,
            body.label,
            labels_path=settings.labels_path,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="unknown match") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/indicators")
def indicators() -> list[dict]:
    root = project_root()
    return [_indicator_dict(item) for item in load_indicators(root / "config" / "indicators")]


@app.put("/indicators/{indicator_id}")
def save_indicator(indicator_id: str, patch: IndicatorPatch) -> dict:
    root = project_root()
    data = patch.model_dump(exclude_none=True)
    try:
        indicator = update_indicator(root / "config" / "indicators", indicator_id, data)
    except IndicatorError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _indicator_dict(indicator)


@app.get("/digest")
def digest(desk: str | None = None, since: str | None = None, until: str | None = None) -> list[dict]:
    root = project_root()
    start, end = _bounds(since, until)
    rows = [row for row in _visible(_rows(root, since=start, until=end), desk, root) if row["action"] == "digest"]
    chosen = get_desk(desk, root) if desk else None
    seen = (chosen or {}).get("digest_seen_at") or ""
    if seen:
        rows = [row for row in rows if row["created_at"] > seen]
    return rows


@app.post("/desks/{desk_id}/digest-read")
def mark_digest_read(desk_id: str) -> dict:
    from wt.text import utcnow

    desk = get_desk(desk_id)
    if desk is None:
        raise HTTPException(status_code=404, detail="unknown desk")
    desk["digest_seen_at"] = utcnow().isoformat()
    return upsert_desk(desk)


@app.get("/desks")
def desks() -> list[dict]:
    return load_desks()


@app.put("/desks/{desk_id}")
def save_desk(desk_id: str, body: DeskBody) -> dict:
    if body.id != desk_id:
        raise HTTPException(status_code=400, detail="desk id mismatch")
    return upsert_desk(body.model_dump())


@app.get("/export")
def export(
    desk: str | None = None,
    format: str = "markdown",
    since: str | None = None,
    until: str | None = None,
):
    root = project_root()
    chosen = get_desk(desk, root) if desk else None
    if desk and chosen is None:
        raise HTTPException(status_code=404, detail="unknown desk")
    name = chosen["name"] if chosen else "All lanes"
    kind = format.strip().lower()
    start, end = _bounds(since, until)
    rows = filter_rows(_rows(root, since=start, until=end), chosen)
    settings = desk_settings(False, root)
    store = Store(settings.db_path)
    try:
        catalog = build_catalog(store, root=root, since=start, until=end)
    finally:
        store.close()
    if kind == "markdown":
        body = to_markdown(rows, desk_name=name)
        media = "text/markdown"
    elif kind == "csv":
        body = to_csv(rows)
        media = "text/csv"
    elif kind == "json":
        body = to_json(rows)
        media = "application/json"
    elif kind == "kev":
        body = to_kev(settings.labels_path, settings.indicators_dir, chosen)
        media = "application/x-ndjson"
    elif kind == "opml":
        body = to_opml(catalog["sources"])
        media = "text/x-opml"
    elif kind == "tether":
        body = to_tether(catalog["sources"], rows, desk_name=name, since=start or "", until=end or "")
        media = "application/json"
    else:
        raise HTTPException(status_code=400, detail="format must be markdown, csv, json, kev, opml, or tether")
    filename = f"watch-tower-{desk or 'all'}.{_ext(kind)}"
    return PlainTextResponse(
        body,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _ext(kind: str) -> str:
    return {"markdown": "md", "csv": "csv", "json": "json", "kev": "jsonl", "opml": "opml", "tether": "json"}[kind]


def _indicator_dict(indicator) -> dict:
    return {
        "id": indicator.id,
        "name": indicator.name,
        "description": indicator.description.strip(),
        "sources": indicator.sources,
        "keywords_any": indicator.prefilter.keywords_any,
        "min_cosine": indicator.prefilter.min_cosine,
        "alert_if": indicator.policy.alert_if,
        "digest_if": indicator.policy.digest_if,
        "cooldown": indicator.policy.cooldown,
        "push_min_confidence": indicator.policy.push_min_confidence,
        "uncertain_min": indicator.policy.uncertain_min,
    }
