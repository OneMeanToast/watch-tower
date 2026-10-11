"""Forensic extraction: stub proposer, queue, Jev mock, sources, span eval."""

import json
from datetime import datetime, timezone

import httpx

from wt.cost import record_usage
from wt.extract_graph.evalkit import gold_spans, span_scores
from wt.extract_graph.graph import SqliteGraphView
from wt.extract_graph.ground import code_facts, normalise_dates
from wt.extract_graph.ontology import HighValueRule, Ontology, TypeSpec
from wt.extract_graph.propose import StubProposer, build_proposer
from wt.extract_graph.queue import note_high_value, run_once
from wt.extract_graph.run import FIXTURE_TEXT, apply_action, extract_text, seed_fixture, undo_merge
from wt.extract_graph.verify import verify_proposal
from wt.ingest.cisa_kev import items_from_kev
from wt.ingest.federal_register import items_from_federal_register
from wt.ingest.gdelt_gkg import fetch_gdelt_graphs, items_from_events, items_from_gkg
from wt.ingest.hfhub import items_from_hf
from wt.ingest.nvd import items_from_nvd
from wt.ingest.openalex import openalex_request
from wt.jev import JevDecider
from wt.models import Item
from wt.store import Store
from wt.text import parse_time


def _item(**updates) -> Item:
    base = dict(
        id="item-1",
        url="https://example.test/paper",
        source="arxiv",
        ts=parse_time("2026-03-02T00:00:00Z"),
        title="A paper",
        text=FIXTURE_TEXT,
    )
    base.update(updates)
    return Item(**base)


def _cap(daily: int) -> Ontology:
    base = Ontology(
        version=1,
        entities=[TypeSpec("Organization")],
        relations=[],
        events=[],
        gazetteer=[],
        high_value=HighValueRule(daily_cap=daily, paper_sources=("arxiv",), advisory_sources=("nvd",)),
    )
    return base


def test_offsets_match_the_surface():
    proposal = StubProposer().propose(FIXTURE_TEXT)
    assert proposal.mentions
    for mention in proposal.mentions:
        assert FIXTURE_TEXT[mention.start : mention.end] == mention.surface


def test_stub_relations_events_coref_and_suffix():
    store = Store(":memory:")
    extract_text(
        store,
        doc_id="fx-advisory-1",
        title="fixture",
        text=FIXTURE_TEXT,
        local_only=True,
        published=datetime(2026, 3, 2, tzinfo=timezone.utc),
        proposer=build_proposer("stub"),
    )
    mentions = store.conn.execute("SELECT surface, type, entity_id FROM extract_mentions").fetchall()
    by_surface = {}
    for row in mentions:
        by_surface.setdefault(row["surface"], []).append(row)
    assert by_surface["CVE-2024-12345"][0]["type"] == "Vulnerability"
    assert by_surface["ExampleSoft Gateway"][0]["type"] == "SoftwareProduct"
    assert len(by_surface["Llama"]) == 2
    assert by_surface["Llama"][0]["entity_id"] == by_surface["Llama"][1]["entity_id"]
    relations = {
        (row["type"], row["modality"])
        for row in store.conn.execute("SELECT type, modality FROM extract_relations")
    }
    assert ("affects", "asserted") in relations
    assert ("manufacturer", "asserted") in relations
    assert ("adopts", "speculative") in relations
    events = {
        (row["type"], row["cameo_code"], row["when_norm"])
        for row in store.conn.execute("SELECT type, cameo_code, when_norm FROM extract_events")
    }
    assert ("PolicyAction", "01", "2026-03-02") in events
    assert any(row[0] == "ModelRelease" for row in events)
    captions = {
        row["id"]: row["caption"]
        for row in store.conn.execute("SELECT id, caption FROM extract_entities")
    }
    judgments = store.conn.execute(
        "SELECT left_id, right_id, judgment FROM extract_judgments WHERE undone = 0"
    ).fetchall()
    pairs = {frozenset({captions[row["left_id"]], captions[row["right_id"]]}) for row in judgments}
    assert frozenset({"ExampleSoft", "ExampleSoft Inc."}) in pairs
    assert all(row["judgment"] == "unsure" for row in judgments)
    assert frozenset({"ExampleSoft", "ExampleSoft Gateway"}) not in pairs


def test_high_value_enqueue_and_cap():
    store = Store(":memory:")
    rule = _cap(2)
    assert note_high_value(store, _item(id="a", source="rss"), ontology=rule) is None
    assert note_high_value(store, _item(id="b", source="arxiv"), ontology=rule)
    assert note_high_value(store, _item(id="b", source="arxiv"), ontology=rule)
    assert store.conn.execute("SELECT COUNT(*) AS n FROM extract_jobs").fetchone()["n"] == 1
    assert note_high_value(store, _item(id="c", source="upload"), ontology=rule)
    assert note_high_value(store, _item(id="d", source="nvd"), pushed=True, ontology=rule) is None
    assert store.conn.execute("SELECT COUNT(*) AS n FROM extract_jobs").fetchone()["n"] == 2


