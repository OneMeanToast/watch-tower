"""Source catalog, publish-date window, manual upload, and the tether export."""

from fastapi.testclient import TestClient

from wt.catalog import build_catalog
from wt.exports import to_opml, to_tether
from wt.models import Indicator, Item
from wt.prefilter import source_allowed
from wt.uploads import UploadError, prepare_upload


def test_catalog_lists_configured_and_off_sources():
    from wt.store import Store

    store = Store(":memory:")
    try:
        catalog = build_catalog(store)
    finally:
        store.close()
    by_id = {row["id"]: row for row in catalog["sources"]}
    assert by_id["rss:hackernews-ai"]["status"] == "armed"
    assert by_id["rss:hackernews-ai"]["dated"] is True
    assert by_id["rss:hackernews-ai"]["group"] == "ai-news"
    assert by_id["bluesky"]["status"] == "off"
    assert by_id["x"]["status"] == "off"
    assert by_id["worldmonitor"]["status"] == "off"
    assert by_id["worldmonitor"]["detail"].startswith("4")
    assert by_id["upload"]["status"] == "armed"


def test_upload_is_eligible_for_every_indicator():
    indicator = Indicator.model_validate(
        {
            "id": "only-rss",
            "name": "Only RSS",
            "description": "rss lane",
            "sources": ["rss:ai-news"],
            "questions": {"hit": {"type": "noul", "instructions": "Is it a hit?"}},
            "policy": {"alert_if": "hit >= 0.5"},
        }
    )
    upload = Item(
        id="u",
        url="",
        source="upload",
        feed_id="manual",
        ts="2026-10-11T00:00:00+00:00",
        title="Pasted",
        text="A pasted note about an AI Act amendment.",
    )
    assert source_allowed(indicator, upload)


def test_prepare_upload_rejects_example_host():
    try:
        prepare_upload(title="", text="", url="https://example.test/secret", published_at="")
    except UploadError as exc:
        assert "example.test" in str(exc)
    else:
        raise AssertionError("expected a refusal")


def test_catalog_window_upload_and_tether(tmp_path, monkeypatch):
    monkeypatch.setattr("wt.deskapp.desk_db", lambda root=None: tmp_path / "desk.db")
    monkeypatch.setattr("wt.desks.live_path", lambda root=None: tmp_path / "desks.yaml")
    from wt.deskapp import app

    client = TestClient(app)
    wired = client.post("/wire", json={"mode": "demo"})
    assert wired.status_code == 200, wired.text
    catalog = client.get("/catalog")
    assert catalog.status_code == 200
    names = {row["id"]: row["items"] for row in catalog.json()["sources"]}
    assert names["arxiv:cs.CL"] >= 1
    papers = client.get("/catalog/items", params={"source": "arxiv", "feed_id": "cs.CL"})
    assert papers.status_code == 200
    assert papers.json()
    assert all(row["feed_id"] == "cs.CL" for row in papers.json())
    narrowed = client.get("/catalog", params={"since": "2026-10-06T00:00:00+00:00"})
    assert narrowed.status_code == 200
    kept = {row["title"] for row in narrowed.json()["items"]}
    assert any("JEPA" in title or "jepa" in title.lower() for title in kept)
    assert all("7B" not in title for title in kept)
    early = client.get("/matches", params={"desk": "weston", "until": "2026-10-03T12:00:00+00:00"})
    assert early.status_code == 200
    assert early.json()
    assert all(row["published_at"] <= "2026-10-03T12:00:00+00:00" for row in early.json())
    uploaded = client.post(
        "/catalog/upload",
        json={
            "title": "Commission proposes an AI Act amendment in a pasted memo",
            "text": "The European Commission proposed an amendment to the AI Act regulating open-weight models. The regulation is scheduled for a vote.",
            "published_at": "2026-10-11T12:00:00+00:00",
        },
    )
    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json()["method"] == "paste"
    again = client.get("/catalog")
    intake = again.json()["intake"]
    assert intake and intake[0]["kind"] == "upload"
    assert intake[0]["method"] == "paste"
    packet = client.get("/export", params={"desk": "weston", "format": "tether"})
    assert packet.status_code == 200
    body = packet.json()
    assert body["sources"]
    assert body["decisions"]
    opml = client.get("/export", params={"format": "opml"})
    assert opml.status_code == 200
    assert "hackernews" in opml.text
    assert "<opml" in opml.text
    bad = client.get("/catalog", params={"since": "not-a-date"})
    assert bad.status_code == 400


def test_tether_and_opml_shapes():
    text = to_opml([{"id": "rss:demo", "name": "Demo & Co", "kind": "rss", "url": "https://example.test/feed"}])
    assert "Demo &amp; Co" in text
    assert "xmlUrl" in text
    packet = to_tether(
        [{"id": "arxiv", "name": "arXiv", "kind": "arxiv", "status": "armed", "url": "", "items": 1, "newest": "", "dated": True}],
        [{"action": "push", "indicator_id": "ai-open-text-jepa", "title": "JEPA", "url": "", "source": "arxiv", "feed_id": "cs.CL", "published_at": "2026-10-06", "p_hit": 0.9, "reason": "hit"}],
        desk_name="Weston",
        since="",
        until="",
    )
    assert "JEPA" in packet
    assert "Weston" in packet
