from __future__ import annotations

import logging

from nytwatch.analysis.engine import generate_batch_patch

log = logging.getLogger(__name__)


def apply_batch_fixes(
    repo_path: str,
    findings: list[dict],
    file_paths: list[str],
) -> tuple[bool, list[str], str]:
    """Apply fixes for all findings by having Claude edit the files directly.

    Claude uses its Edit tool to write changes in-place.  No git-apply step
    is needed — the files are already modified when Claude finishes.

    Returns (success, files_modified_or_[], error_or_notes).
    """
    log.info("Applying batch fixes for %d findings across %d files", len(findings), len(file_paths))
    result = generate_batch_patch(findings, file_paths, repo_path)

    if result is None:
        return False, [], "Fix generation returned no result"

    if not result.files_modified:
        return False, [], "Claude reported no files modified"

    log.info("Fixes applied to: %s", ", ".join(result.files_modified))
    return True, result.files_modified, result.notes
