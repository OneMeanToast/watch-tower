"""SQLite job queue. High-value items are enqueued; the daily cap is in the ontology."""

from __future__ import annotations

import logging
import sqlite3

from wt.extract_graph.ontology import Ontology, default_ontology
from wt.extract_graph.run import extract_text
from wt.text import sha256_text, utcnow

log = logging.getLogger("wt.extract_graph.queue")

JOB_KIND = "extract"


def note_high_value(
    store,
    item,
    *,
    pushed: bool = False,
    starred: bool = False,
    ripped: bool | None = None,
    p_hit: float = 0.0,
    ontology: Ontology | None = None,
) -> str | None:
    """Enqueue one extraction. A queue error must not fail an alert."""
    try:
        return _enqueue(
            store,
            item,
            pushed=pushed,
            starred=starred,
            ripped=ripped,
            p_hit=p_hit,
            ontology=ontology or default_ontology(),
        )
    except Exception:
        log.exception("extract enqueue failed")
        return None


def _enqueue(
    store,
    item,
    *,
    pushed: bool,
    starred: bool,
    ripped: bool | None,
    p_hit: float,
    ontology: Ontology,
) -> str | None:
    rule = ontology.high_value
    reasons: list[str] = []
    if pushed and "pushed_alert" in rule.when:
        reasons.append("pushed_alert")
    if starred and "starred" in rule.when:
        reasons.append("starred")
    ripped_flag = _is_ripped(item) if ripped is None else ripped
    if ripped_flag and "ripped" in rule.when:
        reasons.append("ripped")
    source = str(_field(item, "source", ""))
    if source in rule.paper_sources and "paper" in rule.when:
        reasons.append("paper")
    if source in rule.advisory_sources and "advisory" in rule.when:
        reasons.append("advisory")
    if "tier_a" in rule.when and float(p_hit or 0) >= float(rule.tier_a_min_p):
        groups = _field(item, "groups", []) or []
        if source in rule.tier_a_sources or any(str(group) in rule.tier_a_groups for group in groups):
            reasons.append("tier_a")
    if not reasons:
        return None
    item_id = str(_field(item, "id", ""))
    if not item_id:
        return None
    existing = store.conn.execute(
        """
        SELECT id FROM extract_jobs
        WHERE item_id = ? AND kind = ? AND state IN ('queued', 'running', 'done')
        """,
        (item_id, JOB_KIND),
    ).fetchone()
    if existing is not None:
        return str(existing["id"])
    today = utcnow().date().isoformat()
    used = store.conn.execute(
        """
        SELECT COUNT(*) AS n FROM extract_jobs
        WHERE substr(created_at, 1, 10) = ?
          AND state IN ('queued', 'running', 'done')
        """,
        (today,),
    ).fetchone()["n"]
    if int(used) >= int(rule.daily_cap):
        log.info("extract daily cap of %s is full; skipping %s", rule.daily_cap, item_id)
        return None
    now = utcnow().isoformat()
    job_id = sha256_text(f"{item_id}:{JOB_KIND}")[:16]
    try:
        store.conn.execute(
            """
            INSERT INTO extract_jobs(
              id, kind, item_id, state, attempts, run_after, reason, error, created_at
            ) VALUES (?, ?, ?, 'queued', 0, ?, ?, '', ?)
            """,
            (job_id, JOB_KIND, item_id, now, ",".join(reasons), now),
        )
        store.conn.commit()
    except sqlite3.IntegrityError:
        row = store.conn.execute(
            "SELECT id FROM extract_jobs WHERE item_id = ? AND kind = ?",
            (item_id, JOB_KIND),
        ).fetchone()
        return None if row is None else str(row["id"])
    return job_id


def run_once(store, *, ask=None, proposer=None, deep=None) -> str | None:
    """Run the oldest queued job. Returns the document id, or None when idle."""
    now = utcnow().isoformat()
    job = store.conn.execute(
        """
        SELECT * FROM extract_jobs
        WHERE state = 'queued' AND run_after <= ?
        ORDER BY created_at
        LIMIT 1
        """,
        (now,),
    ).fetchone()
    if job is None:
        return None
    store.conn.execute(
        "UPDATE extract_jobs SET state = 'running', attempts = attempts + 1 WHERE id = ?",
        (job["id"],),
    )
    store.conn.commit()
    try:
        doc_id = _run_job(store, job, ask=ask, proposer=proposer, deep=deep)
    except Exception as exc:
        store.conn.execute(
            "UPDATE extract_jobs SET state = 'error', error = ? WHERE id = ?",
            (str(exc)[:500], job["id"]),
        )
        store.conn.commit()
        log.warning("extract job %s failed (%s)", job["id"], exc)
        return None
    store.conn.execute(
        "UPDATE extract_jobs SET state = 'done', error = '' WHERE id = ?",
        (job["id"],),
    )
    store.conn.commit()
    return doc_id


def drain(store, *, ask=None, proposer=None, deep=None, limit: int = 20) -> int:
    done = 0
    for _ in range(limit):
        if run_once(store, ask=ask, proposer=proposer, deep=deep) is None:
            break
        done += 1
    return done


def _run_job(store, job, *, ask, proposer, deep=None) -> str:
    item = store.get_item(job["item_id"])
    if item is None:
        raise RuntimeError(f"item {job['item_id']} is not in the store")
    local_only = _row_local_only(item)
    text = item["text"] or item["title"]
    return extract_text(
        store,
        doc_id=sha256_text(f"item:{item['id']}")[:16],
        title=item["title"] or item["url"] or item["id"],
        text=text,
        kind=_kind_for(item, job["reason"]),
        local_only=local_only,
        item_id=item["id"],
        proposer=proposer,
        ask=None if local_only else ask,
        deep=deep,
    )


def _kind_for(item, reason: str) -> str:
    source = str(item["source"] or "")
    if "advisory" in reason or source in {"cisa_kev", "nvd"}:
        return "advisory"
    if "paper" in reason or source in {"arxiv", "openalex"}:
        return "paper"
    return source


def _row_local_only(item) -> bool:
    source = str(item["source"] or "")
    method = str(item["rip_method"] or "")
    if source in {"file", "private"}:
        return True
    return method == "paste"


def _is_ripped(item) -> bool:
    source = str(_field(item, "source", ""))
    groups = _field(item, "groups", []) or []
    source_type = str(_field(item, "source_type", ""))
    if source in {"upload", "manual"}:
        return True
    if source_type == "manual_rip":
        return True
    return "manual" in groups or "rip" in groups


def _field(item, name: str, default):
    if isinstance(item, dict):
        return item.get(name, default)
    try:
        return item[name]
    except Exception:
        return getattr(item, name, default)
