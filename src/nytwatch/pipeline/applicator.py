from __future__ import annotations

import logging

from nytwatch.analysis.engine import generate_batch_patch
from nytwatch.pipeline import git_ops

log = logging.getLogger(__name__)


def apply_batch_fixes(
    repo_path: str,
    findings: list[dict],
    file_paths: list[str],
) -> tuple[bool, str, str]:
    """Generate and apply a unified patch for all findings.

    ``file_paths`` is the deduplicated list of repo-relative paths that need
    editing.  Claude reads their current contents itself (agent mode).

    Returns (success, patch_or_error, notes).
    """
    log.info("Generating batch patch for %d findings across %d files", len(findings), len(file_paths))
    result = generate_batch_patch(findings, file_paths, repo_path)

    if result is None:
        return False, "Patch generation returned no result", ""

    success, error = git_ops.apply_patch(repo_path, result.unified_diff)
    if success:
        log.info("Layer 1: patch applied successfully")
        return True, result.unified_diff, result.notes

    log.warning("Layer 1 failed: %s. Retrying with feedback.", error)
    retry_findings = [
        {**f, "_retry_note": f"Previous apply failed: {error}"} for f in findings
    ]
    retry_result = generate_batch_patch(retry_findings, file_paths, repo_path, max_retries=2)

    if retry_result is None:
        return False, f"Retry patch generation failed (original error: {error})", ""

    success, retry_error = git_ops.apply_patch(repo_path, retry_result.unified_diff)
    if success:
        log.info("Layer 2: retry patch applied successfully")
        return True, retry_result.unified_diff, retry_result.notes

    final_error = f"Both attempts failed. L1: {error} | L2: {retry_error}"
    log.error(final_error)
    return False, final_error, ""
