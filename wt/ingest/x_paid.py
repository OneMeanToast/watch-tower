"""X/Twitter is intentionally disabled.

The official API is paid per read, and the old Nitter-style free mirrors are not
a workable substitute. Watch Tower uses RSS, arXiv, GDELT, OpenAlex, Bluesky,
and Mastodon instead.
"""

from __future__ import annotations


class XPaidDisabled(RuntimeError):
    pass


def fetch_x(_cfg: dict | None = None) -> list:
    raise XPaidDisabled(
        "X/Twitter ingestion is disabled. The official API is paid, and free "
        "scrapes are out of scope for this project. Use Bluesky, Mastodon, RSS, or GDELT."
    )
