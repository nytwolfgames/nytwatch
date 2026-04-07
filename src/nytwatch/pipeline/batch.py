from __future__ import annotations

import logging

from nytwatch.config import AuditorConfig
from nytwatch.database import Database
from nytwatch.models import BatchStatus, FindingStatus, now_iso
from nytwatch.pipeline.applicator import apply_batch_fixes
from nytwatch.pipeline.git_ops import commit_changes
from nytwatch.pipeline.test_writer import write_test_files

logger = logging.getLogger(__name__)


def run_batch_pipeline(config: AuditorConfig, db: Database, batch_id: str):
    batch = db.get_batch(batch_id)
    if not batch:
        logger.error("Batch %s not found", batch_id)
        return

    findings = [db.get_finding(fid) for fid in batch["finding_ids"]]
    findings = [f for f in findings if f]

    if not findings:
        logger.error("No findings found for batch %s", batch_id)
        db.update_batch(batch_id, status=BatchStatus.FAILED)
        return

    repo_path = config.repo_path
    db.update_batch(batch_id, status=BatchStatus.APPLYING)

    try:
        # Step 1: Apply fixes
        affected_file_paths = sorted({f["file_path"] for f in findings})
        logger.info("Batch %s: applying %d fixes across %d files", batch_id, len(findings), len(affected_file_paths))
        success, modified_files, error_or_notes = apply_batch_fixes(repo_path, findings, affected_file_paths)

        if not success:
            logger.error("Batch %s: failed to apply fixes: %s", batch_id, error_or_notes)
            db.update_batch(
                batch_id,
                status=BatchStatus.FAILED,
                build_log=f"Patch apply failed:\n{error_or_notes}",
                completed_at=now_iso(),
            )
            for f in findings:
                db.update_finding_status(f["id"], FindingStatus.FAILED)
            return

        # Step 2: Write test files (only for findings with include_test set)
        test_files = write_test_files(repo_path, findings)
        logger.info("Batch %s: wrote %d test files", batch_id, len(test_files))

        # Step 3: Commit only the changed files
        # Use files Claude actually modified (may include files beyond the findings' file_path list)
        db.update_batch(batch_id, status=BatchStatus.COMMITTING)
        finding_summaries = "\n".join(
            f"- [{f['severity']}] {f['title']} ({f['file_path']})"
            for f in findings
        )
        commit_msg = (
            f"fix: batch #{batch_id[:8]} - {len(findings)} findings resolved\n\n"
            f"Automated fixes by Nytwatch:\n{finding_summaries}"
        )
        files_to_commit = modified_files + test_files
        try:
            commit_sha = commit_changes(repo_path, commit_msg, files=files_to_commit)
        except Exception as exc:
            logger.error("Batch %s: commit failed: %s", batch_id, exc)
            db.update_batch(
                batch_id,
                status=BatchStatus.FAILED,
                build_log=f"Fixes applied but commit failed:\n{exc}\n\nFiles were modified — commit or stash manually.",
                completed_at=now_iso(),
            )
            for f in findings:
                db.update_finding_status(f["id"], FindingStatus.FAILED)
            return
        db.update_batch(batch_id, commit_sha=commit_sha)

        # Step 4: Mark everything verified
        db.update_batch(batch_id, status=BatchStatus.VERIFIED, completed_at=now_iso())
        for f in findings:
            db.update_finding_status(f["id"], FindingStatus.VERIFIED)

        logger.info("Batch %s: pipeline complete, commit %s", batch_id, commit_sha[:8])

    except Exception:
        logger.exception("Batch %s: unexpected error", batch_id)
        db.update_batch(batch_id, status=BatchStatus.FAILED, completed_at=now_iso())
        for f in findings:
            db.update_finding_status(f["id"], FindingStatus.FAILED)
