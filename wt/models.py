"""Shared records for items, indicators, and decisions."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class Item(BaseModel):
    id: str
    url: str
    source: str
    feed_id: str = ""
    ts: datetime
    title: str
    text: str = ""
    lang: str = "und"
    authors: list[str] = Field(default_factory=list)

    def document(self) -> str:
        parts = [self.title.strip(), self.text.strip()]
        if self.authors:
            parts.append("Authors: " + ", ".join(self.authors))
        return "\n".join(part for part in parts if part)


class Question(BaseModel):
    type: Literal["noul", "score", "choice"]
    instructions: str
    criteria: list[str] | dict[str, str | None] | None = None

    @field_validator("instructions")
    @classmethod
    def _instructions(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("question instructions must not be empty")
        return text


class PrefilterConfig(BaseModel):
    min_cosine: float = 0.32
    keywords_any: list[str] = Field(default_factory=list)
    keywords_all: list[str] = Field(default_factory=list)
    keywords_none: list[str] = Field(default_factory=list)
    regex_any: list[str] = Field(default_factory=list)
    # Literal phrase match. GLiNER is not bundled; see README.
    entities_any: list[str] = Field(default_factory=list)


class PolicyConfig(BaseModel):
    alert_if: str
    digest_if: str | None = None
    cooldown: str = "6h"


class AlertConfig(BaseModel):
    channels: list[str] = Field(default_factory=list)


class Indicator(BaseModel):
    id: str
    name: str
    description: str
    sources: list[str] = Field(default_factory=list)
    prefilter: PrefilterConfig = Field(default_factory=PrefilterConfig)
    positives: list[str] = Field(default_factory=list)
    negatives: list[str] = Field(default_factory=list)
    questions: dict[str, Question]
    policy: PolicyConfig
    alert: AlertConfig = Field(default_factory=AlertConfig)

    @field_validator("id")
    @classmethod
    def _id(cls, value: str) -> str:
        if not value.replace("-", "").isalnum() or " " in value:
            raise ValueError(f"indicator id {value!r} must be a slug")
        return value

    def anchor_text(self) -> str:
        lines = [self.name.strip(), self.description.strip(), *self.positives]
        return "\n".join(line for line in lines if line)

    def noul_ids(self) -> list[str]:
        return [qid for qid, question in self.questions.items() if question.type == "noul"]

    def question(self, qtype: str, preferred: str) -> tuple[str, Question] | None:
        if preferred in self.questions and self.questions[preferred].type == qtype:
            return preferred, self.questions[preferred]
        for qid, question in self.questions.items():
            if question.type == qtype:
                return qid, question
        return None


class Decision(BaseModel):
    p_hit: float
    severity: float
    category: str | None = None
    confidence: float
    backend: str
    answers: dict[str, Any] = Field(default_factory=dict)

    @field_validator("p_hit", "confidence")
    @classmethod
    def _unit(cls, value: float) -> float:
        return min(1.0, max(0.0, float(value)))


class Candidate(BaseModel):
    indicator: Indicator
    cosine: float
    keyword_hit: bool
    matched_terms: list[str] = Field(default_factory=list)


class MatchResult(BaseModel):
    match_id: str
    item_id: str
    indicator_id: str
    title: str
    url: str
    cosine: float
    keyword_hit: bool
    p_hit: float
    severity: float
    category: str | None
    confidence: float
    action: Literal["push", "digest", "drop", "suppressed"]
    reason: str
    backend: str


class CycleReport(BaseModel):
    items_seen: int
    items_new: int
    duplicates: int
    results: list[MatchResult] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    backend: str
    embedder: str
    vector_backend: str

    @property
    def pushes(self) -> list[MatchResult]:
        return [row for row in self.results if row.action == "push"]

    @property
    def digests(self) -> list[MatchResult]:
        return [row for row in self.results if row.action == "digest"]
