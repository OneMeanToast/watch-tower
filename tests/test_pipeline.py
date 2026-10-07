from wt.evalkit import evaluate
from wt.indicators import load_indicators
from wt.pipeline import run_demo
from wt.settings import Settings


def test_demo_emits_the_strong_hits_and_skips_the_misses(root, tmp_path, capsys):
    settings = Settings.for_demo(root, db_path=tmp_path / "demo.db")
    settings.echo = True
    settings.ntfy_url = None
    settings.apprise_urls = []
    settings.indicators_dir = root / "config" / "indicators"
    settings.fixtures_path = root / "fixtures" / "items.json"
    report = run_demo(settings)
    out = capsys.readouterr().out
    assert report.vector_backend.startswith("sqlite-vec")
    assert report.embedder.startswith("hashing")
    pushes = {(row.indicator_id, row.url) for row in report.pushes}
    assert ("ai-open-text-jepa", "https://example.test/papers/llm-jepa-release") in pushes
    assert ("ai-open-text-jepa", "https://example.test/papers/llm-jepa-paraphrase") in pushes
    assert ("open-weight-model-release", "https://example.test/news/open-weight-7b") in pushes
    assert ("jev-like-decision-model", "https://example.test/papers/kev-0-8b") in pushes
    assert ("ai-policy-regulation", "https://example.test/news/ai-act-amendment") in pushes
    assert ("major-benchmark-sota", "https://example.test/papers/mmlu-sota") in pushes
    pushed_titles = {row.title for row in report.pushes}
    assert "What is JEPA? A short explainer" not in pushed_titles
    assert "Lakers defeat Celtics in overtime" not in pushed_titles
    assert "Machine learning conference opens in Vienna" not in pushed_titles
    assert "[push]" in out
    assert "ai-open-text-jepa" in out
    # Same database, second pass: items are duplicates and nothing new alerts.
    settings.echo = False
    from wt.pipeline import load_fixture_items, run_items
    from wt.store import Store

    store = Store(settings.db_path)
    try:
        again = run_items(settings, load_fixture_items(settings.fixtures_path), store)
    finally:
        store.close()
    assert again.duplicates == again.items_seen
    assert again.pushes == []


def test_seed_labels_have_perfect_push_precision(root):
    metrics = evaluate(
        indicators_dir=root / "config" / "indicators",
        fixtures_path=root / "fixtures" / "items.json",
        labels_path=root / "eval" / "labels.jsonl",
    )
    assert metrics["tp"] >= 7
    assert metrics["fp"] == 0
    assert metrics["fn"] == 0
    assert metrics["precision"] == 1.0
    assert metrics["recall"] == 1.0


def test_indicators_load(root):
    indicators = load_indicators(root / "config" / "indicators")
    assert len(indicators) >= 5
    assert any(item.id == "ai-open-text-jepa" for item in indicators)
