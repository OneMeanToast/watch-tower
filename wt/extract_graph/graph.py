"""Read-only projection of the SQLite assertions.

A later graph store (Ladybug or otherwise) can implement the same GraphView.
Nothing here opens a second database.
"""

from __future__ import annotations

from typing import Protocol


class GraphView(Protocol):
    def snapshot(self) -> dict: ...


class SqliteGraphView:
    def __init__(self, conn):
        self.conn = conn

    def snapshot(self) -> dict:
        nodes = []
        live = {}
        for row in self.conn.execute(
            """
            SELECT id, schema, caption, wikidata_qid
            FROM extract_entities
            WHERE merged_into IS NULL
            ORDER BY caption
            """
        ):
            live[row["id"]] = True
            nodes.append(
                {
                    "id": row["id"],
                    "schema": row["schema"],
                    "caption": row["caption"],
                    "wikidata_qid": row["wikidata_qid"],
                }
            )
        edges = []
        for row in self.conn.execute(
            """
            SELECT id, type, head_entity, tail_entity, modality, status
            FROM extract_relations
            WHERE status != 'rejected'
            """
        ):
            source = _resolve(self.conn, row["head_entity"])
            target = _resolve(self.conn, row["tail_entity"])
            if not source or not target or source == target:
                continue
            if source not in live or target not in live:
                continue
            edges.append(
                {
                    "id": row["id"],
                    "source": source,
                    "target": target,
                    "type": row["type"],
                    "modality": row["modality"],
                }
            )
        for row in self.conn.execute(
            """
            SELECT e.id, e.type, e.status
            FROM extract_events e
            WHERE e.status != 'rejected'
            """
        ):
            roles = self.conn.execute(
                "SELECT role, entity_id FROM extract_event_roles WHERE event_id = ?",
                (row["id"],),
            ).fetchall()
            actors = [r["entity_id"] for r in roles if r["role"] == "actor"]
            targets = [r["entity_id"] for r in roles if r["role"] == "target"]
            if not actors or not targets:
                continue
            source = _resolve(self.conn, actors[0])
            target = _resolve(self.conn, targets[0])
            if not source or not target or source == target:
                continue
            if source not in live or target not in live:
                continue
            edges.append(
                {
                    "id": f"event:{row['id']}",
                    "source": source,
                    "target": target,
                    "type": row["type"],
                    "modality": "event",
                }
            )
        return {"nodes": nodes, "edges": edges}


def _resolve(conn, entity_id: str | None) -> str:
    current = entity_id or ""
    seen: set[str] = set()
    while current and current not in seen:
        seen.add(current)
        row = conn.execute(
            "SELECT merged_into FROM extract_entities WHERE id = ?", (current,)
        ).fetchone()
        if row is None or not row["merged_into"]:
            return current
        current = row["merged_into"]
    return entity_id or ""
