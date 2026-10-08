"""Command line: demo, run, serve, feedback, digest, doctor."""

from __future__ import annotations

import argparse
import json
import logging
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
    demo.add_argument("--decider", default=None, help="default lexical; systemone or ollama are optional")
    demo.add_argument("--embedder", default=None, help="default hashing; qwen is optional")
    demo.set_defaults(func=_cmd_demo)

    run = sub.add_parser("run", help="ingest once and alert")
    run.add_argument("--db", type=Path, default=None)
    run.add_argument("--decider", default=None, help="auto, systemone, ollama, or lexical")
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

    export = sub.add_parser("export-kev", help="write Kev fine-tune JSONL from feedback")
    export.add_argument("--out", type=Path, default=Path("data/kev-train.jsonl"))
    export.set_defaults(func=_cmd_export)

    sub.add_parser("eval", help="score the lexical decider on eval/labels.jsonl").set_defaults(
        func=_cmd_eval
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


def _cmd_doctor(args) -> int:
    root = project_root()
    settings = Settings.from_env(root)
    print(f"watch-tower {__version__}")
    print(f"root: {root}")
    print(f"db: {settings.db_path}")
    print(f"indicators: {settings.indicators_dir}")
    print(f"embedder setting: {settings.embedder} ({settings.embedder_model})")
    print(f"decider setting: {settings.decider} ({settings.decider_url}, model={settings.decider_model})")
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

    print(f"systemone reachable: {probe_systemone(settings.decider_url)}")
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


def _cmd_export(args) -> int:
    settings = Settings.from_env()
    rows = export_kev_jsonl(settings.labels_path, settings.indicators_dir)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"wrote {len(rows)} rows to {args.out}")
    return 0
