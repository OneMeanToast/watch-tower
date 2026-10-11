"""Desk routes for the extraction board. Fixture data, no live model."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from wt.extract_graph.graph import load_graph
from wt.extract_graph.ontology import default_ontology
from wt.extract_graph.queue import note_high_value
from wt.extract_graph.run import FIXTURE_ID, apply_action, seed_fixture, undo_merge
from wt.store import Store

router = APIRouter()


class ActBody(BaseModel):
    action: str
    kind: str = "mention"
    id: str = ""
    type: str = ""
    left: str = ""
    right: str = ""
    judgment_id: int | None = None


def build_board(store, doc_id: str | None = None) -> dict:
    seed_fixture(store)
    docs = [
        _doc(row)
        for row in store.conn.execute(
            "SELECT * FROM extract_documents ORDER BY created_at DESC"
        ).fetchall()
    ]
    chosen = doc_id or ""
    if not any(row["id"] == chosen for row in docs):
        chosen = FIXTURE_ID if any(row["id"] == FIXTURE_ID for row in docs) else (docs[0]["id"] if docs else "")
    document = next((row for row in docs if row["id"] == chosen), None)
    mentions = _mentions(store, chosen)
    graph = load_graph(store)
    return {
        "document": document,
        "documents": [{"id": row["id"], "title": row["title"], "kind": row["kind"]} for row in docs],
        "mentions": mentions,
        "review": _review(store, chosen),
        "entities": _entities(store),
        "graph": graph,
        "types": default_ontology().entity_ids(),
        "jobs_today": _jobs_today(store),
        "daily_cap": default_ontology().high_value.daily_cap,
    }


def build_profile(store, entity_id: str) -> dict:
    resolved = _resolve(store, entity_id)
    row = store.conn.execute(
        "SELECT * FROM extract_entities WHERE id = ?", (resolved,)
    ).fetchone()
    if row is None:
        raise KeyError(entity_id)
    aliases = [
        alias["alias"]
        for alias in store.conn.execute(
            """
            SELECT DISTINCT alias FROM extract_aliases
            WHERE entity_id IN (
              SELECT id FROM extract_entities WHERE id = ? OR merged_into = ?
            )
            ORDER BY alias
            """,
            (resolved, resolved),
        ).fetchall()
    ]
    mentions = []
    for mention in store.conn.execute(
        """
        SELECT m.*, d.title, d.text
        FROM extract_mentions m
        JOIN extract_documents d ON d.id = m.doc_id
        WHERE m.entity_id IN (
          SELECT id FROM extract_entities WHERE id = ? OR merged_into = ?
        )
          AND m.status != 'rejected'
        ORDER BY m.start
        """,
        (resolved, resolved),
    ):
        text = mention["text"] or ""
        left = max(0, mention["start"] - 48)
        right = min(len(text), mention["end"] + 48)
        mentions.append(
            {
                "id": mention["id"],
                "doc_id": mention["doc_id"],
                "title": mention["title"],
                "surface": mention["surface"],
                "type": mention["type"],
                "status": mention["status"],
                "context": text[left:right],
                "p": mention["p_jev"] if mention["p_jev"] is not None else mention["p_local"],
            }
        )
    relations = []
    for relation in store.conn.execute(
        """
        SELECT * FROM extract_relations
        WHERE status != 'rejected'
          AND (head_entity = ? OR tail_entity = ? OR head_entity IN (
                SELECT id FROM extract_entities WHERE merged_into = ?
              ) OR tail_entity IN (
                SELECT id FROM extract_entities WHERE merged_into = ?
              ))
        """,
        (resolved, resolved, resolved, resolved),
    ):
        relations.append(
            {
                "id": relation["id"],
                "type": relation["type"],
                "modality": relation["modality"],
                "head": _caption(store, relation["head_entity"]),
                "tail": _caption(store, relation["tail_entity"]),
                "status": relation["status"],
            }
        )
    timeline = []
    for event in store.conn.execute(
        """
        SELECT DISTINCT e.*
        FROM extract_events e
        JOIN extract_event_roles r ON r.event_id = e.id
        WHERE e.status != 'rejected'
          AND r.entity_id IN (
            SELECT id FROM extract_entities WHERE id = ? OR merged_into = ?
          )
        ORDER BY e.when_norm, e.created_at
        """,
        (resolved, resolved),
    ):
        roles = [
            {"role": role["role"], "caption": _caption(store, role["entity_id"])}
            for role in store.conn.execute(
                "SELECT role, entity_id FROM extract_event_roles WHERE event_id = ?",
                (event["id"],),
            ).fetchall()
        ]
        timeline.append(
            {
                "id": event["id"],
                "type": event["type"],
                "cameo": event["cameo_code"],
                "trigger": event["trigger"],
                "when": event["when_norm"],
                "roles": roles,
            }
        )
    return {
        "id": resolved,
        "schema": row["schema"],
        "caption": row["caption"],
        "wikidata_qid": row["wikidata_qid"],
        "aliases": aliases,
        "mentions": mentions,
        "relations": relations,
        "timeline": timeline,
    }


def _open():
    from wt.deskapp import desk_db

    return Store(desk_db())


@router.get("/extract/board")
def extract_board(doc: str | None = None) -> dict:
    store = _open()
    try:
        return build_board(store, doc)
    finally:
        store.close()


@router.get("/extract/entities/{entity_id}")
def extract_entity(entity_id: str) -> dict:
    store = _open()
    try:
        try:
            return build_profile(store, entity_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="unknown entity") from exc
    finally:
        store.close()


@router.post("/extract/act")
def extract_act(body: ActBody) -> dict:
    store = _open()
    try:
        if body.action == "undo" and body.judgment_id is not None:
            undo_merge(store, body.judgment_id)
            return {"ok": True, "action": "undo"}
        try:
            return apply_action(store, body.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        store.close()


@router.post("/extract/documents/{doc_id}/star")
def extract_star(doc_id: str) -> dict:
    store = _open()
    try:
        row = store.conn.execute(
            "SELECT * FROM extract_documents WHERE id = ?", (doc_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="unknown document")
        store.conn.execute(
            "UPDATE extract_documents SET starred = 1 WHERE id = ?", (doc_id,)
        )
        store.conn.commit()
        job = None
        if row["item_id"]:
            item = store.get_item(row["item_id"])
            if item is not None:
                job = note_high_value(store, item, starred=True)
        return {"ok": True, "starred": True, "job": job}
    finally:
        store.close()


def _doc(row) -> dict:
    return {
        "id": row["id"],
        "title": row["title"],
        "text": row["text"],
        "kind": row["kind"],
        "starred": bool(row["starred"]),
        "local_only": bool(row["local_only"]),
    }


def _mentions(store, doc_id: str) -> list[dict]:
    if not doc_id:
        return []
    rows = []
    for row in store.conn.execute(
        "SELECT * FROM extract_mentions WHERE doc_id = ? ORDER BY start, end",
        (doc_id,),
    ):
        rows.append(
            {
                "id": row["id"],
                "start": row["start"],
                "end": row["end"],
                "surface": row["surface"],
                "type": row["type"],
                "entity_id": row["entity_id"],
                "status": row["status"],
                "p": row["p_jev"] if row["p_jev"] is not None else row["p_local"],
                "extractor": row["extractor"],
            }
        )
    return rows


def _review(store, doc_id: str) -> list[dict]:
    rows = []
    if doc_id:
        for row in store.conn.execute(
            """
            SELECT * FROM extract_mentions
            WHERE doc_id = ? AND status = 'proposed'
            ORDER BY COALESCE(p_jev, p_local, 0.5) ASC, start
            """,
            (doc_id,),
        ):
            rows.append(
                {
                    "kind": "mention",
                    "id": row["id"],
                    "label": row["surface"],
                    "type": row["type"],
                    "status": row["status"],
                    "p": row["p_jev"] if row["p_jev"] is not None else row["p_local"],
                    "entity_id": row["entity_id"],
                }
            )
        for row in store.conn.execute(
            """
            SELECT * FROM extract_relations
            WHERE doc_id = ? AND status = 'proposed'
            ORDER BY COALESCE(p, 0.5) ASC
            """,
            (doc_id,),
        ):
            rows.append(
                {
                    "kind": "relation",
                    "id": row["id"],
                    "label": f"{_caption(store, row['head_entity'])} — {row['type']} — {_caption(store, row['tail_entity'])}",
                    "type": row["type"],
                    "status": row["status"],
                    "p": row["p"],
                    "modality": row["modality"],
                }
            )
    for row in store.conn.execute(
        """
        SELECT * FROM extract_judgments
        WHERE judgment = 'unsure' AND undone = 0
        ORDER BY COALESCE(p, 0.5) ASC
        """
    ):
        rows.append(
            {
                "kind": "judgment",
                "id": str(row["id"]),
                "label": f"{_caption(store, row['left_id'])} same as {_caption(store, row['right_id'])}?",
                "type": "resolution",
                "status": "unsure",
                "p": row["p"],
                "left": row["left_id"],
                "right": row["right_id"],
            }
        )
    return rows


def _entities(store) -> list[dict]:
    rows = []
    for row in store.conn.execute(
        """
        SELECT id, schema, caption, wikidata_qid
        FROM extract_entities
        WHERE merged_into IS NULL
        ORDER BY caption
        """
    ):
        rows.append(
            {
                "id": row["id"],
                "schema": row["schema"],
                "caption": row["caption"],
                "wikidata_qid": row["wikidata_qid"],
            }
        )
    return rows


def _caption(store, entity_id: str | None) -> str:
    if not entity_id:
        return ""
    resolved = _resolve(store, entity_id)
    row = store.conn.execute(
        "SELECT caption FROM extract_entities WHERE id = ?", (resolved,)
    ).fetchone()
    return "" if row is None else row["caption"]


def _resolve(store, entity_id: str) -> str:
    current = entity_id
    seen: set[str] = set()
    while current and current not in seen:
        seen.add(current)
        row = store.conn.execute(
            "SELECT merged_into FROM extract_entities WHERE id = ?", (current,)
        ).fetchone()
        if row is None or not row["merged_into"]:
            return current
        current = row["merged_into"]
    return entity_id


def _jobs_today(store) -> int:
    from wt.text import utcnow

    today = utcnow().date().isoformat()
    return int(
        store.conn.execute(
            """
            SELECT COUNT(*) AS n FROM extract_jobs
            WHERE substr(created_at, 1, 10) = ?
            """,
            (today,),
        ).fetchone()["n"]
    )
