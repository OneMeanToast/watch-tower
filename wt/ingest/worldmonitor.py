"""World Monitor feed URLs, loaded as data.

The catalog in config/feeds/worldmonitor.yaml is names and URLs mined from
the public World Monitor inventory. None of that project's code is imported.
The catalog stays off unless sources.yaml sets worldmonitor.enabled.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from wt.settings import project_root


def catalog_path(cfg: dict | None = None) -> Path:
    raw = "" if not cfg else str(cfg.get("catalog") or "")
    if raw:
        path = Path(raw)
        if not path.is_absolute():
            path = project_root() / path
        return path
    return project_root() / "config" / "feeds" / "worldmonitor.yaml"


def load_catalog(cfg: dict | None = None) -> list[dict]:
    path = catalog_path(cfg)
    if not path.is_file():
        raise FileNotFoundError(f"world monitor catalog not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    feeds = data.get("feeds") if isinstance(data, dict) else None
    if not isinstance(feeds, list):
        raise ValueError(f"{path} has no feeds list")
    rows = []
    for row in feeds:
        if not isinstance(row, dict) or not row.get("url"):
            continue
        rows.append(
            {
                "id": str(row.get("id") or "worldmonitor"),
                "url": str(row["url"]),
                "name": str(row.get("name") or ""),
            }
        )
    return rows
