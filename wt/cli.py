"""Command line: demo, run, serve, feedback, digest, doctor."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

from wt import __version__
from wt.evalkit import evaluate, export_kev_jsonl
from wt.feedback import record_feedback, serve_feedback
from wt.pipeline import load_sources, run_demo, run_live
from wt.settings import Settings, project_root
from wt.store import Store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="wt",
        description="Watch Tower: match feeds against YAML indicators and alert on strong hits.",
    )
    parser.add_argument("--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)

    demo = sub.add_parser("demo", help="run the offline fixture pipeline (replaces the demo database)")
    demo.add_argument("--db", type=Path, default=None)
    demo.add_argument("--decider", default=None, help="default lexical; jev, kev, or ollama are optional")
    demo.add_argument("--embedder", default=None, help="default hashing; qwen is optional")
    demo.set_defaults(func=_cmd_demo)

    run = sub.add_parser("run", help="ingest once and alert")
    run.add_argument("--db", type=Path, default=None)
    run.add_argument("--decider", default=None, help="auto, jev, kev, lexical, or ollama")
    run.add_argument("--embedder", default=None, help="auto, qwen, or hashing")
    run.set_defaults(func=_cmd_run)

    serve = sub.add_parser("serve", help="repeat run on an interval")
    serve.add_argument("--interval", type=float, default=15.0, help="minutes between cycles")
    serve.add_argument("--decider", default=None)
    serve.add_argument("--embedder", default=None)
    serve.set_defaults(func=_cmd_serve)

    feedback = sub.add_parser("feedback", help="label a match hit or miss")
    feedback.add_argument("match_id")
    feedback.add_argument("label", help="up/down or hit/miss")
    feedback.add_argument("--db", type=Path, default=None)
    feedback.set_defaults(func=_cmd_feedback)

    server = sub.add_parser("feedback-server", help="HTTP thumbs up/down")
    server.add_argument("--host", default="127.0.0.1")
    server.add_argument("--port", type=int, default=8787)
    server.add_argument("--db", type=Path, default=None)
    server.set_defaults(func=_cmd_feedback_server)

    digest = sub.add_parser("digest", help="print digest-tier matches and advance the cursor")
    digest.add_argument("--db", type=Path, default=None)
    digest.set_defaults(func=_cmd_digest)

    sub.add_parser("doctor", help="show what this machine will use").set_defaults(func=_cmd_doctor)

    desk = sub.add_parser("desk", help="open the local desk (API plus the browser UI)")
    desk.add_argument("--port", type=int, default=47221, help="browser port")
    desk.add_argument("--api-port", type=int, default=8641)
    desk.add_argument("--lan", action="store_true", help="also listen on the LAN; Shortcuts must send the rip token")
    desk.set_defaults(func=_cmd_desk)

    rip = sub.add_parser("rip", help="rip one URL through the catalog pipeline")
    rip.add_argument("url")
    rip.add_argument("--note", default="")
    rip.add_argument("--render", action="store_true", help="allow Playwright when a page is too thin")
    rip.add_argument("--paste-file", type=Path, default=None)
    rip.add_argument("--via", default="", help="refused: jina is not enabled")
    rip.add_argument("--snapshot", action="store_true", help="refused: monolith snapshot is not enabled")
    rip.add_argument("--json", action="store_true")
    rip.add_argument("--db", type=Path, default=None)
    rip.set_defaults(func=_cmd_rip)

    subscribe = sub.add_parser("subscribe", help="find a feed for a URL and save it in sources.local.yaml")
    subscribe.add_argument("url")
    subscribe.add_argument("--pick", type=int, default=None)
    subscribe.add_argument("--selector", default="")
    subscribe.add_argument("--interval", type=int, default=60)
    subscribe.add_argument("--cdio", action="store_true", help="refused: changedetection.io is not enabled")
    subscribe.add_argument("--dry-run", action="store_true")
    subscribe.add_argument("--db", type=Path, default=None)
    subscribe.set_defaults(func=_cmd_subscribe)

    mark = sub.add_parser("bookmarklet", help="print the rip bookmarklet for the desk port")
    mark.add_argument("--port", type=int, default=47221)
    mark.set_defaults(func=_cmd_bookmarklet)

    inbox = sub.add_parser("inbox", help="accept one URL from a private ntfy topic")
    inbox.add_argument("--once", action="store_true", help="read one message from stdin")
    inbox.set_defaults(func=_cmd_inbox)

    token = sub.add_parser("rip-token", help="print or create the Shortcut bearer token")
    token.set_defaults(func=_cmd_rip_token)

    sources = sub.add_parser("sources", help="list curated feeds and local subscriptions")
    sources.set_defaults(func=_cmd_sources)

    export = sub.add_parser("export-kev", help="write Kev fine-tune JSONL from feedback")
    export.add_argument("--out", type=Path, default=Path("data/kev-train.jsonl"))
    export.set_defaults(func=_cmd_export)

    sub.add_parser("eval", help="score the lexical decider on eval/labels.jsonl").set_defaults(
        func=_cmd_eval
    )
    extract = sub.add_parser("extract", help="drain the high-value extraction queue")
    extract.add_argument("--limit", type=int, default=20)
    extract.set_defaults(func=_cmd_extract)
    sub.add_parser(
        "extract-eval", help="score the stub proposer on eval/extraction_gold.json"
    ).set_defaults(func=_cmd_extract_eval)
    sub.add_parser("graph-rebuild", help="rebuild the LadybugDB projection from SQLite").set_defaults(
        func=_cmd_graph
    )

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    return args.func(args)


def _apply_overrides(settings: Settings, args) -> None:
    if getattr(args, "db", None):
        settings.db_path = args.db
    if getattr(args, "decider", None):
        settings.decider = args.decider
    if getattr(args, "embedder", None):
        settings.embedder = args.embedder


def _print_summary(report) -> None:
    print(
        f"\n{report.items_new} new items, {report.duplicates} duplicates, "
        f"{len(report.pushes)} push, {len(report.digests)} digest "
        f"(embedder={report.embedder}, decider={report.backend}, vectors={report.vector_backend})",
        flush=True,
    )
    for error in report.errors:
        print(f"source error: {error}", file=sys.stderr)


def _cmd_demo(args) -> int:
    root = project_root()
    settings = Settings.for_demo(root, db_path=args.db)
    _apply_overrides(settings, args)
    report = run_demo(settings)
    _print_summary(report)
    if not report.pushes:
        print("demo produced no push alerts", file=sys.stderr)
        return 1
    return 0


def _cmd_run(args) -> int:
    settings = Settings.from_env()
    _apply_overrides(settings, args)
    report = run_live(settings)
    _print_summary(report)
    return 0


def _cmd_serve(args) -> int:
    settings = Settings.from_env()
    _apply_overrides(settings, args)
    interval = max(0.5, args.interval) * 60.0
    print(f"watch-tower {__version__} serving every {args.interval:g} min. Ctrl-C to stop.", flush=True)
    while True:
        try:
            report = run_live(settings)
            _print_summary(report)
        except KeyboardInterrupt:
            print("\nstopped", flush=True)
            return 0
        except Exception:
            logging.getLogger("wt.cli").exception("cycle failed")
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            print("\nstopped", flush=True)
            return 0


def _cmd_feedback(args) -> int:
    settings = Settings.from_env()
    _apply_overrides(settings, args)
    try:
        row = record_feedback(
            settings.db_path,
            args.match_id,
            args.label,
            labels_path=settings.labels_path,
        )
    except KeyError:
        print(f"unknown match {args.match_id}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"recorded {row['label']} for {args.match_id}")
    return 0


def _cmd_feedback_server(args) -> int:
    settings = Settings.from_env()
    _apply_overrides(settings, args)
    serve_feedback(settings.db_path, settings.labels_path, args.host, args.port)
    return 0


def _cmd_digest(args) -> int:
    settings = Settings.from_env()
    _apply_overrides(settings, args)
    store = Store(settings.db_path)
    try:
        since = store.meta_get("digest_cursor")
        rows = store.digest_since(since)
        if not rows:
            print("digest: nothing new")
            return 0
        print(f"digest: {len(rows)} match(es)")
        for row in rows:
            print(
                f"- {row['indicator_id']}  p_hit={row['p_hit']:.2f}  "
                f"severity={row['severity']:.2f}  item={row['item_id']}"
            )
        store.meta_set("digest_cursor", rows[-1]["created_at"])
    finally:
        store.close()
    return 0


def _cmd_desk(args) -> int:
    import subprocess

    root = project_root()
    web = root / "web"
    api_host = "0.0.0.0" if args.lan else "127.0.0.1"
    api = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "wt.deskapp:app",
            "--host",
            api_host,
            "--port",
            str(args.api_port),
        ],
        cwd=root,
    )
    ui = None
    if (web / "package.json").is_file():
        ui = subprocess.Popen(
            ["npm", "run", "dev", "--", "--port", str(args.port), "--hostname", "0.0.0.0"],
            cwd=web,
        )
    print(f"Watch Tower desk  http://127.0.0.1:{args.port}", flush=True)
    print(f"API               http://{api_host}:{args.api_port}", flush=True)
    if args.lan:
        print("LAN rips need Authorization: Bearer from `wt rip-token`.", flush=True)
    try:
        api.wait()
    except KeyboardInterrupt:
        print("\nstopped", flush=True)
    finally:
        api.terminate()
        if ui is not None:
            ui.terminate()
    return 0


def bookmarklet_source(port: int = 47221) -> str:
    return (
        "javascript:(()=>{window.open('http://127.0.0.1:"
        + str(port)
        + "/rip?url='+encodeURIComponent(location.href)+'&title='+encodeURIComponent(document.title)"
        + "+'&sel='+encodeURIComponent(String(getSelection()).slice(0,4000)),'wt','width=520,height=720')})()"
    )


def _cmd_bookmarklet(args) -> int:
    print(bookmarklet_source(args.port))
    return 0


def _cmd_rip_token(args) -> int:
    path = project_root() / "data" / "rip.token"
    return _print_token(path)


def _print_token(path: Path) -> int:
    import secrets

    env = os.environ.get("WT_RIP_TOKEN", "").strip()
    if env:
        print(env)
        return 0
    if path.is_file():
        print(path.read_text(encoding="utf-8").strip())
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(24)
    path.write_text(token + "\n", encoding="utf-8")
    print(token)
    return 0


def _cmd_rip(args) -> int:
    if args.via or args.snapshot:
        print("jina and snapshot are not enabled. Paste the text or rip the URL directly.", file=sys.stderr)
        return 2
    settings = Settings.from_env()
    _apply_overrides(settings, args)
    selection = ""
    if args.paste_file:
        selection = args.paste_file.read_text(encoding="utf-8")
    from wt.rip.pipeline import RipError, commit_rip
    from wt.store import Store

    store = Store(settings.db_path)
    try:
        payload = commit_rip(
            settings,
            store,
            args.url,
            note=args.note,
            selection=selection,
            allow_render=args.render,
        )
    except RipError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        store.close()
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"{payload.get('title') or '(no title)'}")
        print(
            f"method={payload.get('method')} words={payload.get('words')} "
            f"pushes={payload.get('pushes')} id={payload.get('id') or '-'}"
        )
        for warning in payload.get("warnings") or []:
            print(f"warning: {warning}")
    if not payload.get("stored"):
        return 1
    return 0


def _cmd_subscribe(args) -> int:
    if args.cdio:
        print("changedetection.io is not enabled.", file=sys.stderr)
        return 2
    settings = Settings.from_env()
    _apply_overrides(settings, args)
    from wt.rip.discover import discover
    from wt.rip.pipeline import RipError, commit_subscribe
    from wt.rip.ssrf import SsrfError

    if args.dry_run:
        try:
            found = discover(args.url)
        except SsrfError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        if not found.candidates and found.selector is None:
            print("no feed")
            for warning in found.warnings:
                print(f"warning: {warning}")
            return 1
        for index, row in enumerate(found.candidates):
            print(f"[{index}] {row.n_items} items  {row.title}  {row.url}")
        if found.selector is not None:
            print(
                f"selector {found.selector.selector}  {len(found.selector.items)} items"
            )
        return 0
    try:
        payload = commit_subscribe(
            settings,
            args.url,
            selector=args.selector,
            interval_min=args.interval,
            via="cli",
            pick=args.pick,
        )
    except RipError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    discovery = payload.get("discovery") or {}
    found = bool(payload.get("saved") or discovery.get("candidates") or discovery.get("selector"))
    return 0 if found else 1


def _cmd_inbox(args) -> int:
    from wt.rip.inbox import accept_inbox_message, iter_ntfy

    if args.once:
        message = sys.stdin.read()
        accepted = accept_inbox_message(message)
        if accepted is None:
            print("ignored")
            return 0
        print(json.dumps(accepted))
        return 0
    topic = os.environ.get("WT_NTFY_INBOX", "").strip()
    if not topic:
        print("Set WT_NTFY_INBOX to a private ntfy topic URL.", file=sys.stderr)
        return 1
    from wt.rip.pipeline import RipError, commit_rip, commit_subscribe

    settings = Settings.from_env()
    for message in iter_ntfy(topic):
        accepted = accept_inbox_message(message)
        if accepted is None:
            continue
        store = Store(settings.db_path)
        try:
            if accepted["mode"] == "subscribe":
                payload = commit_subscribe(settings, accepted["url"], via="ntfy")
            else:
                payload = commit_rip(settings, store, accepted["url"])
        except RipError as exc:
            print(str(exc), file=sys.stderr)
            continue
        finally:
            store.close()
        print(json.dumps({"url": accepted["url"], "mode": accepted["mode"], "id": payload.get("id", "")}))
    return 0


def _cmd_sources(args) -> int:
    settings = Settings.from_env()
    data = load_sources(settings.sources_path)
    for section in ("rss", "selector"):
        feeds = (data.get(section) or {}).get("feeds") or []
        for feed in feeds:
            if not isinstance(feed, dict):
                continue
            via = feed.get("added_via") or "curated"
            print(f"{section}:{feed.get('id')}  {via}  {feed.get('url')}")
    return 0


def _cmd_doctor(args) -> int:
    root = project_root()
    settings = Settings.from_env(root)
    print(f"watch-tower {__version__}")
    print(f"root: {root}")
    print(f"db: {settings.db_path}")
    print(f"indicators: {settings.indicators_dir}")
    print(f"embedder setting: {settings.embedder} ({settings.embedder_model})")
    print(
        f"decider setting: {settings.decider} "
        f"(kev {settings.decider_url}, model={settings.decider_model}; "
        f"jev {settings.jev_base_url}, model={settings.jev_model})"
    )
    print(f"jev key: {'set' if settings.jev_api_key else 'missing'}")
    print(
        f"jev price: ${settings.jev_usd_per_mtok}/MTok input, output free; "
        f"monthly cap ${settings.jev_monthly_usd:g}; daily warning ${settings.jev_daily_warn_usd:g}"
    )
    print(
        "default run: Jev at api.typesafe.ai when JEV_API_KEY is set, otherwise lexical. "
        "Kev is not contacted unless WT_DECIDER=kev or WT_LOCAL_DECIDER=kev."
    )
    print(f"local decider: {settings.local_decider}")
    print(f"deep extract: {'on ' + settings.ollama_model if settings.deep_extract else 'off'}")
    print("kev release: 1.0 checkpoints kev-0.8b, kev-4b, kev-9b, kev-27b (pin @v1.0)")
    print("kev server: python -m kev.serve uses MLX on Apple Silicon; set KEV_DATE_FACTS=1")
    print("kev size: kev-0.8b@v1.0 on any Apple Silicon Mac; kev-4b@v1.0 wants 32 GB")
    print("kev strength: 0.8b automates ~14% of new-source decisions at a 5% error budget; 4b and up about 52-69%")
    print("kev state: title + 300 words (validated context is 8192 tokens)")
    print(f"escalate: {settings.escalate or 'off'}")
    print(f"injection guard: {settings.injection_guard}")
    print(f"full text: {settings.fetch_fulltext}")
    try:
        from wt.indicators import load_indicators

        indicators = load_indicators(settings.indicators_dir)
        print(f"indicators loaded: {len(indicators)} ({', '.join(i.id for i in indicators)})")
    except Exception as exc:
        print(f"indicators: FAILED {exc}")
    store = Store(":memory:")
    print(f"vector backend: {store.vector_backend}")
    store.close()
    from wt.decider import probe_ollama, probe_systemone
    from wt.jev import probe_jev

    jev_ok, jev_detail = probe_jev(settings.jev_base_url, settings.jev_api_key)
    print(f"jev: {'OK' if jev_ok else 'FAIL'} ({jev_detail})")
    print(
        f"systemone reachable: {probe_systemone(settings.decider_url, api_key=settings.decider_api_key)}"
    )
    print(f"ollama reachable: {probe_ollama(settings.ollama_url)}")
    try:
        sources = load_sources(settings.sources_path)
        print(f"sources file: {settings.sources_path} keys={', '.join(sources)}")
    except Exception as exc:
        print(f"sources: FAILED {exc}")
    return 0


def _cmd_eval(args) -> int:
    root = project_root()
    metrics = evaluate(
        indicators_dir=root / "config" / "indicators",
        fixtures_path=root / "fixtures" / "items.json",
        labels_path=root / "eval" / "labels.jsonl",
    )
    print(
        f"n={metrics['n']} precision={metrics['precision']:.2f} "
        f"recall={metrics['recall']:.2f} brier={metrics['brier']:.3f} "
        f"tp={metrics['tp']} fp={metrics['fp']} fn={metrics['fn']} tn={metrics['tn']}"
    )
    for row in metrics["rows"]:
        mark = "ok" if row["pred_push"] == row["truth"] else "MISS"
        print(
            f"  {mark} {row['indicator_id']} {row['label']} "
            f"p={row['p_hit']:.2f} sev={row['severity']:.2f} -> {row['action']}"
        )
    return 0 if metrics["fp"] == 0 and metrics["tp"] > 0 else 1


def _cmd_extract(args) -> int:
    settings = Settings.from_env()
    store = Store(settings.db_path)
    ask = None
    if settings.jev_api_key:
        from wt.jev import JevDecider

        ask = JevDecider(
            settings.jev_base_url,
            api_key=settings.jev_api_key,
            model=settings.jev_model,
            store=store,
            usd_per_mtok=settings.jev_usd_per_mtok,
            monthly_usd=settings.jev_monthly_usd,
            daily_warn_usd=settings.jev_daily_warn_usd,
            prompt_version=settings.jev_prompt_version,
            allow_private=settings.jev_allow_private,
        ).ask
    from wt.extract_graph.deep import build_deep_proposer
    from wt.extract_graph.propose import build_proposer
    from wt.extract_graph.queue import drain

    deep = build_deep_proposer(
        settings.deep_extract,
        model_id=settings.ollama_model,
        model_url=settings.ollama_url,
    )
    done = drain(
        store,
        ask=ask,
        proposer=build_proposer(settings.extractor),
        deep=deep,
        limit=max(1, args.limit),
    )
    print(f"extracted {done}")
    store.close()
    return 0


def _cmd_graph(_args) -> int:
    from wt.extract_graph.graph import load_graph

    settings = Settings.from_env()
    store = Store(settings.db_path)
    snap = load_graph(store)
    print(
        f"backend={snap.get('backend')} nodes={len(snap['nodes'])} edges={len(snap['edges'])}"
    )
    store.close()
    return 0


def _cmd_extract_eval(_args) -> int:
    from wt.extract_graph.evalkit import gold_spans, score_file, span_scores
    from wt.extract_graph.propose import StubProposer
    from wt.extract_graph.run import FIXTURE_TEXT

    path = project_root() / "eval" / "extraction_gold.json"
    proposal = StubProposer().propose(FIXTURE_TEXT)
    predicted = {(row.start, row.end, row.type) for row in proposal.mentions}
    metrics = score_file(path, predicted)
    print(
        f"spans={metrics['predicted']} gold={metrics['gold']} "
        f"precision={metrics['precision']:.2f} recall={metrics['recall']:.2f} "
        f"tp={metrics['tp']} fp={metrics['fp']} fn={metrics['fn']}"
    )
    del gold_spans, span_scores
    return 0 if metrics["tp"] > 0 else 1


def _cmd_export(args) -> int:
    settings = Settings.from_env()
    rows = export_kev_jsonl(settings.labels_path, settings.indicators_dir)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"wrote {len(rows)} rows to {args.out}")
    return 0
