"""Deliver push alerts. Stdout always. ntfy and other Apprise URLs when configured."""

from __future__ import annotations

import logging
from urllib.parse import urlparse

import httpx

from wt.models import MatchResult

log = logging.getLogger("wt.alert")


def format_result(result: MatchResult, *, feedback_base: str | None) -> str:
    lines = [
        (
            f"[{result.action}] {result.indicator_id}  "
            f"p_hit={result.p_hit:.2f}  severity={result.severity:.2f}  "
            f"cosine={result.cosine:.2f}  backend={result.backend}"
        ),
        f"  {result.title}",
        f"  {result.url}",
    ]
    if result.category:
        lines.append(f"  kind: {result.category}")
    lines.append(f"  why: {result.reason}")
    lines.append(
        f"  feedback: wt feedback {result.match_id} up | wt feedback {result.match_id} down"
    )
    if feedback_base:
        base = feedback_base.rstrip("/")
        lines.append(f"  yes: {base}/feedback/{result.match_id}?label=hit")
        lines.append(f"  no:  {base}/feedback/{result.match_id}?label=miss")
    return "\n".join(lines)


def usable_channels(*groups: list[str]) -> list[str]:
    seen: list[str] = []
    for group in groups:
        for url in group:
            text = (url or "").strip()
            if not text or "<" in text or "example.com" in text or text in seen:
                continue
            seen.append(text)
    return seen


def deliver(
    result: MatchResult,
    *,
    channels: list[str],
    feedback_base: str | None,
    echo: bool = True,
) -> str:
    body = format_result(result, feedback_base=feedback_base)
    title = f"Watch Tower: {result.indicator_id}"
    if echo:
        print(body, flush=True)
    if not channels:
        return "stdout"
    errors: list[str] = []
    sent = 0
    for channel in channels:
        try:
            _send(channel, title, body)
            sent += 1
        except Exception as exc:
            log.warning("alert channel %s failed: %s", channel, exc)
            errors.append(str(exc))
    if sent:
        return f"sent:{sent}"
    if errors:
        return "stdout; channel errors: " + "; ".join(errors)
    return "stdout"


def _send(channel: str, title: str, body: str) -> None:
    if channel.startswith("ntfy://") or _is_ntfy_http(channel):
        _send_ntfy(channel, title, body)
        return
    try:
        import apprise
    except ImportError as exc:
        raise RuntimeError("apprise is not installed") from exc
    notifier = apprise.Apprise()
    if not notifier.add(channel):
        raise RuntimeError(f"apprise rejected {channel}")
    ok = notifier.notify(body=body, title=title)
    if not ok:
        raise RuntimeError(f"apprise notify failed for {channel}")


def _is_ntfy_http(channel: str) -> bool:
    parsed = urlparse(channel)
    return parsed.scheme in {"http", "https"} and "ntfy" in (parsed.netloc or "")


def _send_ntfy(channel: str, title: str, body: str) -> None:
    url = _ntfy_endpoint(channel)
    response = httpx.post(
        url,
        content=body.encode("utf-8"),
        headers={"Title": title, "Markdown": "yes"},
        timeout=20.0,
    )
    response.raise_for_status()


def _ntfy_endpoint(channel: str) -> str:
    if channel.startswith("http://") or channel.startswith("https://"):
        return channel
    rest = channel[len("ntfy://") :]
    if "/" not in rest:
        return f"https://ntfy.sh/{rest}"
    host, topic = rest.split("/", 1)
    if host in {"ntfy.sh", ""}:
        return f"https://ntfy.sh/{topic}"
    return f"http://{host}/{topic}"
