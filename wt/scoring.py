"""Lexical hit/severity scores used when no decision model is available.

The mapping is a fixed heuristic, not a calibrated probability. Re-fit thresholds
on your own labels before trusting them, and prefer a System One backend (Kev,
Von, TinyJev) when one is running.
"""

from __future__ import annotations

import math

# Weights were chosen so the shipped demo fixtures separate at the example
# indicator thresholds (push at hit >= 0.85 and severity >= 2). They are not
# a claim about real-world precision.
COS_W = 0.48
KW_W = 0.50
POS_W = 0.42
NEG_W = 0.55
BIAS = 0.38
STEEP = 8.0

SEV_COS_W = 0.35
SEV_KW_W = 0.45
SEV_POS_W = 0.40
SEV_NEG_W = 0.30


def sigmoid(value: float) -> float:
    if value >= 0:
        z = math.exp(-value)
        return 1.0 / (1.0 + z)
    z = math.exp(value)
    return z / (1.0 + z)


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def lexical_p_hit(
    *,
    cosine: float,
    keyword_hit: bool,
    positive_overlap: float,
    negative_overlap: float,
) -> float:
    raw = (
        COS_W * max(cosine, 0.0)
        + KW_W * (1.0 if keyword_hit else 0.0)
        + POS_W * positive_overlap
        - NEG_W * negative_overlap
    )
    return clamp(sigmoid(STEEP * (raw - BIAS)), 0.01, 0.99)


def lexical_severity(
    *,
    cosine: float,
    keyword_hit: bool,
    positive_overlap: float,
    negative_overlap: float,
    n_levels: int,
) -> float:
    levels = max(1, n_levels)
    evidence = clamp(
        SEV_COS_W * max(cosine, 0.0)
        + SEV_KW_W * (1.0 if keyword_hit else 0.0)
        + SEV_POS_W * positive_overlap
        - SEV_NEG_W * negative_overlap,
        0.0,
        1.0,
    )
    return evidence * (levels - 1)


def noul_confidence(p_hit: float) -> float:
    return clamp(abs(p_hit - 0.5) * 2.0, 0.0, 1.0)


def choice_confidence(probabilities: dict[str, float]) -> float:
    if not probabilities:
        return 0.0
    k = len(probabilities)
    if k == 1:
        return 1.0
    p_max = max(probabilities.values())
    return clamp((p_max - 1.0 / k) / (1.0 - 1.0 / k), 0.0, 1.0)
