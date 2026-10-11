"""Manual URL rip and subscribe. Folded into the catalog, not a second app."""

from wt.rip.pipeline import RipError, commit_rip

__all__ = ["RipError", "commit_rip"]
