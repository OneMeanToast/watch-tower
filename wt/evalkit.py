"""Score labelled (item, indicator) pairs with the offline decider."""

from __future__ import annotations

import json
from pathlib import Path

from wt.decider import LexicalDecider
from wt.embed import HashingEmbedder
from wt.indicators import load_indicators
from wt.models import Item
from wt.normalize import content_hash, simhash, url_hash
from wt.pipeline import index_indicators, load_fixture_items
from wt.policy import decide_action
from wt.prefilter import select_candidates
from wt.store import Store


def load_seed_labels(path: str | Path) -> list[dict]:
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def evaluate(
    *,
    indicators_dir: str | Path,
    fixtures_path: str | Path,
    labels_path: str | Path,
    hash_dim: int = 256,
) -> dict:
    indicators = load_indicators(indicators_dir)
    by_id = {indicator.id: indicator for indicator in indicators}
    items = {item.url: item for item in load_fixture_items(fixtures_path)}
    labels = load_seed_labels(labels_path)
    embedder = HashingEmbedder(hash_dim)
    decider = LexicalDecider()
    store = Store(":memory:")
    try:
        index_indicators(store, embedder, indicators)
        scored = []
        for row in labels:
            item = items[row["url"]]
            indicator = by_id[row["indicator_id"]]
            vector = embedder.embed([item.document()], kind="document")[0]
            _remember(store, item, vector)
            cosines = {
                ind.id: store.max_indicator_cosine(vector, ind.id) for ind in indicators
            }
            candidates = select_candidates(item, indicators, cosines, top_k=20)
            cand = next((c for c in candidates if c.indicator.id == indicator.id), None)
            if cand is None:
                p_hit = 0.0
                severity = 0.0
                action = "drop"
                cosine_value = cosines.get(indicator.id, 0.0)
            else:
                decision = decider.decide_many(item, [cand])[indicator.id]
                action, _reason = decide_action(
                    indicator,
                    p_hit=decision.p_hit,
                    severity=decision.severity,
                    cosine_value=cand.cosine,
                    confidence=decision.confidence,
                )
                p_hit = decision.p_hit
                severity = decision.severity
                cosine_value = cand.cosine
            truth = row["label"] == "hit"
            scored.append(
                {
                    "url": row["url"],
                    "indicator_id": indicator.id,
                    "label": row["label"],
                    "p_hit": p_hit,
                    "severity": severity,
                    "cosine": cosine_value,
                    "action": action,
                    "pred_push": action == "push",
                    "truth": truth,
                }
            )
    finally:
        store.close()
    return summarize(scored)


def summarize(scored: list[dict]) -> dict:
    tp = fp = fn = tn = 0
    brier_sum = 0.0
    for row in scored:
        pred = row["pred_push"]
        truth = row["truth"]
        if pred and truth:
            tp += 1
        elif pred and not truth:
            fp += 1
        elif not pred and truth:
            fn += 1
        else:
            tn += 1
        y = 1.0 if truth else 0.0
        brier_sum += (row["p_hit"] - y) ** 2
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    return {
        "n": len(scored),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": precision,
        "recall": recall,
        "brier": brier_sum / len(scored) if scored else 0.0,
        "rows": scored,
    }


def _remember(store: Store, item: Item, vector: list[float]) -> None:
    if store.get_item(item.id) is not None:
        return
    store.upsert_item(
        item,
        url_hash=url_hash(item.url),
        content_hash=content_hash(item.title, item.text),
        simhash=simhash(item.document()),
        vector=vector,
    )


def export_kev_jsonl(
    feedback_path: str | Path,
    indicators_dir: str | Path,
    indicator_ids: set[str] | None = None,
) -> list[dict]:
    """Turn thumbs-up/down JSONL into Kev fine-tune rows (noul label on `hit`)."""
    indicators = {ind.id: ind for ind in load_indicators(indicators_dir)}
    rows = []
    path = Path(feedback_path)
    if not path.is_file():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if indicator_ids is not None and record.get("indicator_id") not in indicator_ids:
            continue
        indicator = indicators.get(record["indicator_id"])
        if indicator is None or "hit" not in indicator.questions:
            continue
        question = indicator.questions["hit"]
        entry = {
            "type": "noul",
            "instructions": question.instructions,
            "label": record["label"] == "hit",
        }
        if question.criteria is not None:
            entry["criteria"] = question.criteria
        state = f"{record.get('title', '')}\n{record.get('text', '')}".strip()
        rows.append({"state": state, "questions": {"hit": entry}})
    return rows
