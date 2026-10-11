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


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    if value < 0:
        return default
    return value


@dataclass
class Settings:
    db_path: Path
    indicators_dir: Path
    sources_path: Path
    fixtures_path: Path
    decider: str = "auto"
    decider_url: str = "http://127.0.0.1:8009"
    # Request body model name. Kev's server ignores this and serves the
    # checkpoint it was started with (`--run jaredpalmer/kev-4b@v1.0`).
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
    # Empty means no second model. "ollama" or "systemone" re-scores the
    # uncertain band. Only a confident pass can upgrade a digest to a push.
    escalate: str = ""
    injection_guard: str = "heuristic"
    fetch_fulltext: bool = True
    spam_min_cluster: int = 3
    jev_base_url: str = "https://api.typesafe.ai"
    jev_api_key: str | None = None
    jev_model: str = "jev-1.13.0"
    jev_usd_per_mtok: float = 0.042
    jev_monthly_usd: float = 10.0
    jev_daily_warn_usd: float = 1.0
    jev_prompt_version: str = "1"
    jev_allow_private: bool = False

    @classmethod
    def from_env(cls, root: Path | None = None) -> Settings:
        root = root or project_root()
        jev_key = (os.environ.get("JEV_API_KEY") or "").strip() or None
        requested = os.environ.get("WT_DECIDER", "").strip().lower()
        if requested in {"", "auto"}:
            decider = "jev" if jev_key else "auto"
        else:
            decider = requested
        return cls(
            db_path=Path(os.environ.get("WT_DB", root / "data" / "watchtower.db")),
            indicators_dir=Path(os.environ.get("WT_INDICATORS", root / "config" / "indicators")),
            sources_path=Path(os.environ.get("WT_SOURCES", root / "config" / "sources.yaml")),
            fixtures_path=Path(os.environ.get("WT_FIXTURES", root / "fixtures" / "items.json")),
            decider=decider,
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
            escalate=os.environ.get("WT_ESCALATE", "").strip().lower(),
            injection_guard=os.environ.get("WT_INJECTION_GUARD", "heuristic").strip().lower(),
            fetch_fulltext=os.environ.get("WT_FETCH_FULLTEXT", "1") not in {"0", "false", "no"},
            spam_min_cluster=int(os.environ.get("WT_SPAM_MIN_CLUSTER", "3")),
            jev_base_url=os.environ.get("JEV_BASE_URL", "https://api.typesafe.ai").strip()
            or "https://api.typesafe.ai",
            jev_api_key=jev_key,
            jev_model=os.environ.get("JEV_MODEL", "jev-1.13.0").strip() or "jev-1.13.0",
            jev_usd_per_mtok=_float_env("WT_JEV_USD_PER_MTOK", 0.042),
            jev_monthly_usd=_float_env("WT_JEV_MONTHLY_USD", 10.0),
            jev_daily_warn_usd=_float_env("WT_JEV_DAILY_WARN_USD", 1.0),
            jev_prompt_version=os.environ.get("WT_JEV_PROMPT_VERSION", "1").strip() or "1",
            jev_allow_private=os.environ.get("WT_JEV_ALLOW_PRIVATE", "0")
            not in {"0", "false", "no", ""},
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
        # A key in the environment must not send fixture text to Jev.
        base.escalate = ""
        base.fetch_fulltext = False
        base.injection_guard = "heuristic"
        return base
