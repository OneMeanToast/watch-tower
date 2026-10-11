"""Local candidate proposer.

The stub is deterministic and is what tests use. spaCy, GLiNER2 (or GLiNER)
and fastcoref are optional extras. A deep mode is a second proposer interface
and is off until Weston asks for a local LLM.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from wt.extract_graph.ground import normalise_dates, normalise_numbers
from wt.extract_graph.ontology import Ontology, default_ontology

log = logging.getLogger("wt.extract_graph.propose")

_CVE = re.compile(r"\bCVE-\d{4}-\d{4,7}\b")
_SENT = re.compile(r"[^.!?\n]+(?:[.!?]+|$)", re.S)
EXTRACTOR_STUB = "stub"
EXTRACTOR_VERSION = "1"


@dataclass
class MentionCand:
    start: int
    end: int
    surface: str
    type: str
    p_local: float
    extractor: str = EXTRACTOR_STUB


@dataclass
class RelationCand:
    head: int
    tail: int
    type: str
    start: int
    end: int
    p_local: float
    modality: str = "asserted"


@dataclass
class EventCand:
    start: int
    end: int
    trigger: str
    type: str
    cameo: str
    p_local: float
    roles: list[tuple[str, int]] = field(default_factory=list)


@dataclass
class Proposal:
    mentions: list[MentionCand] = field(default_factory=list)
    relations: list[RelationCand] = field(default_factory=list)
    events: list[EventCand] = field(default_factory=list)
    clusters: list[list[int]] = field(default_factory=list)


class Proposer:
    """Interface. Swap the stub for GLiNER2 without changing the writer."""

    name = "proposer"

    def propose(self, text: str) -> Proposal:
        raise NotImplementedError


class DeepProposer:
    """Local-LLM second pass. Disabled until that decision is signed off."""

    name = "off"

    def propose(self, text: str) -> Proposal:
        return Proposal()


class DisabledDeepMode(DeepProposer):
    pass


class StubProposer(Proposer):
    """Gazetteer, CVE regex, dates, and a few closed relation patterns."""

    name = "stub"

    def __init__(self, ontology: Ontology | None = None):
        self.ontology = ontology or default_ontology()

    def propose(self, text: str) -> Proposal:
        mentions = _spans(text or "", self.ontology)
        relations = _relations(text or "", mentions)
        events = _events(text or "", mentions, self.ontology)
        return Proposal(
            mentions=mentions,
            relations=relations,
            events=events,
            clusters=_clusters(mentions),
        )


class ModelProposer(Proposer):
    """spaCy sentences, then GLiNER2 or GLiNER, then fastcoref. Optional extra."""

    name = "model"

    def __init__(self, ontology: Ontology | None = None):
        self.ontology = ontology or default_ontology()
        self._ner = _load_ner()
        self._coref = _load_coref()

    def propose(self, text: str) -> Proposal:
        labels = self.ontology.entity_ids()
        mentions = self._ner(text or "", labels)
        if not mentions:
            mentions = _spans(text or "", self.ontology)
        clusters = self._coref(text or "", mentions) if self._coref else _clusters(mentions)
        return Proposal(
            mentions=mentions,
            relations=_relations(text or "", mentions),
            events=_events(text or "", mentions, self.ontology),
            clusters=clusters,
        )


def build_proposer(kind: str = "stub", ontology: Ontology | None = None) -> Proposer:
    requested = (kind or "stub").strip().lower()
    if requested == "stub":
        return StubProposer(ontology)
    if requested == "model":
        return ModelProposer(ontology)
    if requested == "auto":
        try:
            return ModelProposer(ontology)
        except Exception as exc:
            log.info("model proposer unavailable (%s); using the stub", exc)
            return StubProposer(ontology)
    raise ValueError(f"unknown proposer {kind!r}")


def sentences(text: str) -> list[tuple[int, int, str]]:
    """spaCy sentence spans when the extra is installed, otherwise a regex."""
    parsed = _spacy_sentences(text)
    if parsed:
        return parsed
    rows = []
    for match in _SENT.finditer(text or ""):
        raw = match.group(0)
        if not raw.strip():
            continue
        rows.append((match.start(), match.end(), raw))
    if not rows and text:
        rows.append((0, len(text), text))
    return rows


def windows(text: str, size: int = 5, overlap: int = 1) -> list[tuple[int, int, str]]:
    rows = sentences(text)
    if not rows:
        return []
    step = max(1, size - overlap)
    chunks = []
    for index in range(0, len(rows), step):
        group = rows[index : index + size]
        if not group:
            break
        start = group[0][0]
        end = group[-1][1]
        chunks.append((start, end, text[start:end]))
        if index + size >= len(rows):
            break
    return chunks


def _spans(text: str, ontology: Ontology) -> list[MentionCand]:
    found: list[MentionCand] = []
    occupied: list[tuple[int, int]] = []

    def take(start: int, end: int, surface: str, type_id: str, p_local: float) -> None:
        if any(not (end <= left or start >= right) for left, right in occupied):
            return
        if text[start:end] != surface:
            return
        occupied.append((start, end))
        found.append(MentionCand(start, end, surface, type_id, p_local))

    gazetteer = sorted(ontology.gazetteer, key=lambda row: len(row[0]), reverse=True)
    for surface, type_id in gazetteer:
        pattern = re.compile(rf"(?<!\w){re.escape(surface)}(?!\w)")
        for match in pattern.finditer(text):
            take(match.start(), match.end(), match.group(0), type_id, 0.66)
    for match in _CVE.finditer(text):
        take(match.start(), match.end(), match.group(0), "Vulnerability", 0.99)
    for row in normalise_dates(text):
        take(row["start"], row["end"], row["surface"], "Date", 0.9)
    for row in normalise_numbers(text):
        take(row["start"], row["end"], row["surface"], "Quantity", 0.9)
    found.sort(key=lambda row: row.start)
    return found


def _relations(text: str, mentions: list[MentionCand]) -> list[RelationCand]:
    rows = []
    _cue_pair(rows, text, mentions, "affects", "Vulnerability", "SoftwareProduct", "affects", "asserted", 0.7)
    _cue_pair(rows, text, mentions, "built by", "SoftwareProduct", "Organization", "manufacturer", "asserted", 0.7)
    _cue_pair(rows, text, mentions, "may adopt", "Company", "AIModel", "adopts", "speculative", 0.45)
    _cue_pair(rows, text, mentions, "may adopt", "Organization", "AIModel", "adopts", "speculative", 0.45)
    return rows


def _cue_pair(rows, text, mentions, cue, head_type, tail_type, rel, modality, p_local) -> None:
    heads = [index for index, mention in enumerate(mentions) if mention.type == head_type]
    tails = [index for index, mention in enumerate(mentions) if mention.type == tail_type]
    for head in heads:
        for tail in tails:
            left = min(mentions[head].start, mentions[tail].start)
            right = max(mentions[head].end, mentions[tail].end)
            if right - left > 280:
                continue
            window = text[left:right].lower()
            if cue not in window:
                continue
            rows.append(RelationCand(head, tail, rel, left, right, p_local, modality))
            return


def _pair(rows, mentions, inside, head_type, tail_type, rel, start, end, modality, p_local) -> None:
    heads = [index for index in inside if mentions[index].type == head_type]
    tails = [index for index in inside if mentions[index].type == tail_type]
    if not heads or not tails:
        return
    rows.append(
        RelationCand(heads[0], tails[0], rel, start, end, p_local, modality)
    )


def _events(text: str, mentions: list[MentionCand], ontology: Ontology) -> list[EventCand]:
    rows = []
    for start, end, sentence in sentences(text):
        inside = [index for index, mention in enumerate(mentions) if start <= mention.start < end]
        low = sentence.lower()
        if " added " in f" {low} ":
            event = _event(mentions, inside, start, end, "added", "PolicyAction", ontology, "PublicBody", "Vulnerability")
            if event:
                rows.append(event)
        if "released" in low:
            event = _event(mentions, inside, start, end, "released", "ModelRelease", ontology, "Organization", "AIModel")
            if event:
                rows.append(event)
    return rows


def _event(mentions, inside, start, end, trigger, type_id, ontology, actor_type, target_type) -> EventCand | None:
    actors = [index for index in inside if mentions[index].type == actor_type]
    targets = [index for index in inside if mentions[index].type == target_type]
    if not actors and not targets:
        return None
    roles = []
    if actors:
        roles.append(("actor", actors[0]))
    if targets:
        roles.append(("target", targets[0]))
    trigger_at = mentions[actors[0]].start if actors else start
    trigger_end = mentions[actors[0]].end if actors else start + len(trigger)
    return EventCand(
        start=trigger_at,
        end=trigger_end,
        trigger=trigger,
        type=type_id,
        cameo=ontology.cameo_for(type_id),
        p_local=0.6,
        roles=roles,
    )


def _clusters(mentions: list[MentionCand]) -> list[list[int]]:
    groups: dict[str, list[int]] = {}
    for index, mention in enumerate(mentions):
        groups.setdefault(mention.surface.casefold(), []).append(index)
    return list(groups.values())


def _spacy_sentences(text: str) -> list[tuple[int, int, str]]:
    try:
        import spacy
    except ImportError:
        return []
    try:
        nlp = spacy.blank("en")
        if "sentencizer" not in nlp.pipe_names:
            nlp.add_pipe("sentencizer")
        doc = nlp(text or "")
    except Exception as exc:
        log.info("spaCy sentencizer skipped (%s)", exc)
        return []
    return [(sent.start_char, sent.end_char, sent.text) for sent in doc.sents if sent.text.strip()]


def _load_ner():
    try:
        import gliner2
    except ImportError:
        gliner2 = None
    if gliner2 is not None:
        model = _gliner2_model(gliner2)
        return lambda text, labels: _from_gliner2(model, text, labels)
    try:
        from gliner import GLiNER
    except ImportError as exc:
        raise ImportError("neither gliner2 nor gliner is installed") from exc
    model = GLiNER.from_pretrained("urchade/gliner_small-v2.1")

    def predict(text: str, labels: list[str]) -> list[MentionCand]:
        raw = model.predict_entities(text, labels, threshold=0.3)
        rows = []
        for row in raw or []:
            start = int(row["start"])
            end = int(row["end"])
            surface = text[start:end]
            rows.append(
                MentionCand(start, end, surface, str(row.get("label") or "Organization"), float(row.get("score") or 0.5), "gliner")
            )
        return rows

    return predict


def _gliner2_model(module):
    cls = getattr(module, "GLiNER2", None) or getattr(module, "Extractor", None)
    if cls is None:
        raise ImportError("gliner2 has no extractor class")
    return cls.from_pretrained("fastino/gliner2-base-v1")


def _from_gliner2(model, text: str, labels: list[str]) -> list[MentionCand]:
    schema = {"entities": {label: label for label in labels}}
    if hasattr(model, "extract_entities"):
        raw = model.extract_entities(text, labels, include_spans=True)
    elif hasattr(model, "extract"):
        raw = model.extract(text, schema, include_spans=True)
    else:
        raw = model.predict_entities(text, labels)
    return _mentions_from_raw(text, raw)


def _mentions_from_raw(text: str, raw) -> list[MentionCand]:
    rows = []
    items = raw
    if isinstance(raw, dict):
        items = raw.get("entities") or raw.get("mentions") or []
        if isinstance(items, dict):
            flat = []
            for label, values in items.items():
                for value in values or []:
                    if isinstance(value, dict):
                        value = {**value, "label": value.get("label") or label}
                        flat.append(value)
                    else:
                        flat.append({"text": value, "label": label})
            items = flat
    for row in items or []:
        if not isinstance(row, dict):
            continue
        start = row.get("start")
        end = row.get("end")
        if start is None or end is None:
            continue
        start, end = int(start), int(end)
        rows.append(
            MentionCand(
                start,
                end,
                text[start:end],
                str(row.get("label") or row.get("type") or "Organization"),
                float(row.get("score") or row.get("probability") or 0.5),
                "gliner2",
            )
        )
    return rows


def _load_coref():
    try:
        from fastcoref import FCoref
    except ImportError:
        return None
    model = FCoref()

    def cluster(text: str, mentions: list[MentionCand]) -> list[list[int]]:
        try:
            preds = model.predict(texts=[text])
            spans = preds[0].get_clusters(as_strings=False)
        except Exception as exc:
            log.info("fastcoref skipped (%s)", exc)
            return _clusters(mentions)
        groups: list[list[int]] = []
        used = set()
        for cluster_spans in spans or []:
            indexes = []
            for span in cluster_spans:
                if not isinstance(span, (list, tuple)) or len(span) < 2:
                    continue
                start, end = int(span[0]), int(span[1])
                for index, mention in enumerate(mentions):
                    if index in used:
                        continue
                    if mention.start >= start and mention.end <= end:
                        indexes.append(index)
                        used.add(index)
                        break
            if indexes:
                groups.append(indexes)
        for index in range(len(mentions)):
            if index not in used:
                groups.append([index])
        return groups

    return cluster
