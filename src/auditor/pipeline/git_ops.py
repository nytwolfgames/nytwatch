from __future__ import annotations

import logging
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)


def _run(args: list[str], cwd: str, **kwargs) -> subprocess.CompletedProcess:
    log.debug("git_ops: %s (cwd=%s)", " ".join(args), cwd)
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, **kwargs)


def stash_changes(repo_path: str) -> bool:
    result = _run(["git", "stash"], cwd=repo_path)
    stashed = "No local changes" not in result.stdout
    if stashed:
        log.info("Stashed local changes")
    return stashed


def stash_pop(repo_path: str) -> None:
    result = _run(["git", "stash", "pop"], cwd=repo_path)
    if result.returncode != 0 and "No stash entries" not in result.stderr:
        log.warning("stash pop warning: %s", result.stderr.strip())


def create_branch(repo_path: str, branch_name: str) -> None:
    result = _run(["git", "checkout", "-b", branch_name, "main"], cwd=repo_path)
    if result.returncode != 0:
        raise RuntimeError(f"Failed to create branch {branch_name}: {result.stderr.strip()}")
    log.info("Created branch %s", branch_name)


def checkout_main(repo_path: str) -> None:
    result = _run(["git", "checkout", "main"], cwd=repo_path)
    if result.returncode != 0:
        log.warning("checkout main: %s", result.stderr.strip())


def delete_branch(repo_path: str, branch_name: str) -> None:
    result = _run(["git", "branch", "-D", branch_name], cwd=repo_path)
    if result.returncode != 0:
        log.warning("delete branch %s: %s", branch_name, result.stderr.strip())
    else:
        log.info("Deleted branch %s", branch_name)


def apply_patch(repo_path: str, patch_content: str) -> tuple[bool, str]:
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".patch", delete=False, dir=repo_path
    ) as f:
        f.write(patch_content)
        patch_path = f.name

    try:
        check = _run(["git", "apply", "--check", patch_path], cwd=repo_path)
        if check.returncode != 0:
            return False, check.stderr.strip()

        apply = _run(["git", "apply", patch_path], cwd=repo_path)
        if apply.returncode != 0:
            return False, apply.stderr.strip()

        log.info("Patch applied successfully")
        return True, ""
    finally:
        Path(patch_path).unlink(missing_ok=True)


def commit_changes(repo_path: str, message: str) -> str:
    _run(["git", "add", "-A"], cwd=repo_path)
    result = _run(["git", "commit", "-m", message], cwd=repo_path)
    if result.returncode != 0:
        raise RuntimeError(f"Commit failed: {result.stderr.strip()}")

    sha_result = _run(["git", "rev-parse", "HEAD"], cwd=repo_path)
    sha = sha_result.stdout.strip()
    log.info("Committed %s: %s", sha[:8], message)
    return sha


def create_pr(repo_path: str, title: str, body: str) -> str:
    result = _run(
        ["gh", "pr", "create", "--title", title, "--body", body],
        cwd=repo_path,
    )
    if result.returncode != 0:
        raise RuntimeError(f"PR creation failed: {result.stderr.strip()}")

    pr_url = result.stdout.strip()
    log.info("Created PR: %s", pr_url)
    return pr_url


def get_current_commit(repo_path: str) -> str:
    result = _run(["git", "rev-parse", "HEAD"], cwd=repo_path)
    if result.returncode != 0:
        raise RuntimeError(f"Failed to get current commit: {result.stderr.strip()}")
    return result.stdout.strip()
