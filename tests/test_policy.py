from datetime import datetime, timedelta, timezone

import pytest

from wt.models import Indicator
from wt.policy import (
    PolicyError,
    PriorAlert,
    compile_expr,
    decide_action,
    parse_duration,
    suppressed_by_cooldown,
)
from wt.scoring import lexical_p_hit, lexical_severity


def _indicator(alert_if: str, digest_if: str | None = "hit >= 0.5") -> Indicator:
    return Indicator.model_validate(
        {
            "id": "demo",
            "name": "Demo",
            "description": "Demo indicator",
            "questions": {
                "hit": {"type": "noul", "instructions": "Is this a hit?"},
                "severity": {
                    "type": "score",
                    "instructions": "How big?",
                    "criteria": ["trivial", "minor", "notable", "major", "field-changing"],
                },
            },
            "policy": {"alert_if": alert_if, "digest_if": digest_if, "cooldown": "6h"},
        }
    )


def test_threshold_boundaries():
    indicator = _indicator("hit >= 0.85 and severity >= 2")
    assert decide_action(indicator, p_hit=0.85, severity=2, cosine_value=0.1, confidence=0.5)[0] == "push"
    assert decide_action(indicator, p_hit=0.849, severity=2, cosine_value=0.9, confidence=1)[0] == "digest"
    assert decide_action(indicator, p_hit=0.9, severity=1.99, cosine_value=0.9, confidence=1)[0] == "digest"
    assert decide_action(indicator, p_hit=0.49, severity=4, cosine_value=0.9, confidence=1)[0] == "drop"


def test_boolean_expression():
    indicator = _indicator("(hit >= 0.8 or cosine >= 0.9) and severity >= 1", digest_if=None)
    assert decide_action(indicator, p_hit=0.1, severity=1, cosine_value=0.9, confidence=0)[0] == "push"
    assert decide_action(indicator, p_hit=0.8, severity=0.5, cosine_value=0.95, confidence=0)[0] == "drop"
    assert decide_action(indicator, p_hit=0.2, severity=3, cosine_value=0.2, confidence=0)[0] == "drop"


def test_not_and_rejects_unknown_names():
    env = {"hit": 0.1, "severity": 0, "cosine": 0, "confidence": 0}
    # `not` applies to the comparison that follows it.
    assert compile_expr("not hit >= 0.5")(env)
    assert not compile_expr("not (hit < 0.2)")(env)
    with pytest.raises(PolicyError):
        compile_expr("title == 1")
    with pytest.raises(PolicyError):
        compile_expr("hit >= 0.5; import os")


def test_duration():
    assert parse_duration("6h") == timedelta(hours=6)
    assert parse_duration("30m") == timedelta(minutes=30)
    with pytest.raises(PolicyError):
        parse_duration("6 hours")


def test_partial_overlap_with_a_keyword_still_clears():
    p_hit = lexical_p_hit(cosine=0.25, keyword_hit=True, positive_overlap=0.32, negative_overlap=0.15)
    severity = lexical_severity(
        cosine=0.25,
        keyword_hit=True,
        positive_overlap=0.32,
        negative_overlap=0.15,
        n_levels=5,
    )
    assert p_hit >= 0.85
    assert severity >= 2


def test_lexical_scores_clear_the_example_threshold():
    p_hit = lexical_p_hit(cosine=0.7, keyword_hit=True, positive_overlap=0.8, negative_overlap=0.1)
    severity = lexical_severity(
        cosine=0.7,
        keyword_hit=True,
        positive_overlap=0.8,
        negative_overlap=0.1,
        n_levels=5,
    )
    assert p_hit >= 0.85
    assert severity >= 2

    blocked = lexical_p_hit(
        cosine=0.45, keyword_hit=True, positive_overlap=0.25, negative_overlap=0.85
    )
    assert blocked < 0.5


def test_cooldown_suppresses_near_duplicates_only():
    now = datetime(2026, 10, 7, tzinfo=timezone.utc)
    prior = PriorAlert(
        url_hash="abc",
        simhash=0b1111,
        vector=[1.0, 0.0, 0.0],
        created_at=now - timedelta(hours=1),
    )
    assert suppressed_by_cooldown(
        [prior],
        url_hash="other",
        simhash=0,
        vector=[1.0, 0.0, 0.0],
        now=now,
        cooldown=timedelta(hours=6),
    )
    far = (1 << 64) - 1
    assert (far ^ prior.simhash).bit_count() > 8
    assert not suppressed_by_cooldown(
        [prior],
        url_hash="other",
        simhash=far,
        vector=[0.0, 1.0, 0.0],
        now=now,
        cooldown=timedelta(hours=6),
    )
    assert not suppressed_by_cooldown(
        [prior],
        url_hash="other",
        simhash=0,
        vector=[1.0, 0.0, 0.0],
        now=now,
        cooldown=timedelta(minutes=30),
    )
