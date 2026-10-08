"""Group near-duplicate stories into one event.

Exact copies are already removed by the ingest dedupe. What remains can still
be the same announcement from a second outlet. A cheap check (title overlap,
simhash, or cosine) decides whether to ask the decider "same event?". The
lexical decider answers that question from the title overlap itself, so demo
mode stays offline. A later item in the same event does not push again for
the same indicator.
"""

from __future__ import annotations

import logging
import re

from wt.embed import cosine
from wt.models import Item
from wt.policy import hamming
from wt.text import STOPWORDS

log = logging.getLogger("wt.events")

# Hashing cosine between the fixture JEPA release and its paraphrase is ~0.25
# and the titles barely overlap, so these bars do not merge that pair.
# A wire rewrite that keeps the headline does merge.
TITLE_JACCARD = 0.45
EVENT_COSINE = 0.72
EVENT_HAMMING = 8
_WORD = re.compile(r"[a-z0-9][a-z0-9+\-\.]{1,}")


def title_jaccard(left: str, right: str) -> float:
    a = _title_tokens(left)
    b = _title_tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _title_tokens(title: str) -> set[str]:
    return {word for word in _WORD.findall((title or "").lower()) if word not in STOPWORDS}


def same_event_signals(
    *,
    title: str,
    other_title: str,
    simhash_value: int,
    other_simhash: int,
    vector: list[float],
    other_vector: list[float],
) -> bool:
    if title_jaccard(title, other_title) >= TITLE_JACCARD:
        return True
    if hamming(simhash_value, other_simhash) <= EVENT_HAMMING:
        return True
    if cosine(vector, other_vector) >= EVENT_COSINE:
        return True
    return False


def confirm_same_event(decider, item: Item, other_title: str, other_text: str) -> bool:
    """Ask a System One server when it implements ``same_event``. Otherwise trust the signals."""
    method = getattr(decider, "same_event", None)
    if method is None:
        return True
    try:
        return bool(method(item, other_title, other_text))
    except Exception as exc:
        log.warning("same-event question failed (%s); keeping the lexical grouping", exc)
        return True
