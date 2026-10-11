"""Source catalog: what is configured, what is live, and what already landed."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import yaml

from wt.ingest.common import USER_AGENT
from wt.ingest.worldmonitor import catalog_path
from wt.settings import project_root
from wt.store import Store
from wt.text import utcnow


def parse_bound(value: str | None) -> str | None:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"bad timestamp {value}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def build_catalog(
    store: Store,
    *,
    root: Path | None = None,
    since: str | None = None,
    until: str | None = None,
) -> dict:
    root = root or project_root()
    configured = _configured(root)
    stats = {(row["source"], row["feed_id"]): row for row in store.item_stats(since=since, until=until)}
    probes = _load_probes(store)
    errors = _last_errors(store)
    seen: set[tuple[str, str]] = set()
    sources: list[dict] = []
    for row in configured:
        key = (row["source"], row["feed_id"])
        seen.add(key)
        stat = stats.get(key) or {}
        probe = probes.get(row["id"]) or {}
        sources.append(_with_runtime(row, stat, probe, errors.get(row["id"], "")))
    for (source, feed_id), stat in stats.items():
        if (source, feed_id) in seen:
            continue
        sources.append(
            _with_runtime(
                {
                    "id": f"{source}:{feed_id}" if feed_id else source,
                    "kind": source,
                    "name": _wire_name(source, feed_id),
                    "group": "",
                    "url": "",
                    "source": source,
                    "feed_id": feed_id,
                    "configured": False,
                    "enabled": True,
                    "dated": True,
                    "detail": "On the wire from a fixture or an earlier pull. Not in config/sources.yaml.",
                },
                stat,
                {},
                "",
            )
        )
    sources.sort(key=lambda row: (row["status"] == "off", -row["items"], row["name"].lower()))
    return {
        "generated_at": utcnow().isoformat(),
        "since": since or "",
        "until": until or "",
        "sources": sources,
        "intake": store.list_intake(),
    }


def probe_sources(store: Store, *, root: Path | None = None, timeout: float = 2.5) -> dict:
    """Check configured URLs. The result is cached on the desk database."""
    root = root or project_root()
    targets = [row for row in _configured(root) if row.get("url") and row.get("enabled")]
    found: dict[str, dict] = {}
    if targets:
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(_ping, row["url"], timeout): row["id"] for row in targets}
            for future in as_completed(futures):
                source_id = futures[future]
                found[source_id] = future.result()
    store.meta_set("catalog_probe", json.dumps({"checked_at": utcnow().isoformat(), "sources": found}))
    return found


def _ping(url: str, timeout: float) -> dict:
    import httpx

    checked = utcnow().isoformat()
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as http:
            response = http.get(url)
        ok = response.status_code < 400
        return {"ok": ok, "status": response.status_code, "checked_at": checked, "error": ""}
    except Exception as exc:
        return {"ok": False, "status": 0, "checked_at": checked, "error": str(exc)}


def _configured(root: Path) -> list[dict]:
    path = root / "config" / "sources.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        return []
    rows: list[dict] = []
    rss = data.get("rss") or {}
    for feed in rss.get("feeds") or []:
        if not isinstance(feed, dict):
            continue
        feed_id = str(feed.get("id") or "rss")
        rows.append(
            _row(
                id=f"rss:{feed_id}",
                kind="rss",
                name=_title(feed_id),
                group=str(feed.get("group") or ""),
                url=str(feed.get("url") or ""),
                source="rss",
                feed_id=feed_id,
                enabled=bool(feed.get("url")),
                dated=True,
                detail=f"RSS · group {feed.get('group') or 'none'} · {rss.get('per_feed', 15)} items",
            )
        )
    arxiv = data.get("arxiv") or {}
    categories = arxiv.get("categories") or []
    rows.append(
        _row(
            id="arxiv",
            kind="arxiv",
            name="arXiv",
            group="",
            url="https://export.arxiv.org/api/query",
            source="arxiv",
            feed_id="",
            enabled=bool(categories),
            dated=True,
            detail="Atom API · " + ", ".join(str(item) for item in categories),
        )
    )
    gdelt = data.get("gdelt") or {}
    queries = gdelt.get("queries") or []
    rows.append(
        _row(
            id="gdelt",
            kind="gdelt",
            name="GDELT",
            group="",
            url="https://api.gdeltproject.org/api/v2/doc/doc",
            source="gdelt",
            feed_id="",
            enabled=bool(queries),
            dated=True,
            detail=f"{len(queries)} queries · headlines until a full-text extract",
        )
    )
    openalex = data.get("openalex") or {}
    rows.append(
        _row(
            id="openalex",
            kind="openalex",
            name="OpenAlex",
            group="",
            url="https://api.openalex.org/works",
            source="openalex",
            feed_id="",
            enabled=bool(openalex.get("search")),
            dated=True,
            detail=str(openalex.get("search") or "no search"),
        )
    )
    bluesky = data.get("bluesky") or {}
    rows.append(
        _row(
            id="bluesky",
            kind="bluesky",
            name="Bluesky",
            group="",
            url=str(bluesky.get("endpoint") or ""),
            source="bluesky",
            feed_id="",
            enabled=bool(bluesky.get("enabled")),
            dated=True,
            detail="Sampled Jetstream window" if bluesky.get("enabled") else "Off until bluesky.enabled",
        )
    )
    mastodon = data.get("mastodon") or {}
    tags = mastodon.get("hashtags") or []
    rows.append(
        _row(
            id="mastodon",
            kind="mastodon",
            name="Mastodon",
            group="",
            url=(mastodon.get("instances") or [""])[0],
            source="mastodon",
            feed_id="",
            enabled=bool(tags),
            dated=True,
            detail=("Hashtags " + ", ".join(tags)) if tags else "Off until hashtags is set",
        )
    )
    x_cfg = data.get("x") or {}
    rows.append(
        _row(
            id="x",
            kind="x",
            name="X",
            group="",
            url="",
            source="x",
            feed_id="",
            enabled=False,
            dated=False,
            detail="Refused. The official API is paid and this project does not scrape it.",
        )
    )
    if x_cfg.get("enabled"):
        rows[-1]["detail"] = "Flag is on, and the adapter still refuses to run."
    world = data.get("worldmonitor") or {}
    count = _world_count(world, root)
    rows.append(
        _row(
            id="worldmonitor",
            kind="worldmonitor",
            name="World Monitor catalog",
            group="",
            url="",
            source="worldmonitor",
            feed_id="",
            enabled=bool(world.get("enabled")),
            dated=True,
            detail=f"{count} feed names on disk. Off unless worldmonitor.enabled.",
        )
    )
    rows.append(
        _row(
            id="upload",
            kind="upload",
            name="Manual upload",
            group="",
            url="",
            source="upload",
            feed_id="manual",
            enabled=True,
            dated=True,
            detail="Paste or a URL. The text is stripped, then scored like any other item.",
        )
    )
    return rows


def _row(**fields) -> dict:
    return {
        "configured": True,
        **fields,
    }


def _with_runtime(row: dict, stat: dict, probe: dict, error: str) -> dict:
    items = int(stat.get("items") or 0)
    if not row.get("enabled"):
        status = "off"
    elif probe.get("ok"):
        status = "live"
    elif probe and not probe.get("ok"):
        status = "quiet"
    elif items:
        status = "seen"
    else:
        status = "armed"
    return {
        **row,
        "status": status,
        "items": items,
        "oldest": stat.get("oldest") or "",
        "newest": stat.get("newest") or "",
        "probe_status": probe.get("status") or 0,
        "probe_error": probe.get("error") or "",
        "probe_at": probe.get("checked_at") or "",
        "last_error": error,
    }


def _title(feed_id: str) -> str:
    return feed_id.replace("-", " ").replace("_", " ")


def _wire_name(source: str, feed_id: str) -> str:
    if feed_id:
        return f"{source} · {feed_id}"
    return source


def _world_count(cfg: dict, root: Path) -> int:
    path = catalog_path(cfg) if cfg.get("catalog") else root / "config" / "feeds" / "worldmonitor.yaml"
    if not path.is_file():
        return 0
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    feeds = data.get("feeds") if isinstance(data, dict) else None
    return len(feeds) if isinstance(feeds, list) else 0


def _load_probes(store: Store) -> dict:
    raw = store.meta_get("catalog_probe")
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    sources = payload.get("sources") if isinstance(payload, dict) else None
    return sources if isinstance(sources, dict) else {}


def _last_errors(store: Store) -> dict[str, str]:
    raw = store.meta_get("desk_last_report")
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    found: dict[str, str] = {}
    for line in payload.get("errors") or []:
        text = str(line)
        head, _, rest = text.partition(": ")
        if head:
            found[head] = rest or text
    return found
