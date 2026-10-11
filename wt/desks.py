"""Named desks. One shared wire, a different set of lanes per person."""

from __future__ import annotations

from pathlib import Path

import yaml

from wt.settings import project_root


def seed_path(root: Path | None = None) -> Path:
    return (root or project_root()) / "config" / "desks.yaml"


def live_path(root: Path | None = None) -> Path:
    return (root or project_root()) / "data" / "desks.yaml"


def load_desks(root: Path | None = None) -> list[dict]:
    root = root or project_root()
    path = live_path(root) if live_path(root).is_file() else seed_path(root)
    if not path.is_file():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    desks = data.get("desks") if isinstance(data, dict) else None
    if not isinstance(desks, list):
        return []
    return [_normalize(row) for row in desks if isinstance(row, dict) and row.get("id")]


def save_desks(desks: list[dict], root: Path | None = None) -> None:
    root = root or project_root()
    path = live_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump({"desks": [_normalize(row) for row in desks]}, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def get_desk(desk_id: str, root: Path | None = None) -> dict | None:
    for desk in load_desks(root):
        if desk["id"] == desk_id:
            return desk
    return None


def upsert_desk(desk: dict, root: Path | None = None) -> dict:
    desks = load_desks(root)
    normalized = _normalize(desk)
    replaced = False
    for index, existing in enumerate(desks):
        if existing["id"] == normalized["id"]:
            desks[index] = normalized
            replaced = True
            break
    if not replaced:
        desks.append(normalized)
    save_desks(desks, root)
    return normalized


def allows(desk: dict | None, indicator_id: str) -> bool:
    if desk is None:
        return True
    lanes = desk.get("indicators") or []
    return not lanes or indicator_id in lanes


def _normalize(row: dict) -> dict:
    indicators = row.get("indicators") or []
    exports = row.get("exports") or ["markdown", "csv", "json", "kev"]
    return {
        "id": str(row.get("id") or "").strip(),
        "name": str(row.get("name") or row.get("id") or "").strip(),
        "brief": str(row.get("brief") or "").strip(),
        "indicators": [str(item) for item in indicators],
        "exports": [str(item) for item in exports],
        "digest_seen_at": str(row.get("digest_seen_at") or ""),
    }
