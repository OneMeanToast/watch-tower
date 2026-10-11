"""Span precision and recall. A hit is the same start, end, and type."""

from __future__ import annotations

import json
from pathlib import Path


def locate(text: str, surface: str, occurrence: int = 1) -> tuple[int, int] | None:
    start = 0
    seen = 0
    while True:
        at = text.find(surface, start)
        if at < 0:
            return None
        seen += 1
        if seen == occurrence:
            return at, at + len(surface)
        start = at + len(surface)


def span_scores(gold: set[tuple[int, int, str]], predicted: set[tuple[int, int, str]]) -> dict:
    overlap = gold & predicted
    tp = len(overlap)
    fp = len(predicted - gold)
    fn = len(gold - predicted)
    precision = tp / len(predicted) if predicted else 0.0
    recall = tp / len(gold) if gold else 0.0
    return {
        "precision": precision,
        "recall": recall,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "gold": len(gold),
        "predicted": len(predicted),
    }


def gold_spans(payload: dict) -> set[tuple[int, int, str]]:
    text = payload["text"]
    found = set()
    for row in payload.get("spans") or []:
        if "start" in row and "end" in row:
            start, end = int(row["start"]), int(row["end"])
        else:
            spot = locate(text, row["surface"], int(row.get("occurrence") or 1))
            if spot is None:
                continue
            start, end = spot
        if text[start:end] != row["surface"]:
            continue
        found.add((start, end, row["type"]))
    return found


def score_file(path: Path, predicted: set[tuple[int, int, str]]) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return span_scores(gold_spans(payload), predicted)
