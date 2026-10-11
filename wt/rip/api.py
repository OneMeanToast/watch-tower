"""Desk routes for a rip. GET never stores an item or creates a job."""

from __future__ import annotations

import hmac
import os
import secrets
import time
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from wt.rip.pipeline import RipError, commit_rip, commit_subscribe, preview_rip
from wt.settings import project_root
from wt.store import Store

router = APIRouter()

_DESK_PORTS = {47221, 8641}
_LOCAL_CLIENTS = {"127.0.0.1", "::1", "localhost", "testclient"}
_RATE_LIMIT = 60
_RATE_WINDOW = 3600
_BODY_CAP = 1_000_000


class RipBody(BaseModel):
    url: str
    mode: str = "rip"
    sel: str = ""
    note: str = ""
    csrf: str = ""
    title: str = ""
    pick: int | None = None
    render: bool = False


class SubscribeBody(BaseModel):
    url: str
    csrf: str = ""
    feed_url: str = ""
    selector: str = ""
    link_selector: str = "a"
    title_selector: str = ""
    interval_min: int = 60
    note: str = ""


def _settings_and_db():
    from wt.deskapp import desk_db, desk_settings

    return desk_settings(False), desk_db()


def _token() -> str:
    env = os.environ.get("WT_RIP_TOKEN", "").strip()
    if env:
        return env
    path = project_root() / "data" / "rip.token"
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    return ""


def _bearer(request: Request) -> bool:
    header = request.headers.get("authorization") or ""
    if not header.lower().startswith("bearer "):
        return False
    presented = header.split(" ", 1)[1].strip()
    expected = _token()
    if not presented or not expected:
        return False
    return hmac.compare_digest(presented, expected)


def _origin_ok(request: Request) -> bool:
    origin = request.headers.get("origin")
    if not origin:
        return True
    parts = urlsplit(origin)
    host = (parts.hostname or "").lower()
    if parts.scheme not in {"http", "https"}:
        return False
    if host not in {"127.0.0.1", "localhost"}:
        return False
    return parts.port in _DESK_PORTS


def _client_ok(request: Request, bearer: bool) -> bool:
    host = ""
    if request.client is not None:
        host = request.client.host or ""
    if host in _LOCAL_CLIENTS:
        return True
    return bearer


def _authorize(request: Request, csrf: str, store: Store) -> None:
    bearer = _bearer(request)
    if not _origin_ok(request):
        raise HTTPException(status_code=403, detail="origin is not the local desk")
    if not _client_ok(request, bearer):
        raise HTTPException(status_code=403, detail="a LAN client needs the rip token")
    if bearer:
        return
    if not csrf or not _csrf_ok(store, csrf):
        raise HTTPException(status_code=403, detail="missing CSRF token")


def _issue_csrf(store: Store) -> str:
    token = secrets.token_urlsafe(24)
    store.meta_set(f"csrf:{token}", str(time.time()))
    return token


def _csrf_ok(store: Store, token: str) -> bool:
    raw = store.meta_get(f"csrf:{token}")
    if not raw:
        return False
    try:
        issued = float(raw)
    except ValueError:
        return False
    return time.time() - issued < 2 * 3600


def _rate_ok(store: Store) -> bool:
    raw = store.meta_get("rip-rate") or ""
    now = time.time()
    stamps = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            stamp = float(part)
        except ValueError:
            continue
        if now - stamp < _RATE_WINDOW:
            stamps.append(stamp)
    if len(stamps) >= _RATE_LIMIT:
        return False
    stamps.append(now)
    store.meta_set("rip-rate", ",".join(str(stamp) for stamp in stamps))
    return True


def _check_size(*parts: str) -> None:
    if sum(len(part or "") for part in parts) > _BODY_CAP:
        raise HTTPException(status_code=413, detail="body is over 1 MB")


@router.get("/rip/preview")
def rip_preview(url: str, request: Request, title: str = "", sel: str = "") -> dict:
    """No job, no item. The bookmarklet lands here and waits for a button."""
    _check_size(url, title, sel)
    if not _origin_ok(request):
        raise HTTPException(status_code=403, detail="origin is not the local desk")
    settings, db = _settings_and_db()
    store = Store(db)
    try:
        before = store.count_rip_jobs()
        try:
            payload = preview_rip(url, selection=sel)
        except RipError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
        if store.count_rip_jobs() != before:
            raise HTTPException(status_code=500, detail="preview created a job")
        payload["title"] = title
        payload["csrf"] = _issue_csrf(store)
        payload["items"] = store.counts()["items"]
    finally:
        store.close()
    return payload


@router.get("/rip/jobs")
def rip_jobs() -> list[dict]:
    _settings, db = _settings_and_db()
    store = Store(db)
    try:
        return store.list_rip_jobs(limit=20)
    finally:
        store.close()


@router.get("/rip/job/{job_id}")
def rip_job(job_id: str) -> dict:
    _settings, db = _settings_and_db()
    store = Store(db)
    try:
        found = store.get_rip_job(job_id)
    finally:
        store.close()
    if found is None:
        raise HTTPException(status_code=404, detail="unknown rip")
    return found


@router.post("/rip")
def rip_post(body: RipBody, request: Request) -> dict:
    _check_size(body.url, body.sel, body.note, body.title, body.csrf)
    settings, db = _settings_and_db()
    store = Store(db)
    try:
        _authorize(request, body.csrf, store)
        if not _rate_ok(store):
            raise HTTPException(status_code=429, detail="rip rate limit")
        mode = (body.mode or "rip").strip().lower()
        if mode not in {"rip", "subscribe"}:
            raise HTTPException(status_code=400, detail="mode must be rip or subscribe")
        job_id = uuid4().hex[:12]
        store.create_rip_job(job_id, url=body.url, mode=mode)
        try:
            if mode == "subscribe":
                payload = commit_subscribe(
                    settings,
                    body.url,
                    via="web",
                    pick=body.pick,
                    interval_min=60,
                )
            else:
                payload = commit_rip(
                    settings,
                    store,
                    body.url,
                    note=body.note,
                    selection=body.sel,
                    title=body.title,
                    allow_render=body.render,
                )
            store.finish_rip_job(
                job_id,
                status="done",
                item_id=str(payload.get("id") or ""),
                result=payload,
            )
        except RipError as exc:
            store.finish_rip_job(job_id, status="failed", error=str(exc))
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
        except Exception as exc:
            store.finish_rip_job(job_id, status="failed", error=str(exc))
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        job = store.get_rip_job(job_id) or {"id": job_id, "status": "done"}
    finally:
        store.close()
    return job


@router.post("/rip/subscribe")
def rip_subscribe(body: SubscribeBody, request: Request) -> dict:
    _check_size(body.url, body.feed_url, body.selector, body.note, body.csrf)
    settings, db = _settings_and_db()
    store = Store(db)
    try:
        _authorize(request, body.csrf, store)
        if not _rate_ok(store):
            raise HTTPException(status_code=429, detail="rip rate limit")
        job_id = uuid4().hex[:12]
        store.create_rip_job(job_id, url=body.url, mode="subscribe")
        try:
            payload = commit_subscribe(
                settings,
                body.url,
                feed_url=body.feed_url,
                selector=body.selector,
                link_selector=body.link_selector,
                title_selector=body.title_selector,
                interval_min=body.interval_min,
                via="web",
            )
            store.finish_rip_job(job_id, status="done", result=payload)
        except RipError as exc:
            store.finish_rip_job(job_id, status="failed", error=str(exc))
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
        job = store.get_rip_job(job_id) or {"id": job_id}
    finally:
        store.close()
    return job
