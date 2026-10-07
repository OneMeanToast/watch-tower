from datetime import datetime, timezone

from wt.models import Item
from wt.store import Store


def _item() -> Item:
    return Item(
        id="item1",
        url="https://example.test/a",
        source="arxiv",
        ts=datetime(2026, 10, 6, tzinfo=timezone.utc),
        title="Hello",
        text="World",
        lang="en",
    )


def test_sqlite_vec_cosine_and_roundtrip(tmp_path):
    store = Store(tmp_path / "tower.db")
    try:
        assert store.has_vec, "sqlite-vec failed to load; the MVP stores vectors with it"
        same = store.sql_cosine([1.0, 0.0, 0.0], [2.0, 0.0, 0.0])
        orthogonal = store.sql_cosine([1.0, 0.0, 0.0], [0.0, 1.0, 0.0])
        assert same > 0.99
        assert abs(orthogonal) < 0.01
        store.upsert_item(
            _item(),
            url_hash="abc",
            content_hash="def",
            simhash=7,
            vector=[1.0, 0.0, 0.0, 0.0],
        )
        store.upsert_embedding("ind", "indicator", "", [1.0, 0.0, 0.0, 0.0])
        store.upsert_embedding("ind", "indicator", "pos:0", [0.0, 1.0, 0.0, 0.0])
        store.commit()
        score = store.max_indicator_cosine([1.0, 0.0, 0.0, 0.0], "ind")
        assert score > 0.99
        near = store.nearest_items([1.0, 0.0, 0.0, 0.0], limit=5)
        assert near and near[0][0] == "item1"
        assert near[0][1] > 0.99
    finally:
        store.close()


def test_dimension_change_is_refused(tmp_path):
    store = Store(tmp_path / "tower.db")
    try:
        store.upsert_embedding("a", "indicator", "", [0.1, 0.2])
        store.commit()
        try:
            store.upsert_embedding("b", "indicator", "", [0.1, 0.2, 0.3])
        except RuntimeError as exc:
            assert "dimension" in str(exc)
        else:
            raise AssertionError("expected a dimension error")
    finally:
        store.close()
