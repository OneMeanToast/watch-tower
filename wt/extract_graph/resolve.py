"""Pair judgments. Merges are reversible: merged_into plus an undone flag."""

from __future__ import annotations

from dataclasses import dataclass

_ORG = {"Organization", "Company", "PublicBody"}
_SUFFIXES = {"inc", "inc.", "ltd", "ltd.", "llc", "corp", "corp.", "corporation", "co", "co."}


@dataclass
class Judgment:
    left_id: str
    right_id: str
    judgment: str  # yes / no / unsure
    source: str
    p: float | None = None


class Resolver:
    def judge(self, entities: list[dict]) -> list[Judgment]:
        raise NotImplementedError


class SuffixResolver(Resolver):
    """Org names that differ only by a company suffix are unsure, not merged."""

    def judge(self, entities: list[dict]) -> list[Judgment]:
        rows = []
        live = [row for row in entities if not row.get("merged_into")]
        for index, left in enumerate(live):
            for right in live[index + 1 :]:
                if _company_suffix(left, right):
                    rows.append(
                        Judgment(left["id"], right["id"], "unsure", "rule", 0.42)
                    )
        return rows


def _company_suffix(left: dict, right: dict) -> bool:
    if left.get("schema") not in _ORG or right.get("schema") not in _ORG:
        return False
    a = str(left.get("caption") or "")
    b = str(right.get("caption") or "")
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    if not short or not long.lower().startswith(short.lower()):
        return False
    rest = long[len(short) :].strip(" .").lower()
    return rest in _SUFFIXES
