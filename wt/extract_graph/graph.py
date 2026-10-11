"""SQLite is the source of truth. LadybugDB is a rebuildable Cypher projection.

The desk reads Ladybug when the package is installed and the store is a file.
Otherwise it reads the same edges from SQLite.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol

log = logging.getLogger("wt.extract_graph.graph")


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
        return {"nodes": nodes, "edges": edges, "backend": "sqlite"}


class LadybugGraphView:
    """Embedded Cypher projection. Rebuilt from SQLite on each read."""

    def __init__(self, path: Path):
        import ladybug as lb

        self.path = path
        self._lb = lb
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = lb.Database(str(path))
        self.conn = lb.Connection(self.db)

    def rebuild(self, sqlite_conn) -> None:
        snap = SqliteGraphView(sqlite_conn).snapshot()
        self.conn.execute(
            "CREATE NODE TABLE IF NOT EXISTS Entity(id STRING PRIMARY KEY, schema STRING, caption STRING)"
        )
        self.conn.execute(
            "CREATE REL TABLE IF NOT EXISTS LINK(FROM Entity TO Entity, kind STRING, modality STRING)"
        )
        self.conn.execute("MATCH (a:Entity) DETACH DELETE a")
        for node in snap["nodes"]:
            self.conn.execute(
                "CREATE (e:Entity {id: $id, schema: $schema, caption: $caption})",
                {"id": node["id"], "schema": node["schema"], "caption": node["caption"]},
            )
        for edge in snap["edges"]:
            self.conn.execute(
                """
                MATCH (a:Entity {id: $source}), (b:Entity {id: $target})
                CREATE (a)-[:LINK {kind: $kind, modality: $modality}]->(b)
                """,
                {
                    "source": edge["source"],
                    "target": edge["target"],
                    "kind": edge["type"],
                    "modality": edge["modality"],
                },
            )

    def snapshot(self) -> dict:
        nodes = []
        for row in self.conn.execute("MATCH (a:Entity) RETURN a.id, a.caption, a.schema"):
            nodes.append({"id": row[0], "caption": row[1], "schema": row[2], "wikidata_qid": None})
        edges = []
        query = (
            "MATCH (a:Entity)-[r:LINK]->(b:Entity) "
            "RETURN a.id, b.id, r.kind, r.modality"
        )
        for index, row in enumerate(self.conn.execute(query)):
            edges.append(
                {
                    "id": f"lb-{index}",
                    "source": row[0],
                    "target": row[1],
                    "type": row[2],
                    "modality": row[3],
                }
            )
        nodes.sort(key=lambda row: row["caption"] or "")
        return {"nodes": nodes, "edges": edges, "backend": "ladybug"}


def graph_path_for(store) -> Path | None:
    path = getattr(store, "path", None)
    if path is None or str(path) == ":memory:":
        return None
    return Path(str(path) + ".lbdb")


def load_graph(store) -> dict:
    """Prefer the Ladybug projection. Fall back to SQLite queries."""
    path = graph_path_for(store)
    if path is None:
        return SqliteGraphView(store.conn).snapshot()
    try:
        view = LadybugGraphView(path)
        view.rebuild(store.conn)
        return view.snapshot()
    except Exception as exc:
        log.info("ladybug graph unavailable (%s); reading SQLite", exc)
        return SqliteGraphView(store.conn).snapshot()


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
