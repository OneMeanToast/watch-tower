"""robots.txt. A one-off rip warns. A subscription refuses."""

from __future__ import annotations

from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

from wt.rip.fetch import USER_AGENT, guarded_get
from wt.rip.ssrf import SsrfError


def robots_status(url: str, *, client=None, resolver=None) -> tuple[bool, str]:
    """Return (allowed, warning). A missing robots file allows the fetch."""
    parts = urlsplit(url)
    if not parts.scheme or not parts.hostname:
        return True, ""
    robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
    try:
        result = guarded_get(robots_url, client=client, resolver=resolver)
    except SsrfError:
        raise
    except Exception:
        return True, ""
    if result.status >= 400 or not result.body.strip():
        return True, ""
    parser = RobotFileParser()
    parser.parse(result.text.splitlines())
    if parser.can_fetch(USER_AGENT, url):
        return True, ""
    return False, "robots.txt disallows this URL for WatchTower"


def subscription_allowed(url: str, *, client=None, resolver=None) -> tuple[bool, str]:
    allowed, warning = robots_status(url, client=client, resolver=resolver)
    if allowed:
        return True, ""
    return False, warning or "robots.txt disallows this URL"
