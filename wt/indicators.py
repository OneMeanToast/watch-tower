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


def indicator_path(directory: str | Path, indicator_id: str) -> Path:
    path = Path(directory)
    for file in sorted(path.glob("*.yaml")) + sorted(path.glob("*.yml")):
        data = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
        if isinstance(data, dict) and data.get("id") == indicator_id:
            return file
    raise IndicatorError(f"no indicator file for {indicator_id}")


def update_indicator(directory: str | Path, indicator_id: str, patch: dict) -> Indicator:
    """Write a small set of fields back to the indicator YAML and re-validate."""
    file = indicator_path(directory, indicator_id)
    data = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise IndicatorError(f"{file.name} is not a mapping")
    prefilter = dict(data.get("prefilter") or {})
    policy = dict(data.get("policy") or {})
    if "name" in patch:
        data["name"] = str(patch["name"]).strip()
    if "description" in patch:
        data["description"] = str(patch["description"]).strip()
    if "keywords_any" in patch:
        prefilter["keywords_any"] = [str(term).strip() for term in patch["keywords_any"] if str(term).strip()]
    if "min_cosine" in patch:
        prefilter["min_cosine"] = float(patch["min_cosine"])
    if "alert_if" in patch:
        policy["alert_if"] = str(patch["alert_if"]).strip()
    if "digest_if" in patch:
        raw = patch["digest_if"]
        policy["digest_if"] = None if raw in (None, "") else str(raw).strip()
    if "cooldown" in patch:
        policy["cooldown"] = str(patch["cooldown"]).strip()
    if "push_min_confidence" in patch:
        policy["push_min_confidence"] = float(patch["push_min_confidence"])
    if "uncertain_min" in patch:
        policy["uncertain_min"] = float(patch["uncertain_min"])
    data["prefilter"] = prefilter
    data["policy"] = policy
    try:
        indicator = Indicator.model_validate(data)
        validate_indicator_policy(indicator)
        _validate_questions(indicator, file.name)
        _compile_regexes(indicator, file.name)
    except (PolicyError, IndicatorError):
        raise
    except Exception as exc:
        raise IndicatorError(str(exc)) from exc
    file.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return indicator


def _compile_regexes(indicator: Indicator, filename: str) -> None:
    for pattern in indicator.prefilter.regex_any:
        try:
            re.compile(pattern)
        except re.error as exc:
            raise IndicatorError(f"{filename}: bad regex {pattern!r}: {exc}") from exc
