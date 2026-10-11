"""Write one document's assertions. Every relation and event points at mention spans."""

from __future__ import annotations

import json
from datetime import datetime

from wt.extract_graph.ground import normalise_dates
from wt.extract_graph.link import EntityLinker, StubLinker
from wt.extract_graph.ontology import Ontology, default_ontology
from wt.extract_graph.propose import Proposal, Proposer, StubProposer, build_proposer
from wt.extract_graph.resolve import SuffixResolver
from wt.extract_graph.verify import dump_answers, verify_proposal
from wt.text import sha256_text, utcnow

FIXTURE_TEXT = (
    "On 2 March 2026, CISA added CVE-2024-12345 to the Known Exploited "
    "Vulnerabilities catalog. The flaw affects ExampleSoft Gateway, built by "
    "ExampleSoft. MIT researchers said the Llama weights were released under "
    "an open license. ExampleSoft Inc. confirmed it may adopt Llama."
)
FIXTURE_ID = "fx-advisory-1"
FIXTURE_TITLE = "CISA adds CVE-2024-12345; ExampleSoft and Llama"


def extract_text(
    store,
    *,
    doc_id: str,
    title: str,
    text: str,
    kind: str = "",
    local_only: bool = False,
    published: datetime | None = None,
    item_id: str = "",
    proposer: Proposer | None = None,
    ask=None,
    ontology: Ontology | None = None,
    linker: EntityLinker | None = None,
) -> str:
    ontology = ontology or default_ontology()
    proposer = proposer or StubProposer(ontology)
    proposal = proposer.propose(text)
    model_version = ""
    if ask is not None and not local_only:
        proposal, model_version = verify_proposal(text, proposal, ask, ontology)
    _write(
        store,
        doc_id=doc_id,
        title=title,
        text=text,
        kind=kind,
        local_only=local_only,
        published=published,
        item_id=item_id,
        proposal=proposal,
        model_version=model_version,
        ontology=ontology,
        linker=linker or StubLinker(),
    )
    return doc_id


def seed_fixture(store) -> str:
    row = store.conn.execute(
        "SELECT id FROM extract_documents WHERE id = ?", (FIXTURE_ID,)
    ).fetchone()
    if row is not None:
        return FIXTURE_ID
    return extract_text(
        store,
        doc_id=FIXTURE_ID,
        title=FIXTURE_TITLE,
        text=FIXTURE_TEXT,
        kind="advisory",
        local_only=True,
        published=datetime(2026, 3, 2),
        proposer=build_proposer("stub"),
        ask=None,
    )