def test_worker_writes_a_document():
    store = Store(":memory:")
    item = _item(id="paper-1", source="arxiv")
    store.upsert_item(
        item,
        url_hash="h",
        content_hash="c",
        simhash=1,
        vector=[0.1, 0.2],
    )
    note_high_value(store, item, ontology=_cap(5))
    doc_id = run_once(store, ask=None, proposer=build_proposer("stub"))
    assert doc_id
    row = store.conn.execute("SELECT text FROM extract_documents WHERE id = ?", (doc_id,)).fetchone()
    assert "CVE-2024-12345" in row["text"]
    assert store.conn.execute("SELECT state FROM extract_jobs").fetchone()["state"] == "done"


def test_dates_are_code_facts_and_jev_is_mocked(monkeypatch):
    found = normalise_dates("On 2 March 2026, CISA added a CVE.")
    assert any(row["iso"] == "2026-03-02" for row in found)
    facts = code_facts("On 2 March 2026, CISA added a CVE.")
    assert "2026-03-02" in facts
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        body = json.loads(request.content.decode())
        assert "2026-03-02" in body["state"]
        answers = {}
        for key, question in body["questions"].items():
            assert "what date" not in question["instructions"].lower()
            if question["type"] == "noul":
                answers[key] = {"noul": 0.97}
            elif question["type"] == "choice":
                first = next(iter(question["criteria"]))
                answers[key] = {"choice": first, "confidence": 0.8}
                if key.startswith("type:"):
                    original = [row.id for row in __import__("wt.extract_graph.ontology", fromlist=["default_ontology"]).default_ontology().entities]
                    assert first != original[0]
        return httpx.Response(
            200,
            json={
                "model": "jev-1.13.0",
                "answers": answers,
                "usage": {"input_tokens": 563, "output_tokens": 148},
            },
        )

    store = Store(":memory:")
    decider = JevDecider(
        api_key="test-key",
        transport=httpx.MockTransport(handler),
        store=store,
        monthly_usd=10,
    )
    extract_text(
        store,
        doc_id="jev-doc",
        title="fixture",
        text=FIXTURE_TEXT,
        local_only=False,
        ask=decider.ask,
        proposer=build_proposer("stub"),
    )
    assert calls["n"] == 1
    scored = store.conn.execute(
        "SELECT p_jev FROM extract_mentions WHERE p_jev IS NOT NULL"
    ).fetchall()
    assert scored
    assert all(row["p_jev"] == 0.97 for row in scored)
    spent = store.conn.execute("SELECT COALESCE(SUM(usd), 0) AS usd FROM jev_usage").fetchone()["usd"]
    assert spent > 0
    extract_text(
        store,
        doc_id="jev-doc-2",
        title="fixture",
        text=FIXTURE_TEXT,
        local_only=False,
        ask=decider.ask,
        proposer=build_proposer("stub"),
    )
    assert calls["n"] == 1


def test_budget_blocks_the_http_call():
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"answers": {}, "usage": {"input_tokens": 1, "output_tokens": 0}})

    store = Store(":memory:")
    record_usage(store, run_id="x", input_tokens=1_000_000, output_tokens=0, usd=10.0, cached=0, calls=1)
    decider = JevDecider(
        api_key="test-key",
        transport=httpx.MockTransport(handler),
        store=store,
        monthly_usd=1,
    )
    extract_text(
        store,
        doc_id="local-doc",
        title="fixture",
        text=FIXTURE_TEXT,
        ask=decider.ask,
        proposer=build_proposer("stub"),
    )
    assert calls["n"] == 0
    row = store.conn.execute(
        "SELECT type FROM extract_mentions WHERE surface = ?", ("CVE-2024-12345",)
    ).fetchone()
    assert row["type"] == "Vulnerability"


def test_undo_merge_is_reversible():
    store = Store(":memory:")
    seed_fixture(store)
    judgment = store.conn.execute(
        "SELECT id, left_id, right_id FROM extract_judgments WHERE judgment = 'unsure'"
    ).fetchone()
    apply_action(store, {"action": "accept", "kind": "judgment", "id": str(judgment["id"])})
    merged = store.conn.execute(
        "SELECT merged_into FROM extract_entities WHERE id = ?", (judgment["right_id"],)
    ).fetchone()
    assert merged["merged_into"] == judgment["left_id"]
    action = store.conn.execute(
        "SELECT action FROM extract_actions WHERE target_kind = 'judgment'"
    ).fetchone()
    assert action["action"] == "accept"
    latest = store.conn.execute(
        """
        SELECT id FROM extract_judgments
        WHERE left_id = ? AND right_id = ? AND judgment = 'yes' AND undone = 0
        ORDER BY id DESC LIMIT 1
        """,
        (judgment["left_id"], judgment["right_id"]),
    ).fetchone()
    undo_merge(store, latest["id"])
    cleared = store.conn.execute(
        "SELECT merged_into FROM extract_entities WHERE id = ?", (judgment["right_id"],)
    ).fetchone()
    assert cleared["merged_into"] is None
    nodes = {row["caption"] for row in SqliteGraphView(store.conn).snapshot()["nodes"]}
    assert "ExampleSoft" in nodes
    assert "ExampleSoft Inc." in nodes


