"""Thumbs up/down labels, stored in SQLite and appended to a JSONL file."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from wt.store import Store

_LABELS = {
    "up": "hit",
    "hit": "hit",
    "yes": "hit",
    "down": "miss",
    "miss": "miss",
    "no": "miss",
}


def normalize_label(raw: str) -> str:
    key = (raw or "").strip().lower()
    if key not in _LABELS:
        raise ValueError(f"label must be up/down or hit/miss, not {raw!r}")
    return _LABELS[key]


def record_feedback(
    db_path: str | Path,
    match_id: str,
    label: str,
    *,
    labels_path: str | Path,
    note: str = "",
) -> dict:
    stored = normalize_label(label)
    store = Store(db_path)
    try:
        row = store.add_feedback(match_id, stored, note=note)
    finally:
        store.close()
    path = Path(labels_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def make_server(db_path: str | Path, labels_path: str | Path, host: str, port: int) -> ThreadingHTTPServer:
    database = str(db_path)
    labels = str(labels_path)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path in {"/", "/health"}:
                self._send(200, "watch-tower feedback server\n")
                return
            parts = [part for part in parsed.path.split("/") if part]
            if len(parts) != 2 or parts[0] != "feedback":
                self._send(404, "not found\n")
                return
            match_id = parts[1]
            label = (parse_qs(parsed.query).get("label") or [""])[0]
            try:
                row = record_feedback(database, match_id, label, labels_path=labels)
            except KeyError:
                self._send(404, f"unknown match {match_id}\n")
                return
            except ValueError as exc:
                self._send(400, f"{exc}\n")
                return
            body = (
                f"Recorded {row['label']} for {match_id} "
                f"({row['indicator_id']}).\n"
            )
            self._send(200, body)

        def log_message(self, fmt: str, *args) -> None:
            return

        def _send(self, status: int, body: str) -> None:
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("content-type", "text/plain; charset=utf-8")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return ThreadingHTTPServer((host, port), Handler)


def serve_feedback(db_path: str | Path, labels_path: str | Path, host: str, port: int) -> None:
    server = make_server(db_path, labels_path, host, port)
    bound = server.server_address
    print(f"feedback server on http://{bound[0]}:{bound[1]}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
