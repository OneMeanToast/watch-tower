"""Paths and runtime switches. Environment variables use the WT_ prefix."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def project_root() -> Path:
    packaged = Path(__file__).resolve().parents[1]
    if (packaged / "config" / "indicators").is_dir():
        return packaged
    cwd = Path.cwd()
    if (cwd / "config" / "indicators").is_dir():
        return cwd
    return packaged


def _split_urls(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


@dataclass
class Settings:
    db_path: Path
    indicators_dir: Path
    sources_path: Path
    fixtures_path: Path
    decider: str = "auto"
    decider_url: str = "http://127.0.0.1:8009"
    decider_model: str = "kev-latest"
    decider_api_key: str | None = None
    decider_fallback: bool = True
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen2.5:3b"
    embedder: str = "auto"
    embedder_model: str = "Qwen/Qwen3-Embedding-0.6B"
    hash_dim: int = 256
    top_k: int = 8
    ntfy_url: str | None = None
    apprise_urls: list[str] = field(default_factory=list)
    feedback_base: str = "http://127.0.0.1:8787"
    labels_path: Path = Path("data/feedback.jsonl")
    echo: bool = True

    @classmethod
    def from_env(cls, root: Path | None = None) -> Settings:
        root = root or project_root()
        return cls(
            db_path=Path(os.environ.get("WT_DB", root / "data" / "watchtower.db")),
            indicators_dir=Path(os.environ.get("WT_INDICATORS", root / "config" / "indicators")),
            sources_path=Path(os.environ.get("WT_SOURCES", root / "config" / "sources.yaml")),
            fixtures_path=Path(os.environ.get("WT_FIXTURES", root / "fixtures" / "items.json")),
            decider=os.environ.get("WT_DECIDER", "auto"),
            decider_url=os.environ.get("WT_DECIDER_URL", "http://127.0.0.1:8009"),
            decider_model=os.environ.get("WT_DECIDER_MODEL", "kev-latest"),
            decider_api_key=os.environ.get("WT_DECIDER_API_KEY") or None,
            decider_fallback=os.environ.get("WT_DECIDER_FALLBACK", "1") not in {"0", "false", "no"},
            ollama_url=os.environ.get("WT_OLLAMA_URL", "http://127.0.0.1:11434"),
            ollama_model=os.environ.get("WT_OLLAMA_MODEL", "qwen2.5:3b"),
            embedder=os.environ.get("WT_EMBEDDER", "auto"),
            embedder_model=os.environ.get("WT_EMBEDDER_MODEL", "Qwen/Qwen3-Embedding-0.6B"),
            hash_dim=int(os.environ.get("WT_HASH_DIM", "256")),
            top_k=int(os.environ.get("WT_TOP_K", "8")),
            ntfy_url=os.environ.get("WT_NTFY_URL") or None,
            apprise_urls=_split_urls(os.environ.get("WT_APPRISE_URLS")),
            feedback_base=os.environ.get("WT_FEEDBACK_BASE", "http://127.0.0.1:8787"),
            labels_path=Path(os.environ.get("WT_LABELS", root / "data" / "feedback.jsonl")),
        )

    @classmethod
    def for_demo(cls, root: Path | None = None, db_path: Path | None = None) -> Settings:
        """Offline demo: hashing embedder and the lexical decider, ignoring model env vars."""
        base = cls.from_env(root)
        base.db_path = db_path or (base.db_path.parent / "demo.db")
        if db_path is None:
            root = root or project_root()
            base.db_path = root / "data" / "demo.db"
        base.decider = "lexical"
        base.embedder = "hashing"
        return base
