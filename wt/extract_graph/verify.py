"""Jev checks the local candidates. One request per chunk. Offsets stay in code."""

from __future__ import annotations

import json
import logging

from wt.extract_graph.ground import code_facts
from wt.extract_graph.ontology import Ontology
from wt.extract_graph.propose import EventCand, MentionCand, Proposal, RelationCand, windows
from wt.jev import rotate_choice
from wt.safety import fence_untrusted
from wt.text import sha256_text

log = logging.getLogger("wt.extract_graph.verify")


def verify_proposal(text: str, proposal: Proposal, ask, ontology: Ontology) -> tuple[Proposal, str]:
    """ask(state, questions) -> the System One JSON body. Returns the model id that answered."""
    model_version = ""
    if ask is None or not proposal.mentions:
        return proposal, model_version
    by_chunk: list[tuple[str, list[int]]] = []
    for start, end, chunk in windows(text):
        indexes = [index for index, mention in enumerate(proposal.mentions) if start <= mention.start < end]
        if indexes:
            by_chunk.append((chunk, indexes))
    if not by_chunk:
        by_chunk = [(text, list(range(len(proposal.mentions))))]
    for chunk, indexes in by_chunk:
        questions = _questions(proposal, indexes, ontology, chunk)
        if not questions:
            continue
        state = code_facts(chunk) + "\n" + fence_untrusted("chunk", _marked(chunk, proposal, indexes), "")
        try:
            payload = ask(state, questions)
        except Exception as exc:
            log.warning("jev extract verify skipped (%s)", exc)
            continue
        model_version = str(payload.get("model") or model_version)
        _apply(payload.get("answers") or {}, proposal, indexes, ontology)
    return proposal, model_version


def _questions(proposal: Proposal, indexes: list[int], ontology: Ontology, chunk: str) -> dict:
    questions = {}
    types = {row.id: row.id for row in ontology.entities}
    types["not_an_entity"] = "not a named thing"
    relations = {row.id: row.id for row in ontology.relations}
    relations["none"] = "the text states no relation"
    events = {row.id: (row.cameo or row.id) for row in ontology.events}
    salt = sha256_text(chunk)
    for index in indexes:
        mention = proposal.mentions[index]
        questions[f"type:{index}"] = {
            "type": "choice",
            "instructions": f"What type is mentions[{index}] ({mention.surface})?",
            "criteria": rotate_choice(types, f"{salt}:type:{index}"),
        }
        questions[f"verify:{index}"] = {
            "type": "noul",
            "instructions": (
                f"Does this text assert that mentions[{index}] ({mention.surface}) "
                f"is a {mention.type}?"
            ),
        }
    for rel_index, relation in enumerate(proposal.relations):
        if relation.head not in indexes and relation.tail not in indexes:
            continue
        questions[f"rel:{rel_index}"] = {
            "type": "choice",
            "instructions": (
                f"Which relation does the text state between mentions[{relation.head}] "
                f"and mentions[{relation.tail}]?"
            ),
            "criteria": rotate_choice(relations, f"{salt}:rel:{rel_index}"),
        }
        questions[f"mod:{rel_index}"] = {
            "type": "choice",
            "instructions": "Is that relation asserted as fact, or only speculative?",
            "criteria": rotate_choice(
                {"asserted": "stated as fact", "speculative": "hedged, possible, or predicted"},
                f"{salt}:mod:{rel_index}",
            ),
        }
    for event_index, event in enumerate(proposal.events):
        questions[f"event:{event_index}"] = {
            "type": "choice",
            "instructions": f"What event type fits the trigger '{event.trigger}'?",
            "criteria": rotate_choice(events, f"{salt}:event:{event_index}"),
        }
        labels = {str(index): proposal.mentions[index].surface for index in indexes}
        labels["none"] = "no mention plays this role"
        for role, _mention_index in event.roles:
            questions[f"role:{event_index}:{role}"] = {
                "type": "choice",
                "instructions": f"Which mention fills the {role} role?",
                "criteria": rotate_choice(labels, f"{salt}:role:{event_index}:{role}"),
            }
    return questions


def _marked(chunk: str, proposal: Proposal, indexes: list[int]) -> str:
    extras = []
    for index in indexes:
        mention = proposal.mentions[index]
        extras.append(f"mentions[{index}] = {mention.surface} ({mention.type})")
    return chunk + ("\n" + "\n".join(extras) if extras else "")


def _apply(answers: dict, proposal: Proposal, indexes: list[int], ontology: Ontology) -> None:
    allowed = set(ontology.entity_ids())
    rel_allowed = set(ontology.relation_ids())
    event_allowed = set(ontology.event_ids())
    for index in indexes:
        mention = proposal.mentions[index]
        choice = answers.get(f"type:{index}") or {}
        noul = answers.get(f"verify:{index}") or {}
        picked = choice.get("choice")
        if picked in allowed:
            mention.type = str(picked)
        elif picked == "not_an_entity":
            mention.type = "not_an_entity"
        if isinstance(noul.get("noul"), (int, float)):
            setattr(mention, "p_jev", float(noul["noul"]))
        mention_answers = {"type": choice, "verify": noul}
        setattr(mention, "answers", mention_answers)
    for rel_index, relation in enumerate(proposal.relations):
        choice = answers.get(f"rel:{rel_index}") or {}
        modality = answers.get(f"mod:{rel_index}") or {}
        if choice.get("choice") in rel_allowed:
            relation.type = str(choice["choice"])
        if modality.get("choice") in {"asserted", "speculative"}:
            relation.modality = str(modality["choice"])
        if isinstance(choice.get("confidence"), (int, float)):
            setattr(relation, "p_jev", float(choice["confidence"]))
        setattr(relation, "answers", {"relation": choice, "modality": modality})
    for event_index, event in enumerate(proposal.events):
        choice = answers.get(f"event:{event_index}") or {}
        if choice.get("choice") in event_allowed:
            event.type = str(choice["choice"])
            event.cameo = ontology.cameo_for(event.type)
        if isinstance(choice.get("confidence"), (int, float)):
            setattr(event, "p_jev", float(choice["confidence"]))
        setattr(event, "answers", {"event": choice})
        new_roles = []
        for role, mention_index in event.roles:
            picked = (answers.get(f"role:{event_index}:{role}") or {}).get("choice")
            if picked == "none":
                continue
            if isinstance(picked, str) and picked.isdigit():
                new_roles.append((role, int(picked)))
            else:
                new_roles.append((role, mention_index))
        event.roles = new_roles


def dump_answers(row) -> str:
    payload = getattr(row, "answers", None)
    if not payload:
        return "{}"
    return json.dumps(payload)
