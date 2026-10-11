"""Provenance tables. SQLite is the source of truth; a graph store is only a projection."""

from __future__ import annotations

DDL = """
CREATE TABLE IF NOT EXISTS extract_documents (
  id TEXT PRIMARY KEY,
  item_id TEXT NOT NULL DEFAULT '',
  title TEXT NOT NULL,
  text TEXT NOT NULL,
  text_sha256 TEXT NOT NULL,
  lang TEXT NOT NULL DEFAULT 'en',
  kind TEXT NOT NULL DEFAULT '',
  starred INTEGER NOT NULL DEFAULT 0,
  local_only INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS extract_mentions (
  id TEXT PRIMARY KEY,
  doc_id TEXT NOT NULL,
  start INTEGER NOT NULL,
  end INTEGER NOT NULL,
  surface TEXT NOT NULL,
  type TEXT NOT NULL,
  entity_id TEXT,
  extractor TEXT NOT NULL,
  extractor_version TEXT NOT NULL DEFAULT '',
  model_version TEXT NOT NULL DEFAULT '',
  p_local REAL,
  p_jev REAL,
  status TEXT NOT NULL DEFAULT 'proposed',
  answers_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_extract_mentions_doc ON extract_mentions(doc_id);
CREATE TABLE IF NOT EXISTS extract_entities (
  id TEXT PRIMARY KEY,
  schema TEXT NOT NULL,
  caption TEXT NOT NULL,
  wikidata_qid TEXT,
  merged_into TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS extract_aliases (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  entity_id TEXT NOT NULL,
  alias TEXT NOT NULL,
  doc_id TEXT NOT NULL DEFAULT '',
  mention_id TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS extract_judgments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  left_id TEXT NOT NULL,
  right_id TEXT NOT NULL,
  judgment TEXT NOT NULL,
  source TEXT NOT NULL,
  p REAL,
  reversible INTEGER NOT NULL DEFAULT 1,
  undone INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS extract_relations (
  id TEXT PRIMARY KEY,
  type TEXT NOT NULL,
  head_entity TEXT NOT NULL,
  tail_entity TEXT NOT NULL,
  head_mention TEXT NOT NULL,
  tail_mention TEXT NOT NULL,
  doc_id TEXT NOT NULL,
  start INTEGER NOT NULL,
  end INTEGER NOT NULL,
  modality TEXT NOT NULL DEFAULT 'asserted',
  p REAL,
  status TEXT NOT NULL DEFAULT 'proposed',
  answers_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS extract_events (
  id TEXT PRIMARY KEY,
  type TEXT NOT NULL,
  cameo_code TEXT NOT NULL DEFAULT '',
  doc_id TEXT NOT NULL,
  mention_id TEXT NOT NULL,
  start INTEGER NOT NULL,
  end INTEGER NOT NULL,
  trigger TEXT NOT NULL DEFAULT '',
  when_norm TEXT NOT NULL DEFAULT '',
  p REAL,
  status TEXT NOT NULL DEFAULT 'proposed',
  answers_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS extract_event_roles (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id TEXT NOT NULL,
  role TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  mention_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS extract_actions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  target_kind TEXT NOT NULL,
  target_id TEXT NOT NULL,
  action TEXT NOT NULL,
  before_json TEXT NOT NULL DEFAULT '{}',
  after_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS extract_jobs (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  item_id TEXT NOT NULL,
  state TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  run_after TEXT NOT NULL,
  reason TEXT NOT NULL DEFAULT '',
  error TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_extract_job_open
  ON extract_jobs(item_id, kind) WHERE state IN ('queued', 'running', 'done');
"""


def ensure_extract_schema(conn) -> None:
    conn.executescript(DDL)