def test_span_scores_and_gold_file():
    metrics = span_scores({(0, 1, "A"), (2, 3, "B")}, {(0, 1, "A"), (4, 5, "C")})
    assert metrics["precision"] == 0.5
    assert metrics["recall"] == 0.5
    from pathlib import Path

    payload = json.loads((Path(__file__).resolve().parents[1] / "eval" / "extraction_gold.json").read_text())
    predicted = {(row.start, row.end, row.type) for row in StubProposer().propose(payload["text"]).mentions}
    scored = span_scores(gold_spans(payload), predicted)
    assert scored["precision"] == 1
    assert 0 < scored["recall"] < 1
    assert scored["fn"] >= 1


def test_openalex_puts_the_key_on_the_query(monkeypatch):
    monkeypatch.setenv("WT_OPENALEX_KEY", "oa-test")
    monkeypatch.setenv("WT_OPENALEX_MAILTO", "desk@example.test")
    params, headers = openalex_request({"search": "llm", "per_page": 2})
    assert params["api_key"] == "oa-test"
    assert params["mailto"] == "desk@example.test"
    assert headers["Authorization"] == "Bearer oa-test"


def test_keyless_parsers_and_gkg_download_off():
    kev = items_from_kev(
        {
            "vulnerabilities": [
                {
                    "cveID": "CVE-2024-12345",
                    "vulnerabilityName": "ExampleSoft Gateway",
                    "shortDescription": "A flaw in the gateway.",
                    "dateAdded": "2026-03-02",
                }
            ]
        }
    )
    assert kev[0].source == "cisa_kev"
    assert "CVE-2024-12345" in kev[0].title
    nvd = items_from_nvd(
        {
            "vulnerabilities": [
                {
                    "cve": {
                        "id": "CVE-2024-99999",
                        "published": "2026-03-02T00:00:00.000",
                        "descriptions": [{"lang": "en", "value": "Example advisory."}],
                    }
                }
            ]
        }
    )
    assert nvd[0].source == "nvd"
    models = items_from_hf([{"id": "example/llama", "pipeline_tag": "text-generation", "createdAt": "2026-03-02T00:00:00Z"}])
    assert models[0].source == "hf_hub"
    articles = items_from_federal_register(
        {
            "results": [
                {
                    "title": "Artificial intelligence guidance",
                    "abstract": "An agency notice.",
                    "html_url": "https://www.federalregister.gov/documents/example",
                    "publication_date": "2026-03-02",
                }
            ]
        }
    )
    assert articles[0].source == "federal_register"
    gkg = items_from_gkg(
        "rec\t20260302120000\t1\texample.test\thttps://example.test/advisory\t\t\tCYBER_ATTACK;VULNERABILITY\n"
    )
    assert gkg[0].source == "gdelt_gkg"
    assert gkg[0].url.startswith("https://example.test/")
    events = items_from_events(
        "1\t20260302\t\t\t\t\tCISA\t\t\t\t\thttps://example.test/event\n"
    )
    assert events[0].source == "gdelt_events"
    assert fetch_gdelt_graphs({"gkg_enabled": False, "events_enabled": False}) == []


def test_accept_writes_a_label_and_board_highlights():
    from wt.extract_graph.api import build_board

    store = Store(":memory:")
    board = build_board(store)
    assert board["document"]["text"][board["mentions"][0]["start"] : board["mentions"][0]["end"]] == board["mentions"][0]["surface"]
    mention = board["review"][0]
    assert mention["kind"] == "mention"
    apply_action(store, {"action": "accept", "kind": "mention", "id": mention["id"]})
    stored = store.conn.execute(
        "SELECT action, target_id FROM extract_actions"
    ).fetchone()
    assert stored["action"] == "accept"
    assert stored["target_id"] == mention["id"]
    status = store.conn.execute(
        "SELECT status FROM extract_mentions WHERE id = ?", (mention["id"],)
    ).fetchone()["status"]
    assert status == "accepted"


def test_verify_keeps_local_candidates_when_ask_fails():
    proposal = StubProposer().propose(FIXTURE_TEXT)

    def ask(_state, _questions):
        raise RuntimeError("down")

    checked, _model = verify_proposal(FIXTURE_TEXT, proposal, ask, __import__("wt.extract_graph.ontology", fromlist=["default_ontology"]).default_ontology())
    assert any(row.surface == "CVE-2024-12345" and row.type == "Vulnerability" for row in checked.mentions)
