"""Desk export pathways. Each format is a view of the same matches."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from wt.desks import allows
from wt.evalkit import export_kev_jsonl


def filter_rows(rows: list[dict], desk: dict | None) -> list[dict]:
    return [row for row in rows if allows(desk, row["indicator_id"])]


def to_json(rows: list[dict]) -> str:
    return json.dumps(rows, ensure_ascii=False, indent=2)


def to_csv(rows: list[dict]) -> str:
    buffer = io.StringIO()
    fields = [
        "action",
        "indicator_id",
        "title",
        "url",
        "p_hit",
        "severity",
        "cosine",
        "confidence",
        "backend",
        "reason",
        "event_id",
        "created_at",
    ]
    writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue()


def to_markdown(rows: list[dict], *, desk_name: str) -> str:
    lines = [f"# {desk_name} digest", ""]
    if not rows:
        lines.append("Nothing in this desk's lanes.")
        return "\n".join(lines)
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(row["indicator_id"], []).append(row)
    for indicator_id, items in grouped.items():
        lines.append(f"## {indicator_id}")
        lines.append("")
        for row in items:
            lines.append(
                f"- **{row['action']}** {row['title']}  "
                f"(p={row['p_hit']:.2f}, severity={row['severity']:.2f}, cosine={row['cosine']:.2f})"
            )
            lines.append(f"  {row['url']}")
            if row.get("reason"):
                lines.append(f"  {row['reason']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def to_opml(sources: list[dict]) -> str:
    """OPML 2.0 source list. RSS rows carry xmlUrl; the rest are notes."""
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<opml version="2.0">',
        "  <head><title>Watch Tower sources</title></head>",
        "  <body>",
    ]
    for row in sources:
        text = _xml(row.get("name") or row.get("id") or "source")
        kind = _xml(row.get("kind") or "")
        url = row.get("url") or ""
        if url and row.get("kind") == "rss":
            lines.append(
                f'    <outline text="{text}" title="{text}" type="rss" xmlUrl="{_xml(url)}" category="{kind}"/>'
            )
        else:
            lines.append(f'    <outline text="{text}" title="{text}" category="{kind}"/>')
    lines.extend(["  </body>", "</opml>", ""])
    return "\n".join(lines)


def to_tether(sources: list[dict], rows: list[dict], *, desk_name: str, since: str, until: str) -> str:
    """One packet from sources to the decisions they became."""
    compact_sources = [
        {
            "id": row.get("id"),
            "name": row.get("name"),
            "kind": row.get("kind"),
            "status": row.get("status"),
            "url": row.get("url") or "",
            "items": row.get("items") or 0,
            "newest": row.get("newest") or "",
            "dated": bool(row.get("dated")),
        }
        for row in sources
    ]
    decisions = [
        {
            "action": row.get("action"),
            "indicator_id": row.get("indicator_id"),
            "title": row.get("title"),
            "url": row.get("url"),
            "source": row.get("source"),
            "feed_id": row.get("feed_id"),
            "published_at": row.get("published_at") or "",
            "p_hit": row.get("p_hit"),
            "reason": row.get("reason"),
        }
        for row in rows
    ]
    counts = {"push": 0, "digest": 0, "suppressed": 0, "drop": 0}
    for row in decisions:
        action = str(row.get("action") or "")
        if action in counts:
            counts[action] += 1
    return json.dumps(
        {
            "desk": desk_name,
            "since": since,
            "until": until,
            "counts": {"sources": len(compact_sources), "decisions": len(decisions), **counts},
            "decisions": decisions,
            "sources": compact_sources,
        },
        ensure_ascii=False,
        indent=2,
    )


def _xml(value: str) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def to_kev(labels_path: Path, indicators_dir: Path, desk: dict | None) -> str:
    lanes = None
    if desk and desk.get("indicators"):
        lanes = set(desk["indicators"])
    rows = export_kev_jsonl(labels_path, indicators_dir, indicator_ids=lanes)
    return "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
