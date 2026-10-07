#!/usr/bin/env python3
"""Precision, recall, and Brier score of the lexical decider on eval/labels.jsonl."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from wt.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(["eval", *sys.argv[1:]]))