def _write(
    store,
    *,
    doc_id: str,
    title: str,
    text: str,
    kind: str,
    local_only: bool,
    published: datetime | None,
    item_id: str,
    proposal: Proposal,
    model_version: str,
    ontology: Ontology,
    linker: EntityLinker,
) -> None:
    now = utcnow().isoformat()
    store.conn.execute(
        """
        INSERT INTO extract_documents(
          id, item_id, title, text, text_sha256, lang, kind, starred, local_only, created_at
        ) VALUES (?, ?, ?, ?, ?, 'en', ?, 0, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
          title = excluded.title, text = excluded.text, text_sha256 = excluded.text_sha256,
          kind = excluded.kind, local_only = excluded.local_only
        """,
        (doc_id, item_id, title, text, sha256_text(text), kind, 1 if local_only else 0, now),
    )
    store.conn.execute("DELETE FROM extract_mentions WHERE doc_id = ? AND status = 'proposed'", (doc_id,))
    store.conn.execute("DELETE FROM extract_relations WHERE doc_id = ? AND status = 'proposed'", (doc_id,))
    proposed_events = store.conn.execute(
        "SELECT id FROM extract_events WHERE doc_id = ? AND status = 'proposed'", (doc_id,)
    ).fetchall()
    for row in proposed_events:
        store.conn.execute("DELETE FROM extract_event_roles WHERE event_id = ?", (row["id"],))
    store.conn.execute("DELETE FROM extract_events WHERE doc_id = ? AND status = 'proposed'", (doc_id,))
    mention_ids: list[str] = []
    entity_for: dict[int, str] = {}
    clusters = proposal.clusters or [[index] for index in range(len(proposal.mentions))]
    for cluster in clusters:
        if not cluster:
            continue
        head = proposal.mentions[cluster[0]]
        entity_id = sha256_text(f"{head.type}:{head.surface.casefold()}")[:16]
        entity_for.update({index: entity_id for index in cluster})
        qid = _qid(linker, head.surface, head.type)
        store.conn.execute(
            """
            INSERT INTO extract_entities(id, schema, caption, wikidata_qid, merged_into, created_at)
            VALUES (?, ?, ?, ?, NULL, ?)
            ON CONFLICT(id) DO NOTHING
            """,
            (entity_id, head.type, head.surface, qid, now),
        )
    dates = {(row["start"], row["end"]): row["iso"] for row in normalise_dates(text, published)}
    for index, mention in enumerate(proposal.mentions):
        if mention.type == "not_an_entity":
            mention_ids.append("")
            continue
        mention_id = sha256_text(f"{doc_id}:{mention.start}:{mention.end}:{mention.surface}")[:16]
        mention_ids.append(mention_id)
        entity_id = entity_for.get(index)
        store.conn.execute(
            """
            INSERT INTO extract_mentions(
              id, doc_id, start, end, surface, type, entity_id, extractor, extractor_version,
              model_version, p_local, p_jev, status, answers_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'proposed', ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              type = excluded.type, p_jev = excluded.p_jev, model_version = excluded.model_version,
              answers_json = excluded.answers_json
            WHERE extract_mentions.status = 'proposed'
            """,
            (
                mention_id,
                doc_id,
                mention.start,
                mention.end,
                mention.surface,
                mention.type,
                entity_id,
                mention.extractor,
                "1",
                model_version,
                mention.p_local,
                getattr(mention, "p_jev", None),
                dump_answers(mention),
                now,
            ),
        )
        if entity_id:
            store.conn.execute(
                """
                INSERT INTO extract_aliases(entity_id, alias, doc_id, mention_id)
                SELECT ?, ?, ?, ?
                WHERE NOT EXISTS (
                  SELECT 1 FROM extract_aliases WHERE entity_id = ? AND alias = ? AND mention_id = ?
                )
                """,
                (entity_id, mention.surface, doc_id, mention_id, entity_id, mention.surface, mention_id),
            )
    for relation in proposal.relations:
        if relation.type == "none":
            continue
        head_id = mention_ids[relation.head] if relation.head < len(mention_ids) else ""
        tail_id = mention_ids[relation.tail] if relation.tail < len(mention_ids) else ""
        if not head_id or not tail_id:
            continue
        head_entity = entity_for.get(relation.head) or ""
        tail_entity = entity_for.get(relation.tail) or ""
        relation_id = sha256_text(f"{doc_id}:{relation.type}:{head_id}:{tail_id}")[:16]
        store.conn.execute(
            """
            INSERT INTO extract_relations(
              id, type, head_entity, tail_entity, head_mention, tail_mention, doc_id,
              start, end, modality, p, status, answers_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'proposed', ?, ?)
            ON CONFLICT(id) DO NOTHING
            """,
            (
                relation_id,
                relation.type,
                head_entity,
                tail_entity,
                head_id,
                tail_id,
                doc_id,
                relation.start,
                relation.end,
                relation.modality,
                getattr(relation, "p_jev", None) if getattr(relation, "p_jev", None) is not None else relation.p_local,
                dump_answers(relation),
                now,
            ),
        )
    for event in proposal.events:
        trigger_mention = ""
        for index, mention in enumerate(proposal.mentions):
            if mention.start == event.start and index < len(mention_ids):
                trigger_mention = mention_ids[index]
                break
        if not trigger_mention and event.roles:
            trigger_mention = mention_ids[event.roles[0][1]]
        if not trigger_mention:
            continue
        when = _event_when(text, event, proposal, dates)
        event_id = sha256_text(f"{doc_id}:{event.type}:{trigger_mention}")[:16]
        store.conn.execute(
            """
            INSERT INTO extract_events(
              id, type, cameo_code, doc_id, mention_id, start, end, trigger, when_norm,
              p, status, answers_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'proposed', ?, ?)
            ON CONFLICT(id) DO NOTHING
            """,
            (
                event_id,
                event.type,
                event.cameo or ontology.cameo_for(event.type),
                doc_id,
                trigger_mention,
                event.start,
                event.end,
                event.trigger,
                when,
                getattr(event, "p_jev", None) if getattr(event, "p_jev", None) is not None else event.p_local,
                dump_answers(event),
                now,
            ),
        )
        for role, mention_index in event.roles:
            if mention_index >= len(mention_ids) or not mention_ids[mention_index]:
                continue
            store.conn.execute(
                """
                INSERT INTO extract_event_roles(event_id, role, entity_id, mention_id)
                SELECT ?, ?, ?, ?
                WHERE NOT EXISTS (
                  SELECT 1 FROM extract_event_roles
                  WHERE event_id = ? AND role = ? AND mention_id = ?
                )
                """,
                (
                    event_id,
                    role,
                    entity_for.get(mention_index) or "",
                    mention_ids[mention_index],
                    event_id,
                    role,
                    mention_ids[mention_index],
                ),
            )
    entities = [
        dict(row)
        for row in store.conn.execute(
            "SELECT id, schema, caption, merged_into FROM extract_entities"
        ).fetchall()
    ]
    for judgment in SuffixResolver().judge(entities):
        exists = store.conn.execute(
            """
            SELECT 1 FROM extract_judgments
            WHERE left_id = ? AND right_id = ? AND undone = 0
            """,
            (judgment.left_id, judgment.right_id),
        ).fetchone()
        if exists:
            continue
        store.conn.execute(
            """
            INSERT INTO extract_judgments(
              left_id, right_id, judgment, source, p, reversible, undone, created_at
            ) VALUES (?, ?, ?, ?, ?, 1, 0, ?)
            """,
            (judgment.left_id, judgment.right_id, judgment.judgment, judgment.source, judgment.p, now),
        )
    store.conn.commit()


