"""Hugging Face Hub model search. The public models API needs no token."""

from __future__ import annotations

from wt.ingest.common import client
from wt.models import Item
from wt.normalize import make_item
from wt.text import parse_time

HF_URL = "https://huggingface.co/api/models"


def fetch_hf_hub(cfg: dict, http=None) -> list[Item]:
    if cfg.get("enabled") is False:
        return []
    search = (cfg.get("search") or "open-weight").strip()
    limit = int(cfg.get("limit", 10))
    own = http is None
    if own:
        http = client()
    try:
        response = http.get(HF_URL, params={"search": search, "limit": limit, "sort": "createdAt"})
        response.raise_for_status()
        payload = response.json()
    finally:
        if own:
            http.close()
    return items_from_hf(payload)


def items_from_hf(payload) -> list[Item]:
    rows = payload if isinstance(payload, list) else (payload or {}).get("models") or []
    items: list[Item] = []
    for model in rows:
        model_id = str(model.get("id") or model.get("modelId") or "").strip()
        if not model_id:
            continue
        tag = str(model.get("pipeline_tag") or "")
        item = make_item(
            url=f"https://huggingface.co/{model_id}",
            source="hf_hub",
            feed_id="models",
            title=model_id,
            text=tag or model_id,
            ts=parse_time(model.get("createdAt") or model.get("lastModified")),
        )
        if item is not None:
            items.append(item)
    return items
