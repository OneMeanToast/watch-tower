"""Shared HTTP client for source adapters."""

from __future__ import annotations

import httpx

USER_AGENT = "watch-tower/0.1 (local OSINT monitor; RSS and public APIs)"


def client() -> httpx.Client:
    return httpx.Client(
        timeout=httpx.Timeout(30.0),
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    )
