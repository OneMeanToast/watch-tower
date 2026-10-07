from wt.embed import HashingEmbedder, cosine
from wt.normalize import canonical_url, content_hash, make_item, simhash


def test_url_canonicalization_strips_tracking():
    assert canonical_url("https://Example.com/a/?utm_source=x&id=1") == canonical_url(
        "https://www.example.com/a?id=1"
    )


def test_simhash_and_embedder_separate_topics():
    left = simhash("Meta releases open weights for an LLM-JEPA text embedding model")
    right = simhash("The Lakers defeated the Celtics in overtime")
    assert left == simhash("Meta releases open weights for an LLM-JEPA text embedding model")
    assert (left ^ right).bit_count() > 3
    embedder = HashingEmbedder(128)
    vecs = embedder.embed(
        [
            "Meta releases open weights for an LLM-JEPA text embedding model under Apache-2.0",
            "Meta releases open weights for an LLM-JEPA embedding checkpoint under Apache-2.0",
            "The Lakers defeated the Celtics in overtime behind a late run",
        ]
    )
    assert cosine(vecs[0], vecs[1]) > cosine(vecs[0], vecs[2])


def test_make_item_id_is_stable_for_the_same_url():
    first = make_item(url="https://example.test/a?utm_medium=email", source="rss", title="Hello", text="Body")
    second = make_item(url="https://example.test/a", source="rss", title="Hello again", text="Other")
    assert first is not None and second is not None
    assert first.id == second.id
    assert content_hash("Hello", "Body") != content_hash("Hello again", "Other")
