"""Injection gate in front of the decision model.

The heuristic runs offline and is what demo mode and the canary tests use.
``WT_INJECTION_GUARD=deberta`` also consults
``protectai/deberta-v3-base-prompt-injection-v2`` (Apache-2.0, about 184M
parameters) when that extra is installed. A flagged item is not sent to Kev
and cannot produce a push.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from wt.safety import injection_suspected

log = logging.getLogger("wt.guard")

DEBERTA_MODEL = "protectai/deberta-v3-base-prompt-injection-v2"
_deberta = None


@dataclass(frozen=True)
class GuardVerdict:
    flagged: bool
    reason: str
    backend: str


def classify_text(text: str, *, mode: str = "heuristic") -> GuardVerdict:
    if injection_suspected(text):
        return GuardVerdict(True, "instruction-shaped text in the visible article", "heuristic")
    if mode == "deberta":
        flagged, reason = _deberta_flagged(text)
        if flagged:
            return GuardVerdict(True, reason, "deberta")
        return GuardVerdict(False, "", "deberta" if reason != "unavailable" else "heuristic")
    return GuardVerdict(False, "", "heuristic")


def _deberta_flagged(text: str) -> tuple[bool, str]:
    global _deberta
    if _deberta is False:
        return False, "unavailable"
    try:
        if _deberta is None:
            from transformers import pipeline

            _deberta = pipeline(
                "text-classification",
                model=DEBERTA_MODEL,
                truncation=True,
                max_length=512,
            )
        rows = _deberta((text or "")[:4000])
        row = rows[0] if isinstance(rows, list) else rows
        label = str(row.get("label", "")).upper()
        score = float(row.get("score", 0.0))
        if "INJECTION" in label and score >= 0.5:
            return True, f"deberta {label} {score:.2f}"
        return False, ""
    except Exception as exc:
        log.warning("deberta injection guard unavailable (%s); using the heuristic only", exc)
        _deberta = False
        return False, "unavailable"
