from __future__ import annotations

import logging
from typing import Optional

from auditor.analysis.engine import generate_batch_patch
from auditor.pipeline import git_ops

log = logging.getLogger(__name__)


def apply_batch_fixes(
    repo_path: str,
    findings: list[dict],
    file_contents: dict[str, str],
) -> tuple[bool, str, str]:
    log.info("Generating batch patch for %d findings", len(findings))
    result = generate_batch_patch(findings, file_contents)

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
    retry_result = generate_batch_patch(retry_findings, file_contents, max_retries=2)

    if retry_result is None:
        return False, f"Retry patch generation failed (original error: {error})", ""

    success, retry_error = git_ops.apply_patch(repo_path, retry_result.unified_diff)
    if success:
        log.info("Layer 2: retry patch applied successfully")
        return True, retry_result.unified_diff, retry_result.notes

    final_error = f"Both attempts failed. L1: {error} | L2: {retry_error}"
    log.error(final_error)
    return False, final_error, ""
