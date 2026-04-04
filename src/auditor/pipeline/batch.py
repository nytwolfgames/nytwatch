from __future__ import annotations

import logging

from auditor.config import AuditorConfig
from auditor.database import Database
from auditor.models import BatchStatus, FindingStatus, now_iso
from auditor.pipeline.applicator import apply_batch_fixes
from auditor.pipeline.builder import run_ue_build
from auditor.pipeline.git_ops import (
    checkout_main,
    commit_changes,
    create_branch,
    create_pr,
    delete_branch,
    stash_changes,
    stash_pop,
)
from auditor.pipeline.notifier import format_batch_complete_message, notify
from auditor.pipeline.test_runner import run_tests
from auditor.pipeline.test_writer import cleanup_test_files, write_test_files

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
    branch_name = f"auditor/batch-{batch_id}"
    db.update_batch(batch_id, branch_name=branch_name)

    stashed = False
    branch_created = False

    try:
        # Step 1: Prepare branch
        logger.info("Batch %s: preparing branch %s", batch_id, branch_name)
        db.update_batch(batch_id, status=BatchStatus.APPLYING)

        stashed = stash_changes(repo_path)
        create_branch(repo_path, branch_name)
        branch_created = True

        # Step 2: Collect the repo-relative paths of affected files.
        # Claude reads their current contents itself via the Read tool —
        # no need to embed file text in the prompt.
        affected_file_paths = sorted({f["file_path"] for f in findings})

        # Step 3: Apply fixes
        logger.info("Batch %s: applying %d fixes across %d files", batch_id, len(findings), len(affected_file_paths))
        success, patch_or_error, notes = apply_batch_fixes(
            repo_path, findings, affected_file_paths
        )

        if not success:
            logger.error("Batch %s: failed to apply fixes: %s", batch_id, patch_or_error)
            db.update_batch(
                batch_id,
                status=BatchStatus.FAILED,
                build_log=f"Patch apply failed:\n{patch_or_error}",
                completed_at=now_iso(),
            )
            _cleanup(repo_path, branch_name, branch_created, stashed)
            for f in findings:
                db.update_finding_status(f["id"], FindingStatus.FAILED)
            return

        # Step 4: Write test files
        logger.info("Batch %s: writing test files", batch_id)
        test_files = write_test_files(repo_path, findings)
        logger.info("Batch %s: wrote %d test files", batch_id, len(test_files))

        # Step 5: Build
        logger.info("Batch %s: running UE build", batch_id)
        db.update_batch(batch_id, status=BatchStatus.BUILDING)

        build_ok, build_log = run_ue_build(config)
        db.update_batch(batch_id, build_log=build_log)

        if not build_ok:
            logger.error("Batch %s: build failed", batch_id)
            db.update_batch(
                batch_id,
                status=BatchStatus.FAILED,
                completed_at=now_iso(),
            )
            cleanup_test_files(repo_path, findings)
            _cleanup(repo_path, branch_name, branch_created, stashed)
            for f in findings:
                db.update_finding_status(f["id"], FindingStatus.FAILED)
            return

        # Step 6: Run tests
        logger.info("Batch %s: running tests", batch_id)
        db.update_batch(batch_id, status=BatchStatus.TESTING)

        tests_ok, test_log, test_results = run_tests(config)
        db.update_batch(batch_id, test_log=test_log)

        if not tests_ok:
            logger.error("Batch %s: tests failed", batch_id)
            db.update_batch(
                batch_id,
                status=BatchStatus.FAILED,
                completed_at=now_iso(),
            )
            cleanup_test_files(repo_path, findings)
            _cleanup(repo_path, branch_name, branch_created, stashed)
            for f in findings:
                db.update_finding_status(f["id"], FindingStatus.FAILED)
            return

        # Step 7: Commit and PR
        logger.info("Batch %s: committing and creating PR", batch_id)
        finding_summaries = "\n".join(
            f"- [{f['severity']}] {f['title']} ({f['file_path']})"
            for f in findings
        )
        commit_msg = (
            f"fix: batch #{batch_id[:8]} - {len(findings)} findings resolved\n\n"
            f"Automated fixes by Code Auditor Agent:\n{finding_summaries}"
        )
        commit_sha = commit_changes(repo_path, commit_msg)
        db.update_batch(batch_id, commit_sha=commit_sha)

        pr_title = f"Auditor Batch #{batch_id[:8]}: {len(findings)} fixes"
        pr_body = (
            f"## Summary\n\n"
            f"Automated batch of {len(findings)} code fixes.\n\n"
            f"### Findings resolved:\n{finding_summaries}\n\n"
            f"### Verification\n"
            f"- UE build: PASSED\n"
            f"- Automated tests: {len(test_results)} tests passed\n\n"
            f"---\n"
            f"Generated by Code Auditor Agent"
        )
        pr_url = create_pr(repo_path, pr_title, pr_body)
        db.update_batch(batch_id, pr_url=pr_url)

        # Step 8: Mark everything verified
        db.update_batch(
            batch_id,
            status=BatchStatus.VERIFIED,
            completed_at=now_iso(),
        )
        for f in findings:
            db.update_finding_status(f["id"], FindingStatus.VERIFIED)

        # Step 9: Notify
        logger.info("Batch %s: sending notification", batch_id)
        title, message = format_batch_complete_message(
            db.get_batch(batch_id), findings
        )
        notify(config, title, message, pr_url=pr_url)

        # Step 10: Return to main
        checkout_main(repo_path)
        if stashed:
            stash_pop(repo_path)
            stashed = False

        logger.info("Batch %s: pipeline complete", batch_id)

    except Exception:
        logger.exception("Batch %s: unexpected error", batch_id)
        db.update_batch(
            batch_id,
            status=BatchStatus.FAILED,
            completed_at=now_iso(),
        )
        for f in findings:
            db.update_finding_status(f["id"], FindingStatus.FAILED)
        _cleanup(repo_path, branch_name, branch_created, stashed)


def _cleanup(repo_path: str, branch_name: str, branch_created: bool, stashed: bool):
    try:
        checkout_main(repo_path)
    except Exception:
        pass
    if branch_created:
        try:
            delete_branch(repo_path, branch_name)
        except Exception:
            pass
    if stashed:
        try:
            stash_pop(repo_path)
        except Exception:
            pass
