"""Stage-one gate: source filter, keywords/regex, and cosine against indicator anchors."""

from __future__ import annotations

import re

from wt.models import Candidate, Indicator, Item
from wt.text import term_in


def source_allowed(indicator: Indicator, item: Item) -> bool:
    if not indicator.sources:
        return True
    keys = {item.source}
    if item.feed_id:
        keys.add(f"{item.source}:{item.feed_id}")
    for group in item.groups:
        if group:
            keys.add(f"{item.source}:{group}")
    return bool(keys & set(indicator.sources))


def _any_terms(indicator: Indicator) -> list[str]:
    return [*indicator.prefilter.keywords_any, *indicator.prefilter.entities_any]


def matched_terms(text: str, indicator: Indicator) -> list[str]:
    hits = [term for term in _any_terms(indicator) if term_in(text, term)]
    for pattern in indicator.prefilter.regex_any:
        if re.search(pattern, text, flags=re.IGNORECASE):
            hits.append(f"/{pattern}/")
    return hits


def block_reason(text: str, indicator: Indicator) -> str | None:
    missing = [term for term in indicator.prefilter.keywords_all if not term_in(text, term)]
    if missing:
        return "missing keywords_all: " + ", ".join(missing)
    blocked = [term for term in indicator.prefilter.keywords_none if term_in(text, term)]
    if blocked:
        return "keywords_none matched: " + ", ".join(blocked)
    return None


def select_candidates(
    item: Item,
    indicators: list[Indicator],
    cosines: dict[str, float],
    *,
    top_k: int,
) -> list[Candidate]:
    text = item.document()
    found: list[Candidate] = []
    for indicator in indicators:
        if not source_allowed(indicator, item):
            continue
        if block_reason(text, indicator) is not None:
            continue
        terms = matched_terms(text, indicator)
        score = float(cosines.get(indicator.id, 0.0))
        keyword_hit = bool(terms)
        if score < indicator.prefilter.min_cosine and not keyword_hit:
            continue
        found.append(
            Candidate(
                indicator=indicator,
                cosine=score,
                keyword_hit=keyword_hit,
                matched_terms=terms,
            )
        )
    # Keyword hits stay in the top-K even when hashing cosine is modest.
    found.sort(
        key=lambda cand: (max(cand.cosine, 0.5) if cand.keyword_hit else cand.cosine),
        reverse=True,
    )
    if top_k > 0:
        return found[:top_k]
    return found
