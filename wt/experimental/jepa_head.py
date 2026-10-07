"""JEPA-style linear predictor in embedding space.

Frozen item vectors z_x and indicator vectors z_y. A ridge map P is fit so
P(z_x) is close to z_y on confirmed hits. score = cosine(P(z_x), z_y).

This is an experiment, not part of the default alert decision. It is close to
fitting a linear probe on the bi-encoder, which the research note already flags.
"""

from __future__ import annotations

import numpy as np

from wt.embed import cosine


class JepaHead:
    def __init__(self, dim: int):
        self.dim = dim
        self.W = np.eye(dim)

    def fit(self, item_vecs, indicator_vecs, *, l2: float = 1e-2) -> JepaHead:
        x = np.asarray(item_vecs, dtype=np.float64)
        y = np.asarray(indicator_vecs, dtype=np.float64)
        if x.ndim != 2 or y.ndim != 2 or x.shape != y.shape:
            raise ValueError("item and indicator matrices must share shape (n, dim)")
        if x.shape[1] != self.dim:
            raise ValueError(f"expected dim {self.dim}, got {x.shape[1]}")
        gram = x.T @ x + l2 * np.eye(self.dim)
        self.W = np.linalg.solve(gram, x.T @ y)
        return self

    def predict(self, item_vec) -> np.ndarray:
        z = np.asarray(item_vec, dtype=np.float64)
        return z @ self.W

    def score(self, item_vec, indicator_vec) -> float:
        predicted = self.predict(item_vec)
        return cosine(predicted.tolist(), list(np.asarray(indicator_vec, dtype=np.float64)))
