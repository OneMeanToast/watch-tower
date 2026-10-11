"""Local desk API. The browser talks to this; the pipeline stays in wt."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from wt import __version__
from wt.desks import get_desk, load_desks, upsert_desk
from wt.exports import filter_rows, to_csv, to_json, to_kev, to_markdown
from wt.feedback import record_feedback
from wt.indicators import IndicatorError, load_indicators, update_indicator
from wt.pipeline import run_demo, run_live
from wt.settings import Settings, project_root
from wt.store import Store

app = FastAPI(title="Watch Tower desk")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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


def _rows(root: Path | None = None) -> list[dict]:
    store = Store(desk_db(root))
    try:
        return [_match_dict(row) for row in store.list_matches(limit=400)]
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
def matches(desk: str | None = None) -> list[dict]:
    return _visible(_rows(), desk)


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
def digest(desk: str | None = None) -> list[dict]:
    root = project_root()
    rows = [row for row in _visible(_rows(root), desk, root) if row["action"] == "digest"]
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
def export(desk: str | None = None, format: str = "markdown"):
    root = project_root()
    chosen = get_desk(desk, root) if desk else None
    if desk and chosen is None:
        raise HTTPException(status_code=404, detail="unknown desk")
    name = chosen["name"] if chosen else "All lanes"
    kind = format.strip().lower()
    rows = filter_rows(_rows(root), chosen)
    settings = desk_settings(False, root)
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
    else:
        raise HTTPException(status_code=400, detail="format must be markdown, csv, json, or kev")
    filename = f"watch-tower-{desk or 'all'}.{_ext(kind)}"
    return PlainTextResponse(
        body,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _ext(kind: str) -> str:
    return {"markdown": "md", "csv": "csv", "json": "json", "kev": "jsonl"}[kind]


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
