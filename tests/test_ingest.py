import json
from pathlib import Path

import pytest

from wt.ingest.bluesky import item_from_jetstream
from wt.ingest.gdelt import items_from_gdelt
from wt.ingest.mastodon import items_from_mastodon
from wt.ingest.openalex import items_from_openalex, reconstruct_abstract
from wt.ingest.rss import items_from_xml
from wt.ingest.x_paid import XPaidDisabled, fetch_x

ROOT = Path(__file__).resolve().parents[1]


def test_rss_strips_html_and_utm():
    items = items_from_xml((ROOT / "fixtures" / "sample_feed.xml").read_bytes(), source="rss", feed_id="ai-news")
    assert len(items) == 1
    assert items[0].url == "https://example.test/papers/llm-jepa-release"
    assert "<p>" not in items[0].text
    assert "LLM-JEPA" in items[0].text


def test_gdelt_openalex_mastodon_bluesky_fixtures():
    gdelt = items_from_gdelt(json.loads((ROOT / "fixtures" / "gdelt_sample.json").read_text()))
    assert gdelt[0].source == "gdelt"
    assert gdelt[0].lang == "en"
    assert "utm_source" not in gdelt[0].url

    abstract = reconstruct_abstract({"Open": [0], "weights": [1], "for": [2], "JEPA.": [3]})
    assert abstract.startswith("Open weights")
    works = items_from_openalex(json.loads((ROOT / "fixtures" / "openalex_sample.json").read_text()))
    assert works[0].source == "openalex"
    assert works[0].authors == ["A. Researcher"]
    assert "JEPA" in works[0].text

    posts = items_from_mastodon(
        json.loads((ROOT / "fixtures" / "mastodon_sample.json").read_text()),
        instance="https://mastodon.social",
        tag="AI",
    )
    assert "open weights" in posts[0].text
    assert "<p>" not in posts[0].text

    event = json.loads((ROOT / "fixtures" / "bluesky_sample.json").read_text())
    post = item_from_jetstream(event, keywords=["JEPA"])
    assert post is not None
    assert post.url == "https://bsky.app/profile/did:plc:example/post/3abc"
    assert item_from_jetstream(event, keywords=["basketball"]) is None
    reply = json.loads(json.dumps(event))
    reply["commit"]["record"]["reply"] = {"parent": {"uri": "at://x"}}
    assert item_from_jetstream(reply, skip_replies=True) is None


def test_x_is_disabled():
    with pytest.raises(XPaidDisabled):
        fetch_x({"enabled": True})
