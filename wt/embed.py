"""Embedders.

Live mode can load Qwen/Qwen3-Embedding-0.6B through sentence-transformers.
Demo mode and machines without that model use a deterministic hashing embedder
so the rest of the pipeline still runs offline.
"""

from __future__ import annotations

import hashlib
import logging
import math
from typing import Protocol

from wt.text import tokenize

log = logging.getLogger("wt.embed")


class Embedder(Protocol):
    name: str
    dim: int

    def embed(self, texts: list[str], *, kind: str = "document") -> list[list[float]]: ...


def cosine(a: list[float], b: list[float]) -> float:
    if not a or len(a) != len(b):
        return 0.0
    dot = na = nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / math.sqrt(na * nb)


def _l2(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec))
    if norm <= 0.0:
        return vec
    return [v / norm for v in vec]


class HashingEmbedder:
    """Signed feature hashing (unigrams + bigrams). No model download."""

    def __init__(self, dim: int = 256, seed: int = 13):
        if dim < 16:
            raise ValueError("hashing dimension must be at least 16")
        self.dim = dim
        self.seed = seed
        self.name = f"hashing-{dim}"

    def embed(self, texts: list[str], *, kind: str = "document") -> list[list[float]]:
        return [self._one(text) for text in texts]

    def _one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        tokens = tokenize(text)
        if not tokens:
            return vec
        for token in tokens:
            digest = hashlib.blake2b(
                f"{self.seed}:{token}".encode("utf-8"), digest_size=8
            ).digest()
            number = int.from_bytes(digest, "little")
            index = number % self.dim
            sign = 1.0 if ((number >> 8) & 1) else -1.0
            vec[index] += sign
        return _l2(vec)


class SentenceTransformerEmbedder:
    """Open embedding model. Default checkpoint is Qwen3-Embedding-0.6B."""

    def __init__(self, model_name: str = "Qwen/Qwen3-Embedding-0.6B"):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError(
                "sentence-transformers is not installed. "
                "Install it with: pip install 'watch-tower[embed]'"
            ) from exc
        self.model_name = model_name
        self._model = SentenceTransformer(model_name)
        self.dim = int(self._model.get_sentence_embedding_dimension())
        self.name = model_name

    def embed(self, texts: list[str], *, kind: str = "document") -> list[list[float]]:
        if not texts:
            return []
        kwargs: dict = {"normalize_embeddings": True}
        if kind == "query":
            try:
                vectors = self._model.encode(list(texts), prompt_name="query", **kwargs)
            except TypeError:
                vectors = self._model.encode(list(texts), **kwargs)
        else:
            vectors = self._model.encode(list(texts), **kwargs)
        return [list(map(float, row)) for row in vectors]


def build_embedder(kind: str, *, model_name: str, hash_dim: int) -> Embedder:
    """`auto` tries the configured model, then hashing. `hashing` never touches the network."""
    if kind in {"hashing", "hash"}:
        return HashingEmbedder(hash_dim)
    if kind in {"qwen", "sentence-transformers", "auto"}:
        try:
            return SentenceTransformerEmbedder(model_name)
        except Exception as exc:
            if kind == "auto":
                log.warning("embedding model unavailable (%s); using hashing embedder", exc)
                return HashingEmbedder(hash_dim)
            raise
    raise ValueError(f"unknown embedder {kind!r} (expected auto, qwen, or hashing)")
