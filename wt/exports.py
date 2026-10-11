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


def to_kev(labels_path: Path, indicators_dir: Path, desk: dict | None) -> str:
    lanes = None
    if desk and desk.get("indicators"):
        lanes = set(desk["indicators"])
    rows = export_kev_jsonl(labels_path, indicators_dir, indicator_ids=lanes)
    return "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
