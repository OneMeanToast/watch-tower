"""OpenAlex works search. A free key (WT_OPENALEX_KEY) raises the daily budget."""

from __future__ import annotations

import os

from wt.ingest.common import client
from wt.models import Item
from wt.normalize import make_item
from wt.text import parse_time


def fetch_openalex(cfg: dict) -> list[Item]:
    search = (cfg.get("search") or "").strip()
    if not search:
        return []
    per_page = int(cfg.get("per_page", 10))
    params = {
        "search": search,
        "per-page": per_page,
        "sort": "publication_date:desc",
    }
    mailto = os.environ.get("WT_OPENALEX_MAILTO") or cfg.get("mailto")
    if mailto:
        params["mailto"] = mailto
    headers = {}
    api_key = os.environ.get("WT_OPENALEX_KEY") or cfg.get("api_key")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    with client() as http:
        response = http.get("https://api.openalex.org/works", params=params, headers=headers)
        if response.status_code in {401, 403}:
            raise RuntimeError(
                "OpenAlex rejected the request. Set WT_OPENALEX_KEY "
                "(free key from https://openalex.org/settings/api)."
            )
        response.raise_for_status()
        payload = response.json()
    return items_from_openalex(payload)


def reconstruct_abstract(inverted: dict | None) -> str:
    if not inverted:
        return ""
    placed: list[tuple[int, str]] = []
    for word, indexes in inverted.items():
        for index in indexes or []:
            placed.append((int(index), str(word)))
    placed.sort()
    return " ".join(word for _, word in placed)


def items_from_openalex(payload: dict) -> list[Item]:
    items: list[Item] = []
    for work in payload.get("results") or []:
        url = work.get("doi") or work.get("id") or ""
        authors = []
        for authorship in work.get("authorships") or []:
            author = authorship.get("author") or {}
            name = author.get("display_name")
            if name:
                authors.append(name)
        item = make_item(
            url=url,
            source="openalex",
            feed_id="works",
            title=work.get("display_name") or "",
            text=reconstruct_abstract(work.get("abstract_inverted_index")),
            ts=parse_time(work.get("publication_date")),
            authors=authors,
        )
        if item is not None:
            items.append(item)
    return items
