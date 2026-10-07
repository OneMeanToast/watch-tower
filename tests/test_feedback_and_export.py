import json
from datetime import datetime, timezone

from wt.evalkit import export_kev_jsonl
from wt.feedback import make_server, record_feedback
from wt.models import Item, MatchResult
from wt.store import Store
import urllib.request


def test_feedback_roundtrip_and_kev_export(root, tmp_path):
    db = tmp_path / "tower.db"
    labels = tmp_path / "feedback.jsonl"
    store = Store(db)
    item = Item(
        id="item1",
        url="https://example.test/jepa",
        source="arxiv",
        ts=datetime(2026, 10, 6, tzinfo=timezone.utc),
        title="Meta releases LLM-JEPA weights",
        text="Training code is on GitHub.",
        lang="en",
    )
    store.upsert_item(item, url_hash="u", content_hash="c", simhash=1, vector=[1.0, 0.0, 0.0])
    result = MatchResult(
        match_id="match123",
        item_id="item1",
        indicator_id="ai-open-text-jepa",
        title=item.title,
        url=item.url,
        cosine=0.8,
        keyword_hit=True,
        p_hit=0.91,
        severity=3.2,
        category="release",
        confidence=0.8,
        action="push",
        reason="alert_if matched",
        backend="lexical",
    )
    store.save_match(result, {"hit": {"type": "noul", "noul": 0.91}})
    store.close()

    row = record_feedback(db, "match123", "up", labels_path=labels)
    assert row["label"] == "hit"
    server = make_server(db, labels, "127.0.0.1", 0)
    port = server.server_address[1]
    import threading

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/feedback/match123?label=down", timeout=5
        ) as response:
            body = response.read().decode()
        assert "miss" in body
    finally:
        server.shutdown()
        server.server_close()

    exported = export_kev_jsonl(labels, root / "config" / "indicators")
    assert len(exported) == 2
    assert exported[0]["questions"]["hit"]["label"] is True
    assert exported[1]["questions"]["hit"]["label"] is False
    assert exported[0]["questions"]["hit"]["type"] == "noul"
    assert "LLM-JEPA" in exported[0]["state"]
    json.dumps(exported[0])


def test_jepa_head_fits_a_linear_map():
    import numpy as np

    from wt.experimental.jepa_head import JepaHead

    rng = np.random.default_rng(0)
    x = rng.normal(size=(48, 8))
    true_w = rng.normal(size=(8, 8))
    y = x @ true_w
    head = JepaHead(8).fit(x, y, l2=1e-8)
    predicted = head.predict(x[3])
    assert np.linalg.norm(predicted - y[3]) < 1e-4
    assert head.score(x[3], y[3]) > 0.99
