"""One cycle: dedupe, embed, prefilter, decide, threshold, alert."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import yaml

from wt.alert import deliver, usable_channels
from wt.decider import Decider, DeciderError, build_decider
from wt.embed import Embedder, build_embedder, cosine
from wt.events import confirm_same_event, same_event_signals
from wt.extract import enrich_item
from wt.guard import classify_text
from wt.indicators import load_indicators
from wt.models import CycleReport, Decision, Indicator, Item, MatchResult
from wt.normalize import (
    INGEST_COSINE,
    INGEST_HAMMING,
    content_hash,
    simhash,
    url_hash,
)
from wt.policy import decide_action, parse_duration, suppressed_by_cooldown
from wt.prefilter import select_candidates
from wt.safety import excerpts, strip_hidden
from wt.settings import Settings
from wt.spam import filter_spam
from wt.store import Store
from wt.text import clean_text, sha256_text, utcnow

log = logging.getLogger("wt.pipeline")


def load_sources(path: str | Path) -> dict:
    file = Path(path)
    if not file.is_file():
        raise FileNotFoundError(f"sources file not found: {file}")
    data = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{file} must be a YAML mapping")
    return data


def load_fixture_items(path: str | Path) -> list[Item]:
    from wt.normalize import make_item
    from wt.text import parse_time

    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    items: list[Item] = []
    for row in raw:
        item = make_item(
            url=row.get("url") or "",
            source=row.get("source") or "fixture",
            feed_id=row.get("feed_id") or "",
            title=row.get("title") or "",
            text=row.get("text") or "",
            ts=parse_time(row.get("ts")),
            authors=row.get("authors") or [],
            lang=row.get("lang"),
        )
        if item is not None:
            items.append(item)
    return items


def index_indicators(store: Store, embedder: Embedder, indicators: list[Indicator]) -> None:
    for indicator in indicators:
        anchor = embedder.embed([indicator.anchor_text()], kind="query")[0]
        store.upsert_embedding(indicator.id, "indicator", "", anchor)
        if indicator.positives:
            for index, vector in enumerate(embedder.embed(indicator.positives, kind="query")):
                store.upsert_embedding(indicator.id, "indicator", f"pos:{index}", vector)
        if indicator.negatives:
            for index, vector in enumerate(embedder.embed(indicator.negatives, kind="document")):
                store.upsert_embedding(indicator.id, "indicator", f"neg:{index}", vector)
    store.commit()


def run_items(
    settings: Settings,
    items: list[Item],
    store: Store,
    *,
    embedder: Embedder | None = None,
    decider: Decider | None = None,
    indicators: list[Indicator] | None = None,
) -> CycleReport:
    indicators = indicators or load_indicators(settings.indicators_dir)
    embedder = embedder or build_embedder(
        settings.embedder, model_name=settings.embedder_model, hash_dim=settings.hash_dim
    )
    decider = decider or build_decider(
        settings.decider,
        decider_url=settings.decider_url,
        decider_model=settings.decider_model,
        api_key=settings.decider_api_key,
        ollama_url=settings.ollama_url,
        ollama_model=settings.ollama_model,
        fallback=settings.decider_fallback,
    )
    index_indicators(store, embedder, indicators)
    visible = [_visible_item(item) for item in items]
    visible, spam_dropped = filter_spam(visible, min_cluster=settings.spam_min_cluster)
    fresh, duplicates = _dedupe(store, embedder, visible)
    _assign_events(store, decider, fresh)
    results: list[MatchResult] = []
    for item, vector in fresh:
        cosines = {
            indicator.id: store.max_indicator_cosine(vector, indicator.id)
            for indicator in indicators
        }
        candidates = select_candidates(item, indicators, cosines, top_k=settings.top_k)
        if not candidates:
            continue
        enriched = enrich_item(item, enabled=settings.fetch_fulltext)
        if enriched.text != item.text:
            store.update_item_text(item.id, enriched.text)
            item = enriched
        verdict = classify_text(item.document(), mode=settings.injection_guard)
        decisions = {} if verdict.flagged else decider.decide_many(item, candidates)
        for cand in candidates:
            if verdict.flagged:
                decision = Decision(
                    p_hit=0.0,
                    severity=0.0,
                    confidence=0.0,
                    backend="guard",
                    answers={},
                )
                action, reason = (
                    "digest",
                    f"injection flagged ({verdict.backend}): {verdict.reason}",
                )
            else:
                decision = decisions.get(cand.indicator.id)
                if decision is None:
                    continue
                action, reason = decide_action(
                    cand.indicator,
                    p_hit=decision.p_hit,
                    severity=decision.severity,
                    cosine_value=cand.cosine,
                    confidence=decision.confidence,
                )
                decision, action, reason = maybe_escalate(
                    settings, item, cand, decision, action, reason
                )
            event_id = item.event_id or item.id
            match_id = sha256_text(f"{item.id}:{cand.indicator.id}")[:16]
            if action == "push" and store.has_push(match_id):
                action = "suppressed"
                reason = "already alerted"
            elif action == "push" and store.event_has_push(event_id, cand.indicator.id):
                action = "suppressed"
                reason = "same event already alerted"
            elif action == "push":
                cooldown = parse_duration(cand.indicator.policy.cooldown)
                priors = store.recent_push_priors(
                    cand.indicator.id, since=utcnow() - cooldown
                )
                if suppressed_by_cooldown(
                    priors,
                    url_hash=url_hash(item.url) or "",
                    simhash=simhash(item.document()),
                    vector=vector,
                    now=utcnow(),
                    cooldown=cooldown,
                ):
                    action = "suppressed"
                    reason = "cooldown: near-duplicate of a recent alert"
            snippets = excerpts(item.document(), cand.matched_terms)
            explanation = _explanation(item, cand, decision, event_id)
            result = MatchResult(
                match_id=match_id,
                item_id=item.id,
                indicator_id=cand.indicator.id,
                title=item.title,
                url=item.url,
                cosine=cand.cosine,
                keyword_hit=cand.keyword_hit,
                p_hit=decision.p_hit,
                severity=decision.severity,
                category=decision.category,
                confidence=decision.confidence,
                action=action,  # type: ignore[arg-type]
                reason=reason,
                backend=decision.backend,
                matched_terms=list(cand.matched_terms),
                snippets=snippets,
                explanation=explanation,
                event_id=event_id,
            )
            store.save_match(result, decision.answers)
            if action == "push":
                channels = usable_channels(
                    cand.indicator.alert.channels,
                    [settings.ntfy_url] if settings.ntfy_url else [],
                    settings.apprise_urls,
                )
                status = deliver(
                    result,
                    channels=channels,
                    feedback_base=settings.feedback_base,
                    echo=settings.echo,
                )
                store.record_alert(result, kind="push", body=result.title, status=status)
            elif action == "digest" and settings.echo:
                deliver(
                    result,
                    channels=[],
                    feedback_base=settings.feedback_base,
                    echo=True,
                )
            results.append(result)
    backends = sorted({row.backend for row in results}) or [decider.name]
    return CycleReport(
        items_seen=len(items),
        items_new=len(fresh),
        duplicates=duplicates,
        spam_dropped=spam_dropped,
        results=results,
        backend=",".join(backends),
        embedder=embedder.name,
        vector_backend=store.vector_backend,
    )


def _visible_item(item: Item) -> Item:
    title = clean_text(strip_hidden(item.title))
    text = clean_text(strip_hidden(item.text))
    if title == item.title and text == item.text:
        return item
    return item.model_copy(update={"title": title or item.title, "text": text})


def _explanation(item: Item, cand, decision: Decision, event_id: str) -> str:
    terms = ", ".join(cand.matched_terms) if cand.matched_terms else "none"
    return (
        f"indicator={cand.indicator.name} ({cand.indicator.id}); "
        f"keywords={terms}; cosine={cand.cosine:.3f}; "
        f"p_hit={decision.p_hit:.3f}; severity={decision.severity:.3f}; "
        f"confidence={decision.confidence:.3f}; backend={decision.backend}; "
        f"event={event_id}"
    )


def maybe_escalate(settings, item, cand, decision, action, reason):
    """Re-score an uncertain push with the optional local escalation backend."""
    if action != "digest" or not reason.startswith("uncertain"):
        return decision, action, reason
    kind = (settings.escalate or "").strip().lower()
    if kind not in {"ollama", "systemone"}:
        return decision, action, reason
    try:
        escalator = build_decider(
            kind,
            decider_url=settings.decider_url,
            decider_model=settings.decider_model,
            api_key=settings.decider_api_key,
            ollama_url=settings.ollama_url,
            ollama_model=settings.ollama_model,
            fallback=False,
        )
        upgraded = escalator.decide_many(item, [cand]).get(cand.indicator.id)
    except (DeciderError, ValueError) as exc:
        log.warning("escalation failed (%s)", exc)
        return decision, action, reason
    if upgraded is None:
        return decision, action, reason
    new_action, new_reason = decide_action(
        cand.indicator,
        p_hit=upgraded.p_hit,
        severity=upgraded.severity,
        cosine_value=cand.cosine,
        confidence=upgraded.confidence,
    )
    if new_action != "push":
        return decision, action, reason
    upgraded.backend = f"escalated:{upgraded.backend}"
    return upgraded, "push", f"escalated: {new_reason}"


def _assign_events(store: Store, decider: Decider, fresh: list[tuple[Item, list[float]]]) -> None:
    from wt.normalize import simhash as simhash_of

    for item, vector in fresh:
        event_id = item.id
        own_sim = simhash_of(item.document())
        for prior in store.recent_fingerprints():
            if prior["id"] == item.id or not prior["vector"]:
                continue
            if not same_event_signals(
                title=item.title,
                other_title=prior["title"],
                simhash_value=own_sim,
                other_simhash=prior["simhash"],
                vector=vector,
                other_vector=prior["vector"],
            ):
                continue
            if confirm_same_event(decider, item, prior["title"], prior["text"]):
                event_id = prior["event_id"] or prior["id"]
                break
        item.event_id = event_id
        store.set_event_id(item.id, event_id)


def _dedupe(
    store: Store, embedder: Embedder, items: list[Item]
) -> tuple[list[tuple[Item, list[float]]], int]:
    pending: list[tuple[Item, str | None, str, int]] = []
    seen_urls: set[str] = set()
    seen_content: set[str] = set()
    seen_sims: list[int] = []
    duplicates = 0
    for item in items:
        hashed_url = url_hash(item.url)
        hashed_body = content_hash(item.title, item.text)
        hashed_sim = simhash(item.document())
        if hashed_url and (hashed_url in seen_urls or store.url_exists(hashed_url)):
            duplicates += 1
            continue
        if hashed_body in seen_content or store.content_exists(hashed_body):
            duplicates += 1
            continue
        if any((prev ^ hashed_sim).bit_count() <= INGEST_HAMMING for prev in seen_sims):
            duplicates += 1
            continue
        if store.simhash_near(hashed_sim, max_hamming=INGEST_HAMMING):
            duplicates += 1
            continue
        if hashed_url:
            seen_urls.add(hashed_url)
        seen_content.add(hashed_body)
        seen_sims.append(hashed_sim)
        pending.append((item, hashed_url, hashed_body, hashed_sim))
    if not pending:
        return [], duplicates
    vectors = embedder.embed([item.document() for item, *_ in pending], kind="document")
    kept: list[tuple[Item, list[float]]] = []
    batch: list[list[float]] = []
    for (item, hashed_url, hashed_body, hashed_sim), vector in zip(pending, vectors):
        if store.cosine_near(vector, min_cosine=INGEST_COSINE):
            duplicates += 1
            continue
        if any(cosine(vector, previous) >= INGEST_COSINE for previous in batch):
            duplicates += 1
            continue
        store.upsert_item(
            item,
            url_hash=hashed_url,
            content_hash=hashed_body,
            simhash=hashed_sim,
            vector=vector,
        )
        batch.append(vector)
        kept.append((item, vector))
    return kept, duplicates


def run_live(settings: Settings) -> CycleReport:
    from wt.ingest import ingest_all

    store = Store(settings.db_path)
    try:
        sources = load_sources(settings.sources_path)
        items, errors = ingest_all(sources, store)
        report = run_items(settings, items, store)
        report.errors.extend(errors)
        return report
    finally:
        store.close()


def _reset_db(path: Path) -> None:
    if str(path) == ":memory:":
        return
    for candidate in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm")):
        if candidate.exists():
            candidate.unlink()


def run_demo(settings: Settings) -> CycleReport:
    _reset_db(settings.db_path)
    store = Store(settings.db_path)
    try:
        items = load_fixture_items(settings.fixtures_path)
        return run_items(settings, items, store)
    finally:
        store.close()
