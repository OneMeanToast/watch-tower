"""Load the type list. Callers depend on this object, not on a hard-coded schema."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from wt.settings import project_root


@dataclass
class TypeSpec:
    id: str
    group: str = ""
    ftm: str = ""
    stix: str = ""
    cameo: str = ""


@dataclass
class HighValueRule:
    daily_cap: int = 50
    when: tuple[str, ...] = ("pushed_alert", "starred", "ripped", "paper", "advisory")
    paper_sources: tuple[str, ...] = ("arxiv", "openalex")
    advisory_sources: tuple[str, ...] = ("cisa_kev", "nvd")


@dataclass
class Ontology:
    version: int
    entities: list[TypeSpec]
    relations: list[TypeSpec]
    events: list[TypeSpec]
    gazetteer: list[tuple[str, str]]
    high_value: HighValueRule = field(default_factory=HighValueRule)

    def entity_ids(self) -> list[str]:
        return [row.id for row in self.entities]

    def relation_ids(self) -> list[str]:
        return [row.id for row in self.relations]

    def event_ids(self) -> list[str]:
        return [row.id for row in self.events]

    def cameo_for(self, event_type: str) -> str:
        for row in self.events:
            if row.id == event_type:
                return row.cameo
        return ""


def load_ontology(path: Path | None = None) -> Ontology:
    file = path or (project_root() / "config" / "ontology.yaml")
    raw = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
    rule = raw.get("high_value") or {}
    return Ontology(
        version=int(raw.get("version") or 1),
        entities=[_spec(row) for row in raw.get("entity_types") or []],
        relations=[_spec(row) for row in raw.get("relation_types") or []],
        events=[_spec(row) for row in raw.get("event_types") or []],
        gazetteer=[
            (str(row["surface"]), str(row["type"]))
            for row in raw.get("stub_gazetteer") or []
            if row.get("surface") and row.get("type")
        ],
        high_value=HighValueRule(
            daily_cap=int(rule.get("daily_cap") or 50),
            when=tuple(rule.get("when") or ["pushed_alert", "starred", "ripped", "paper", "advisory"]),
            paper_sources=tuple(rule.get("paper_sources") or ["arxiv", "openalex"]),
            advisory_sources=tuple(rule.get("advisory_sources") or ["cisa_kev", "nvd"]),
        ),
    )


def _spec(row: dict) -> TypeSpec:
    return TypeSpec(
        id=str(row.get("id") or ""),
        group=str(row.get("group") or ""),
        ftm=str(row.get("ftm") or ""),
        stix=str(row.get("stix") or ""),
        cameo=str(row.get("cameo") or ""),
    )


@lru_cache(maxsize=1)
def default_ontology() -> Ontology:
    return load_ontology()
