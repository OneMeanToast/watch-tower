"""SQLite storage. Vectors are float32 blobs queried with sqlite-vec when it loads."""

from __future__ import annotations

import json
import logging
import sqlite3
import struct
import time
from datetime import datetime, timedelta
from pathlib import Path

from wt.embed import cosine
from wt.models import Item, MatchResult
from wt.policy import PriorAlert
from wt.text import utcnow

log = logging.getLogger("wt.store")

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS items (
  id TEXT PRIMARY KEY,
  url TEXT,
  url_hash TEXT,
  content_hash TEXT,
  source TEXT NOT NULL,
  feed_id TEXT NOT NULL DEFAULT '',
  ts TEXT NOT NULL,
  title TEXT NOT NULL,
  text TEXT NOT NULL,
  lang TEXT NOT NULL,
  authors TEXT NOT NULL DEFAULT '[]',
  simhash TEXT NOT NULL,
  event_id TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  rip_method TEXT NOT NULL DEFAULT '',
  warnings_json TEXT NOT NULL DEFAULT '[]',
  user_note TEXT NOT NULL DEFAULT '',
  source_type TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_items_url_hash
  ON items(url_hash) WHERE url_hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_items_content ON items(content_hash);
CREATE INDEX IF NOT EXISTS idx_items_ts ON items(ts);
CREATE TABLE IF NOT EXISTS embeddings (
  entity_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  label TEXT NOT NULL DEFAULT '',
  dim INTEGER NOT NULL,
  vector BLOB NOT NULL,
  PRIMARY KEY (entity_id, kind, label)
);
CREATE TABLE IF NOT EXISTS matches (
  id TEXT PRIMARY KEY,
  item_id TEXT NOT NULL,
  indicator_id TEXT NOT NULL,
  cosine REAL NOT NULL,
  keyword_hit INTEGER NOT NULL,
  p_hit REAL NOT NULL,
  severity REAL NOT NULL,
  category TEXT,
  confidence REAL NOT NULL,
  action TEXT NOT NULL,
  reason TEXT NOT NULL,
  backend TEXT NOT NULL,
  answers_json TEXT NOT NULL DEFAULT '{}',
  explanation TEXT NOT NULL DEFAULT '',
  snippets_json TEXT NOT NULL DEFAULT '[]',
  trace_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  match_id TEXT NOT NULL,
  indicator_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  body TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS feedback (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  match_id TEXT NOT NULL,
  label TEXT NOT NULL CHECK (label IN ('hit', 'miss')),
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS intake (
  item_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  method TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rip_jobs (
  id TEXT PRIMARY KEY,
  url TEXT NOT NULL,
  mode TEXT NOT NULL,
  status TEXT NOT NULL,
  result_item_id TEXT NOT NULL DEFAULT '',
  error TEXT NOT NULL DEFAULT '',
  result_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jev_usage (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL,
  run_id TEXT NOT NULL,
  input_tokens INTEGER NOT NULL,
  output_tokens INTEGER NOT NULL,
  usd REAL NOT NULL,
  cached INTEGER NOT NULL DEFAULT 0,
  calls INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_jev_usage_created ON jev_usage(created_at);
CREATE TABLE IF NOT EXISTS jev_cache (
  key TEXT PRIMARY KEY,
  indicator_id TEXT NOT NULL,
  answer_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
"""


def pack_f32(vector: list[float]) -> bytes:
    return struct.pack(f"<{len(vector)}f", *[float(v) for v in vector])


def unpack_f32(blob: bytes) -> list[float]:
    count = len(blob) // 4
    return list(struct.unpack(f"<{count}f", blob[: count * 4]))


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        if str(self.path) != ":memory:":
            self.conn.execute("PRAGMA journal_mode = WAL")
        self.has_vec, self.vec_version = self._load_vec()
        self.conn.executescript(SCHEMA)
        self._ensure_columns()
        self.conn.commit()

    def _load_vec(self) -> tuple[bool, str]:
        try:
            import sqlite_vec
        except ImportError:
            log.info("sqlite-vec is not installed; cosine runs in Python")
            return False, ""
        try:
            self.conn.enable_load_extension(True)
            sqlite_vec.load(self.conn)
            self.conn.enable_load_extension(False)
            version = self.conn.execute("SELECT vec_version()").fetchone()[0]
            return True, str(version)
        except Exception as exc:
            log.info("sqlite-vec did not load (%s); cosine runs in Python", exc)
            return False, ""

    @property
    def vector_backend(self) -> str:
        if self.has_vec:
            return f"sqlite-vec {self.vec_version}"
        return "python"

    def _ensure_columns(self) -> None:
        item_cols = {row[1] for row in self.conn.execute("PRAGMA table_info(items)")}
        if "event_id" not in item_cols:
            self.conn.execute("ALTER TABLE items ADD COLUMN event_id TEXT NOT NULL DEFAULT ''")
        for name, ddl in (
            ("rip_method", "TEXT NOT NULL DEFAULT ''"),
            ("warnings_json", "TEXT NOT NULL DEFAULT '[]'"),
            ("user_note", "TEXT NOT NULL DEFAULT ''"),
            ("source_type", "TEXT NOT NULL DEFAULT ''"),
        ):
            if name not in item_cols:
                self.conn.execute(f"ALTER TABLE items ADD COLUMN {name} {ddl}")
        match_cols = {row[1] for row in self.conn.execute("PRAGMA table_info(matches)")}
        if "explanation" not in match_cols:
            self.conn.execute(
                "ALTER TABLE matches ADD COLUMN explanation TEXT NOT NULL DEFAULT ''"
            )
        if "snippets_json" not in match_cols:
            self.conn.execute(
                "ALTER TABLE matches ADD COLUMN snippets_json TEXT NOT NULL DEFAULT '[]'"
            )
        if "trace_json" not in match_cols:
            self.conn.execute(
                "ALTER TABLE matches ADD COLUMN trace_json TEXT NOT NULL DEFAULT '[]'"
            )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS intake (
              item_id TEXT PRIMARY KEY,
              kind TEXT NOT NULL,
              method TEXT NOT NULL,
              note TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rip_jobs (
              id TEXT PRIMARY KEY,
              url TEXT NOT NULL,
              mode TEXT NOT NULL,
              status TEXT NOT NULL,
              result_item_id TEXT NOT NULL DEFAULT '',
              error TEXT NOT NULL DEFAULT '',
              result_json TEXT NOT NULL DEFAULT '{}',
              created_at TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS jev_usage (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              created_at TEXT NOT NULL,
              run_id TEXT NOT NULL,
              input_tokens INTEGER NOT NULL,
              output_tokens INTEGER NOT NULL,
              usd REAL NOT NULL,
              cached INTEGER NOT NULL DEFAULT 0,
              calls INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_jev_usage_created ON jev_usage(created_at)"
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS jev_cache (
              key TEXT PRIMARY KEY,
              indicator_id TEXT NOT NULL,
              answer_json TEXT NOT NULL,
              created_at TEXT NOT NULL
            )
            """
        )

    def close(self) -> None:
        self.conn.close()

    def meta_get(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return None if row is None else str(row["value"])

    def meta_set(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self.conn.commit()

    def wait_rate_limit(self, key: str, seconds: float) -> None:
        last = self.meta_get(f"ratelimit:{key}")
        if last:
            delay = seconds - (time.time() - float(last))
            if delay > 0:
                time.sleep(delay)
        self.meta_set(f"ratelimit:{key}", str(time.time()))

    def url_exists(self, url_hash: str | None) -> bool:
        if not url_hash:
            return False
        row = self.conn.execute(
            "SELECT 1 FROM items WHERE url_hash = ?", (url_hash,)
        ).fetchone()
        return row is not None

    def content_exists(self, content_hash: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM items WHERE content_hash = ?", (content_hash,)
        ).fetchone()
        return row is not None

    def simhash_near(self, simhash: int, *, max_hamming: int, limit: int = 5000) -> bool:
        rows = self.conn.execute(
            "SELECT simhash FROM items ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()
        for row in rows:
            if (int(row["simhash"], 16) ^ simhash).bit_count() <= max_hamming:
                return True
        return False

    def cosine_near(self, vector: list[float], *, min_cosine: float, limit: int = 500) -> bool:
        for _item_id, score in self.nearest_items(vector, limit=limit):
            if score >= min_cosine:
                return True
        return False

    def nearest_items(self, vector: list[float], *, limit: int = 500) -> list[tuple[str, float]]:
        blob = pack_f32(vector)
        if self.has_vec:
            rows = self.conn.execute(
                """
                SELECT e.entity_id AS entity_id,
                       1 - vec_distance_cosine(e.vector, ?) AS cosine
                FROM embeddings e
                JOIN items i ON i.id = e.entity_id
                WHERE e.kind = 'item' AND e.label = ''
                ORDER BY i.ts DESC
                LIMIT ?
                """,
                (blob, limit),
            ).fetchall()
            return [(str(row["entity_id"]), float(row["cosine"])) for row in rows]
        rows = self.conn.execute(
            """
            SELECT e.entity_id AS entity_id, e.vector AS vector
            FROM embeddings e
            JOIN items i ON i.id = e.entity_id
            WHERE e.kind = 'item' AND e.label = ''
            ORDER BY i.ts DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        scored = [
            (str(row["entity_id"]), cosine(unpack_f32(row["vector"]), vector)) for row in rows
        ]
        return scored

    def sql_cosine(self, left: list[float], right: list[float]) -> float:
        """Cosine similarity. Uses sqlite-vec's distance function when the extension is loaded."""
        if len(left) != len(right):
            raise ValueError("vectors have different dimensions")
        blob_l, blob_r = pack_f32(left), pack_f32(right)
        if self.has_vec:
            distance = self.conn.execute(
                "SELECT vec_distance_cosine(?, ?)", (blob_l, blob_r)
            ).fetchone()[0]
            return 1.0 - float(distance)
        return cosine(left, right)

    def upsert_item(
        self,
        item: Item,
        *,
        url_hash: str | None,
        content_hash: str,
        simhash: int,
        vector: list[float],
    ) -> None:
        now = utcnow().isoformat()
        self.conn.execute(
            """
            INSERT INTO items (
              id, url, url_hash, content_hash, source, feed_id, ts, title, text,
              lang, authors, simhash, event_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              title = excluded.title,
              text = excluded.text,
              ts = excluded.ts,
              simhash = excluded.simhash,
              content_hash = excluded.content_hash,
              event_id = excluded.event_id
            """,
            (
                item.id,
                item.url,
                url_hash,
                content_hash,
                item.source,
                item.feed_id,
                item.ts.isoformat(),
                item.title,
                item.text,
                item.lang,
                json.dumps(item.authors),
                f"{simhash:016x}",
                item.event_id or "",
                now,
            ),
        )
        self.upsert_embedding(item.id, "item", "", vector)
        self.conn.commit()

    def upsert_embedding(self, entity_id: str, kind: str, label: str, vector: list[float]) -> None:
        existing = self.meta_get("embed_dim")
        if existing and int(existing) != len(vector):
            raise RuntimeError(
                f"embedding dimension changed from {existing} to {len(vector)}. "
                "Delete the database before switching embedders."
            )
        if not existing:
            self.conn.execute(
                "INSERT INTO meta(key, value) VALUES('embed_dim', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(len(vector)),),
            )
        self.conn.execute(
            """
            INSERT INTO embeddings (entity_id, kind, label, dim, vector)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(entity_id, kind, label) DO UPDATE SET
              dim = excluded.dim,
              vector = excluded.vector
            """,
            (entity_id, kind, label, len(vector), pack_f32(vector)),
        )

    def max_indicator_cosine(self, vector: list[float], indicator_id: str) -> float:
        blob = pack_f32(vector)
        if self.has_vec:
            row = self.conn.execute(
                """
                SELECT MAX(1 - vec_distance_cosine(vector, ?)) AS cosine
                FROM embeddings
                WHERE entity_id = ?
                  AND kind = 'indicator'
                  AND (label = '' OR label LIKE 'pos:%')
                """,
                (blob, indicator_id),
            ).fetchone()
            if row is None or row["cosine"] is None:
                return 0.0
            return float(row["cosine"])
        rows = self.conn.execute(
            """
            SELECT vector FROM embeddings
            WHERE entity_id = ? AND kind = 'indicator'
              AND (label = '' OR label LIKE 'pos:%')
            """,
            (indicator_id,),
        ).fetchall()
        if not rows:
            return 0.0
        return max(cosine(unpack_f32(row["vector"]), vector) for row in rows)

    def commit(self) -> None:
        self.conn.commit()

    def save_match(self, result: MatchResult, answers: dict) -> None:
        self.conn.execute(
            """
            INSERT INTO matches (
              id, item_id, indicator_id, cosine, keyword_hit, p_hit, severity,
              category, confidence, action, reason, backend, answers_json,
              explanation, snippets_json, trace_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              cosine = excluded.cosine,
              keyword_hit = excluded.keyword_hit,
              p_hit = excluded.p_hit,
              severity = excluded.severity,
              category = excluded.category,
              confidence = excluded.confidence,
              action = excluded.action,
              reason = excluded.reason,
              backend = excluded.backend,
              answers_json = excluded.answers_json,
              explanation = excluded.explanation,
              snippets_json = excluded.snippets_json,
              trace_json = excluded.trace_json,
              created_at = excluded.created_at
            """,
            (
                result.match_id,
                result.item_id,
                result.indicator_id,
                result.cosine,
                1 if result.keyword_hit else 0,
                result.p_hit,
                result.severity,
                result.category,
                result.confidence,
                result.action,
                result.reason,
                result.backend,
                json.dumps(answers),
                result.explanation,
                json.dumps(result.snippets),
                json.dumps(result.trace),
                utcnow().isoformat(),
            ),
        )
        self.conn.commit()

    def set_event_id(self, item_id: str, event_id: str) -> None:
        self.conn.execute("UPDATE items SET event_id = ? WHERE id = ?", (event_id, item_id))
        self.conn.commit()

    def update_item_text(self, item_id: str, text: str) -> None:
        self.conn.execute("UPDATE items SET text = ? WHERE id = ?", (text, item_id))
        self.conn.commit()

    def event_has_push(self, event_id: str, indicator_id: str) -> bool:
        if not event_id:
            return False
        row = self.conn.execute(
            """
            SELECT 1
            FROM alerts a
            JOIN matches m ON m.id = a.match_id
            JOIN items i ON i.id = m.item_id
            WHERE a.kind = 'push' AND a.indicator_id = ? AND i.event_id = ?
            LIMIT 1
            """,
            (indicator_id, event_id),
        ).fetchone()
        return row is not None

    def recent_fingerprints(self, *, limit: int = 400) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT i.id AS id, i.title AS title, i.text AS text, i.simhash AS simhash,
                   i.event_id AS event_id, e.vector AS vector
            FROM items i
            LEFT JOIN embeddings e
              ON e.entity_id = i.id AND e.kind = 'item' AND e.label = ''
            ORDER BY i.ts DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        found = []
        for row in rows:
            vector = [] if row["vector"] is None else unpack_f32(row["vector"])
            found.append(
                {
                    "id": str(row["id"]),
                    "title": str(row["title"]),
                    "text": str(row["text"]),
                    "simhash": int(row["simhash"], 16),
                    "event_id": str(row["event_id"] or row["id"]),
                    "vector": vector,
                }
            )
        return found

    def list_matches(
        self,
        *,
        action: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 300,
    ) -> list[sqlite3.Row]:
        sql = """
            SELECT m.*, i.title AS item_title, i.url AS item_url, i.source AS item_source,
                   i.feed_id AS item_feed, i.text AS item_text, i.event_id AS item_event,
                   i.ts AS item_ts
            FROM matches m
            JOIN items i ON i.id = m.item_id
        """
        clauses: list[str] = []
        params: list = []
        if action:
            clauses.append("m.action = ?")
            params.append(action)
        if since:
            clauses.append("i.ts >= ?")
            params.append(since)
        if until:
            clauses.append("i.ts <= ?")
            params.append(until)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY i.ts DESC LIMIT ?"
        params.append(limit)
        return self.conn.execute(sql, params).fetchall()

    def item_stats(self, *, since: str | None = None, until: str | None = None) -> list[dict]:
        sql = """
            SELECT source, feed_id, COUNT(*) AS items, MIN(ts) AS oldest, MAX(ts) AS newest
            FROM items
        """
        clauses: list[str] = []
        params: list = []
        if since:
            clauses.append("ts >= ?")
            params.append(since)
        if until:
            clauses.append("ts <= ?")
            params.append(until)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " GROUP BY source, feed_id"
        rows = self.conn.execute(sql, params).fetchall()
        return [
            {
                "source": str(row["source"]),
                "feed_id": str(row["feed_id"] or ""),
                "items": int(row["items"]),
                "oldest": str(row["oldest"] or ""),
                "newest": str(row["newest"] or ""),
            }
            for row in rows
        ]

    def list_items(
        self,
        *,
        source: str | None = None,
        feed_id: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 40,
    ) -> list[dict]:
        sql = "SELECT id, title, url, source, feed_id, ts, text FROM items"
        clauses: list[str] = []
        params: list = []
        if source:
            clauses.append("source = ?")
            params.append(source)
        if feed_id is not None:
            clauses.append("feed_id = ?")
            params.append(feed_id)
        if since:
            clauses.append("ts >= ?")
            params.append(since)
        if until:
            clauses.append("ts <= ?")
            params.append(until)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY ts DESC LIMIT ?"
        params.append(limit)
        rows = self.conn.execute(sql, params).fetchall()
        return [
            {
                "id": row["id"],
                "title": row["title"],
                "url": row["url"] or "",
                "source": row["source"],
                "feed_id": row["feed_id"] or "",
                "published_at": row["ts"],
                "words": len((row["text"] or "").split()),
            }
            for row in rows
        ]

    def record_intake(self, item_id: str, *, kind: str, method: str, note: str = "") -> None:
        self.conn.execute(
            """
            INSERT INTO intake (item_id, kind, method, note, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(item_id) DO UPDATE SET
              kind = excluded.kind,
              method = excluded.method,
              note = excluded.note,
              created_at = excluded.created_at
            """,
            (item_id, kind, method, note, utcnow().isoformat()),
        )
        self.conn.commit()

    def list_intake(self, *, limit: int = 80) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT n.item_id AS item_id, n.kind AS kind, n.method AS method,
                   n.note AS note, n.created_at AS created_at,
                   i.title AS title, i.url AS url, i.ts AS ts, i.source AS source
            FROM intake n
            LEFT JOIN items i ON i.id = n.item_id
            ORDER BY n.created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [
            {
                "item_id": row["item_id"],
                "kind": row["kind"],
                "method": row["method"],
                "note": row["note"] or "",
                "created_at": row["created_at"],
                "title": row["title"] or "",
                "url": row["url"] or "",
                "published_at": row["ts"] or "",
                "source": row["source"] or "",
            }
            for row in rows
        ]

    def has_push(self, match_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM alerts WHERE match_id = ? AND kind = 'push'", (match_id,)
        ).fetchone()
        return row is not None

    def record_alert(self, result: MatchResult, *, kind: str, body: str, status: str) -> None:
        self.conn.execute(
            """
            INSERT INTO alerts (match_id, indicator_id, kind, body, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (result.match_id, result.indicator_id, kind, body, status, utcnow().isoformat()),
        )
        self.conn.commit()

    def recent_push_priors(self, indicator_id: str, *, since: datetime) -> list[PriorAlert]:
        rows = self.conn.execute(
            """
            SELECT i.url_hash AS url_hash, i.simhash AS simhash, e.vector AS vector,
                   a.created_at AS created_at
            FROM alerts a
            JOIN matches m ON m.id = a.match_id
            JOIN items i ON i.id = m.item_id
            LEFT JOIN embeddings e
              ON e.entity_id = i.id AND e.kind = 'item' AND e.label = ''
            WHERE a.indicator_id = ? AND a.kind = 'push' AND a.created_at >= ?
            """,
            (indicator_id, since.isoformat()),
        ).fetchall()
        priors: list[PriorAlert] = []
        for row in rows:
            if row["vector"] is None:
                continue
            created = datetime.fromisoformat(row["created_at"])
            priors.append(
                PriorAlert(
                    url_hash=row["url_hash"] or "",
                    simhash=int(row["simhash"], 16),
                    vector=unpack_f32(row["vector"]),
                    created_at=created,
                )
            )
        return priors

    def get_match(self, match_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM matches WHERE id = ?", (match_id,)).fetchone()

    def get_item(self, item_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()

    def add_feedback(self, match_id: str, label: str, *, note: str = "") -> dict:
        match = self.get_match(match_id)
        if match is None:
            raise KeyError(match_id)
        if label not in {"hit", "miss"}:
            raise ValueError(label)
        created = utcnow().isoformat()
        self.conn.execute(
            "INSERT INTO feedback (match_id, label, note, created_at) VALUES (?, ?, ?, ?)",
            (match_id, label, note, created),
        )
        self.conn.commit()
        item = self.get_item(match["item_id"])
        return {
            "match_id": match_id,
            "indicator_id": match["indicator_id"],
            "item_id": match["item_id"],
            "label": label,
            "title": "" if item is None else item["title"],
            "text": "" if item is None else item["text"],
            "url": "" if item is None else (item["url"] or ""),
            "source": "" if item is None else item["source"],
            "p_hit": match["p_hit"],
            "severity": match["severity"],
            "created_at": created,
            "note": note,
        }

    def digest_since(self, since_iso: str | None) -> list[sqlite3.Row]:
        if since_iso:
            return self.conn.execute(
                """
                SELECT * FROM matches
                WHERE action = 'digest' AND created_at > ?
                ORDER BY created_at
                """,
                (since_iso,),
            ).fetchall()
        return self.conn.execute(
            "SELECT * FROM matches WHERE action = 'digest' ORDER BY created_at"
        ).fetchall()

    def set_rip_meta(
        self,
        item_id: str,
        *,
        method: str,
        warnings: list[str],
        note: str = "",
        source_type: str = "manual_rip",
    ) -> None:
        self.conn.execute(
            """
            UPDATE items
            SET rip_method = ?, warnings_json = ?, user_note = ?, source_type = ?
            WHERE id = ?
            """,
            (method, json.dumps(warnings), note, source_type, item_id),
        )
        self.conn.commit()

    def create_rip_job(self, job_id: str, *, url: str, mode: str) -> None:
        self.conn.execute(
            """
            INSERT INTO rip_jobs (id, url, mode, status, created_at)
            VALUES (?, ?, ?, 'queued', ?)
            """,
            (job_id, url, mode, utcnow().isoformat()),
        )
        self.conn.commit()

    def finish_rip_job(
        self,
        job_id: str,
        *,
        status: str,
        item_id: str = "",
        error: str = "",
        result: dict | None = None,
    ) -> None:
        self.conn.execute(
            """
            UPDATE rip_jobs
            SET status = ?, result_item_id = ?, error = ?, result_json = ?
            WHERE id = ?
            """,
            (status, item_id, error, json.dumps(result or {}), job_id),
        )
        self.conn.commit()

    def get_rip_job(self, job_id: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM rip_jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            return None
        return _job_dict(row)

    def list_rip_jobs(self, *, limit: int = 20) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM rip_jobs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [_job_dict(row) for row in rows]

    def count_rip_jobs(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM rip_jobs").fetchone()[0])

    def counts(self) -> dict[str, int]:
        def _count(table: str) -> int:
            return int(self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

        return {
            "items": _count("items"),
            "matches": _count("matches"),
            "alerts": _count("alerts"),
            "feedback": _count("feedback"),
        }


def _job_dict(row: sqlite3.Row) -> dict:
    try:
        result = json.loads(row["result_json"] or "{}")
    except json.JSONDecodeError:
        result = {}
    return {
        "id": row["id"],
        "url": row["url"],
        "mode": row["mode"],
        "status": row["status"],
        "result_item_id": row["result_item_id"] or "",
        "error": row["error"] or "",
        "result": result,
        "created_at": row["created_at"],
    }


def cooldown_since(now: datetime, cooldown: timedelta) -> datetime:
    return now - cooldown
