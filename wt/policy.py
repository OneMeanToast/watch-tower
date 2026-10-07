"""Threshold expressions, cooldown windows, and near-duplicate suppression.

Expressions are a tiny language: comparisons, and/or/not, and parentheses.
Names are only hit, severity, cosine, and confidence. Nothing is eval()'d.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

from wt.embed import cosine
from wt.models import Indicator

ALLOWED_NAMES = frozenset({"hit", "severity", "cosine", "confidence"})
_TOKEN = re.compile(
    r"\s*(>=|<=|==|!=|>|<|\(|\)|[A-Za-z_][A-Za-z0-9_]*|\d+(?:\.\d+)?)"
)
_DURATION = re.compile(r"^(\d+)\s*([smhd])$")
_OPS = {
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    ">": lambda a, b: a > b,
    "<": lambda a, b: a < b,
}


class PolicyError(ValueError):
    pass


def parse_duration(value: str) -> timedelta:
    match = _DURATION.fullmatch(value.strip().lower())
    if not match:
        raise PolicyError(f"cooldown {value!r} must look like 30s, 15m, 6h, or 1d")
    amount = int(match.group(1))
    unit = match.group(2)
    seconds = amount * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
    return timedelta(seconds=seconds)


def _tokenize(expr: str) -> list[str]:
    tokens: list[str] = []
    pos = 0
    for match in _TOKEN.finditer(expr):
        if match.start() != pos and expr[pos:match.start()].strip():
            raise PolicyError(f"unexpected characters in expression: {expr!r}")
        tokens.append(match.group(1))
        pos = match.end()
    if expr[pos:].strip():
        raise PolicyError(f"unexpected characters in expression: {expr!r}")
    if not tokens:
        raise PolicyError("empty threshold expression")
    return tokens


class _Parser:
    def __init__(self, tokens: list[str]):
        self.tokens = tokens
        self.i = 0

    def peek(self) -> str | None:
        if self.i >= len(self.tokens):
            return None
        return self.tokens[self.i]

    def eat(self) -> str:
        token = self.peek()
        if token is None:
            raise PolicyError("unexpected end of expression")
        self.i += 1
        return token

    def parse(self) -> tuple:
        node = self._or()
        if self.peek() is not None:
            raise PolicyError(f"unexpected token {self.peek()!r}")
        return node

    def _or(self) -> tuple:
        node = self._and()
        while (self.peek() or "").lower() == "or":
            self.eat()
            node = ("or", node, self._and())
        return node

    def _and(self) -> tuple:
        node = self._not()
        while (self.peek() or "").lower() == "and":
            self.eat()
            node = ("and", node, self._not())
        return node

    def _not(self) -> tuple:
        if (self.peek() or "").lower() == "not":
            self.eat()
            return ("not", self._not())
        return self._primary()

    def _primary(self) -> tuple:
        token = self.peek()
        if token == "(":
            self.eat()
            node = self._or()
            if self.peek() != ")":
                raise PolicyError("missing closing parenthesis")
            self.eat()
            return node
        if token is None or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", token):
            raise PolicyError(f"expected a name, found {token!r}")
        name = self.eat()
        if name.lower() in {"and", "or", "not"}:
            raise PolicyError(f"expected a comparison, found {name!r}")
        if name not in ALLOWED_NAMES:
            raise PolicyError(
                f"unknown name {name!r}; use hit, severity, cosine, or confidence"
            )
        op = self.peek()
        if op not in _OPS:
            raise PolicyError(f"expected a comparison operator after {name}")
        self.eat()
        raw = self.peek()
        if raw is None or not re.fullmatch(r"\d+(?:\.\d+)?", raw):
            raise PolicyError(f"expected a number after {name} {op}")
        self.eat()
        return ("cmp", name, op, float(raw))


def compile_expr(expr: str) -> Callable[[dict[str, float]], bool]:
    tree = _Parser(_tokenize(expr)).parse()

    def evaluate(env: dict[str, float]) -> bool:
        return _eval(tree, env)

    return evaluate


def _eval(node: tuple, env: dict[str, float]) -> bool:
    kind = node[0]
    if kind == "and":
        return _eval(node[1], env) and _eval(node[2], env)
    if kind == "or":
        return _eval(node[1], env) or _eval(node[2], env)
    if kind == "not":
        return not _eval(node[1], env)
    _kind, name, op, value = node
    if name not in env:
        raise PolicyError(f"missing value for {name}")
    return bool(_OPS[op](float(env[name]), value))


def validate_indicator_policy(indicator: Indicator) -> None:
    compile_expr(indicator.policy.alert_if)
    if indicator.policy.digest_if:
        compile_expr(indicator.policy.digest_if)
    parse_duration(indicator.policy.cooldown)


def decide_action(
    indicator: Indicator,
    *,
    p_hit: float,
    severity: float,
    cosine_value: float,
    confidence: float,
) -> tuple[str, str]:
    env = {
        "hit": float(p_hit),
        "severity": float(severity),
        "cosine": float(cosine_value),
        "confidence": float(confidence),
    }
    if compile_expr(indicator.policy.alert_if)(env):
        return "push", "alert_if matched"
    if indicator.policy.digest_if and compile_expr(indicator.policy.digest_if)(env):
        return "digest", "digest_if matched"
    return "drop", "below thresholds"


@dataclass
class PriorAlert:
    url_hash: str
    simhash: int
    vector: list[float]
    created_at: datetime


def hamming(left: int, right: int) -> int:
    return (left ^ right).bit_count()


def suppressed_by_cooldown(
    priors: list[PriorAlert],
    *,
    url_hash: str,
    simhash: int,
    vector: list[float],
    now: datetime,
    cooldown: timedelta,
    cosine_threshold: float = 0.80,
    max_hamming: int = 8,
) -> bool:
    """Suppress a push when a recent alert for this indicator is nearly the same story."""
    for prior in priors:
        if now - prior.created_at > cooldown:
            continue
        if url_hash and prior.url_hash == url_hash:
            return True
        if hamming(prior.simhash, simhash) <= max_hamming:
            return True
        if cosine(prior.vector, vector) >= cosine_threshold:
            return True
    return False
