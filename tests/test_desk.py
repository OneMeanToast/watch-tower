"""Desk views: traces survive the store, lanes filter, and the local API."""

import json

from fastapi.testclient import TestClient

from wt.desks import allows, upsert_desk
from wt.exports import filter_rows, to_markdown
from wt.pipeline import run_demo
from wt.settings import Settings
from wt.store import Store


def test_demo_stores_a_ten_gate_trace(tmp_path):
    settings = Settings.for_demo(db_path=tmp_path / "desk.db")
    report = run_demo(settings)
    assert report.pushes
    store = Store(settings.db_path)
    try:
        rows = store.list_matches(limit=50)
    finally:
        store.close()
    assert rows
    trace = json.loads(rows[0]["trace_json"])
    assert [step["gate"] for step in trace] == [
        "Source",
        "Visible text",
        "Spam",
        "Dedupe",
        "Event",
        "Prefilter",
        "Injection",
        "Decision",
        "Cascade",
        "Policy",
    ]
    assert rows[0]["item_event"]


def test_desk_lanes_and_markdown(tmp_path):
    assert allows({"indicators": []}, "ai-policy-regulation")
    assert allows({"indicators": ["ai-policy-regulation"]}, "ai-policy-regulation")
    assert not allows({"indicators": ["ai-policy-regulation"]}, "ai-open-text-jepa")
    rows = [
        {"indicator_id": "ai-policy-regulation", "action": "push", "title": "Act", "url": "https://example.test/a", "p_hit": 0.9, "severity": 2, "cosine": 0.4, "reason": "hit"},
        {"indicator_id": "ai-open-text-jepa", "action": "push", "title": "JEPA", "url": "https://example.test/b", "p_hit": 0.9, "severity": 2, "cosine": 0.4, "reason": "hit"},
    ]
    desk = {"id": "policy", "name": "Policy", "indicators": ["ai-policy-regulation"], "exports": ["markdown"]}
    kept = filter_rows(rows, desk)
    assert [row["title"] for row in kept] == ["Act"]
    text = to_markdown(kept, desk_name="Policy")
    assert "Act" in text
    assert "JEPA" not in text
    saved = upsert_desk(
        {"id": "night", "name": "Night", "brief": "Quiet", "indicators": [], "exports": ["csv"]},
        root=tmp_path,
    )
    assert saved["id"] == "night"
    assert (tmp_path / "data" / "desks.yaml").is_file()


def test_desk_api_replays_fixtures(tmp_path, monkeypatch):
    monkeypatch.setattr("wt.deskapp.desk_db", lambda root=None: tmp_path / "desk.db")
    monkeypatch.setattr("wt.desks.live_path", lambda root=None: tmp_path / "desks.yaml")
    from wt.deskapp import app

    client = TestClient(app)
    status = client.get("/status")
    assert status.status_code == 200
    assert status.json()["counts"]["matches"] == 0
    wired = client.post("/wire", json={"mode": "demo"})
    assert wired.status_code == 200, wired.text
    body = wired.json()
    assert body["pushes"] >= 1
    assert body["backend"] == "lexical"
    matches = client.get("/matches", params={"desk": "policy"})
    assert matches.status_code == 200
    policy_rows = matches.json()
    assert policy_rows
    assert {row["indicator_id"] for row in policy_rows} == {"ai-policy-regulation"}
    assert all(len(row["trace"]) == 10 for row in policy_rows)
    weston = client.get("/matches", params={"desk": "weston"})
    assert any(row["indicator_id"] == "ai-open-text-jepa" for row in weston.json())
    missing = client.get("/matches", params={"desk": "no-such-desk"})
    assert missing.status_code == 404
