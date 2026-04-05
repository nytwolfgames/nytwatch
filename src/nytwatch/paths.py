from __future__ import annotations


def normalize_path(p: str) -> str:
    """Normalize a path to always use forward slashes.

    All internal path handling in nytwatch uses POSIX-style forward
    slashes, matching git's output format.  This function ensures paths
    produced by ``pathlib.Path`` on Windows (which use backslashes) are
    converted to the canonical format before storage or comparison.
    """
    return p.replace("\\", "/")