def _event_when(text: str, event, proposal: Proposal, dates: dict) -> str:
    """Attach a code-normalised date that sits next to the trigger. No model call."""
    window_start = max(0, event.start - 120)
    window_end = min(len(text), event.end + 120)
    window = text[window_start:window_end]
    best = ""
    best_dist = 10**9
    for mention in proposal.mentions:
        if mention.type != "Date":
            continue
        iso = dates.get((mention.start, mention.end))
        if not iso or mention.surface not in window:
            continue
        dist = abs(mention.start - event.start)
        if dist < best_dist:
            best = str(iso)
            best_dist = dist
    return best


def _qid(linker: EntityLinker, surface: str, schema: str) -> str | None:
    found = linker.candidates(surface, schema)
    if not found:
        return None
    return found[0].qid


def apply_action(store, body: dict) -> dict:
    action = str(body.get("action") or "")
    kind = str(body.get("kind") or "mention")
    target = str(body.get("id") or "")
    now = utcnow().isoformat()
    if action not in {"accept", "reject", "edit", "merge"}:
        raise ValueError("action must be accept, reject, edit, or merge")
    before = _snapshot(store, kind, target, body)
    if action == "merge":
        _merge(store, str(body.get("left") or ""), str(body.get("right") or ""), now)
        target = str(body.get("left") or target)
        kind = "entity"
    elif kind == "mention":
        _mention_status(store, target, action, body.get("type"))
    elif kind == "relation":
        _status_table(store, "extract_relations", target, action)
    elif kind == "event":
        _status_table(store, "extract_events", target, action)
    elif kind == "judgment":
        _judgment_action(store, target, action, now)
    else:
        raise ValueError(f"unknown target {kind}")
    after = _snapshot(store, kind, target, body)
    store.conn.execute(
        """
        INSERT INTO extract_actions(target_kind, target_id, action, before_json, after_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (kind, target, action, json.dumps(before), json.dumps(after), now),
    )
    store.conn.commit()
    return {"ok": True, "action": action, "id": target}


def _mention_status(store, mention_id: str, action: str, new_type) -> None:
    status = {"accept": "accepted", "reject": "rejected", "edit": "edited"}[action]
    if action == "edit":
        if not new_type:
            raise ValueError("edit needs a type")
        store.conn.execute(
            "UPDATE extract_mentions SET status = ?, type = ? WHERE id = ?",
            (status, str(new_type), mention_id),
        )
        return
    store.conn.execute(
        "UPDATE extract_mentions SET status = ? WHERE id = ?",
        (status, mention_id),
    )


def _status_table(store, table: str, row_id: str, action: str) -> None:
    status = {"accept": "accepted", "reject": "rejected", "edit": "edited"}[action]
    store.conn.execute(f"UPDATE {table} SET status = ? WHERE id = ?", (status, row_id))


def _merge(store, left: str, right: str, now: str) -> None:
    if not left or not right or left == right:
        raise ValueError("merge needs two entities")
    _point_merge(store, left, right)
    store.conn.execute(
        """
        INSERT INTO extract_judgments(
          left_id, right_id, judgment, source, p, reversible, undone, created_at
        ) VALUES (?, ?, 'yes', 'analyst', 1, 1, 0, ?)
        """,
        (left, right, now),
    )


def _point_merge(store, left: str, right: str) -> None:
    """Point right at left. Mentions stay put so undo only clears merged_into."""
    if not left or not right or left == right:
        raise ValueError("merge needs two entities")
    store.conn.execute(
        "UPDATE extract_entities SET merged_into = ? WHERE id = ?",
        (left, right),
    )


def undo_merge(store, judgment_id: int) -> None:
    row = store.conn.execute(
        "SELECT * FROM extract_judgments WHERE id = ?", (judgment_id,)
    ).fetchone()
    if row is None or not row["reversible"]:
        raise ValueError("judgment cannot be undone")
    if row["judgment"] == "yes":
        store.conn.execute(
            "UPDATE extract_entities SET merged_into = NULL WHERE id = ? AND merged_into = ?",
            (row["right_id"], row["left_id"]),
        )
    store.conn.execute(
        "UPDATE extract_judgments SET undone = 1 WHERE id = ?", (judgment_id,)
    )
    store.conn.commit()


def _judgment_action(store, judgment_id: str, action: str, now: str) -> None:
    row = store.conn.execute(
        "SELECT * FROM extract_judgments WHERE id = ?", (judgment_id,)
    ).fetchone()
    if row is None:
        raise ValueError("unknown judgment")
    if action == "accept":
        mapped = "yes"
    elif action == "reject":
        mapped = "no"
    else:
        mapped = "unsure"
    store.conn.execute(
        """
        INSERT INTO extract_judgments(
          left_id, right_id, judgment, source, p, reversible, undone, created_at
        ) VALUES (?, ?, ?, 'analyst', ?, 1, 0, ?)
        """,
        (row["left_id"], row["right_id"], mapped, row["p"], now),
    )
    if mapped == "yes":
        _point_merge(store, row["left_id"], row["right_id"])


def _snapshot(store, kind: str, target: str, body: dict) -> dict:
    if kind == "mention" and target:
        row = store.conn.execute("SELECT * FROM extract_mentions WHERE id = ?", (target,)).fetchone()
        return {} if row is None else {"status": row["status"], "type": row["type"]}
    if kind == "entity" or body.get("action") == "merge":
        return {"left": body.get("left"), "right": body.get("right")}
    return {"id": target}
