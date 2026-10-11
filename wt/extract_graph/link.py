"""Entity linking. Wikidata search is optional and stubbed. Jev would choose among candidates."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class LinkCandidate:
    qid: str
    label: str
    description: str


class EntityLinker:
    def candidates(self, surface: str, schema: str) -> list[LinkCandidate]:
        raise NotImplementedError


class StubLinker(EntityLinker):
    """No network. A later linker can call wbsearchentities and hand the list to Jev."""

    def __init__(self, table: dict[str, list[LinkCandidate]] | None = None):
        self.table = table or {}

    def candidates(self, surface: str, schema: str) -> list[LinkCandidate]:
        return list(self.table.get(surface) or self.table.get(surface.casefold()) or [])
