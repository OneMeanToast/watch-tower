"""Load indicator YAML files and reject ones the policy language cannot run."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from wt.models import Indicator
from wt.policy import PolicyError, validate_indicator_policy


class IndicatorError(ValueError):
    pass


def load_indicators(directory: str | Path) -> list[Indicator]:
    path = Path(directory)
    if not path.is_dir():
        raise IndicatorError(f"indicators directory not found: {path}")
    files = sorted(path.glob("*.yaml")) + sorted(path.glob("*.yml"))
    if not files:
        raise IndicatorError(f"no indicator YAML files in {path}")
    indicators: list[Indicator] = []
    seen: set[str] = set()
    for file in files:
        try:
            data = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
            indicator = Indicator.model_validate(data)
            validate_indicator_policy(indicator)
            _validate_questions(indicator, file.name)
            _compile_regexes(indicator, file.name)
        except (PolicyError, IndicatorError):
            raise
        except Exception as exc:
            raise IndicatorError(f"{file.name}: {exc}") from exc
        if indicator.id in seen:
            raise IndicatorError(f"duplicate indicator id {indicator.id}")
        seen.add(indicator.id)
        if not indicator.noul_ids():
            raise IndicatorError(f"{file.name}: need at least one noul question (usually id 'hit')")
        indicators.append(indicator)
    return indicators


def _validate_questions(indicator: Indicator, filename: str) -> None:
    for qid, question in indicator.questions.items():
        if question.type == "score" and not isinstance(question.criteria, list):
            raise IndicatorError(f"{filename}: score question {qid} needs a list of criteria")
        if question.type == "choice" and not isinstance(question.criteria, (list, dict)):
            raise IndicatorError(f"{filename}: choice question {qid} needs criteria")
        if question.type == "score" and isinstance(question.criteria, list) and len(question.criteria) < 2:
            raise IndicatorError(f"{filename}: score question {qid} needs at least two levels")


def _compile_regexes(indicator: Indicator, filename: str) -> None:
    for pattern in indicator.prefilter.regex_any:
        try:
            re.compile(pattern)
        except re.error as exc:
            raise IndicatorError(f"{filename}: bad regex {pattern!r}: {exc}") from exc
