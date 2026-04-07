from __future__ import annotations

import logging
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)


def _run(args: list[str], cwd: str, **kwargs) -> subprocess.CompletedProcess:
    log.debug("git_ops: %s (cwd=%s)", " ".join(args), cwd)
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, **kwargs)


def get_default_branch(repo_path: str) -> str:
    """Return the configured default branch for the repo.

    Tries (in order):
      1. The symbolic ref of origin/HEAD (set when the repo was cloned)
      2. The name of the currently checked-out branch
      3. Falls back to "main"
    """
    # Try origin/HEAD first — most reliable for cloned repos
    result = _run(
        ["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
        cwd=repo_path,
    )
    if result.returncode == 0:
        ref = result.stdout.strip()
        # Returns "origin/main" — strip the remote prefix
        return ref.split("/", 1)[-1] if "/" in ref else ref

    # Fall back to the current branch name
    result = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_path)
    if result.returncode == 0:
        branch = result.stdout.strip()
        if branch and branch != "HEAD":
            return branch

    return "main"


def get_local_branches(repo_path: str) -> list[str]:
    """Return all local branch names, current branch first."""
    result = _run(["git", "branch", "--format=%(refname:short)"], cwd=repo_path)
    if result.returncode != 0:
        return []
    branches = [b.strip() for b in result.stdout.splitlines() if b.strip()]

    # Put the current branch first
    current = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_path)
    current_branch = current.stdout.strip() if current.returncode == 0 else ""
    if current_branch and current_branch in branches:
        branches = [current_branch] + [b for b in branches if b != current_branch]

    return branches


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


def create_branch(repo_path: str, branch_name: str, from_branch: str) -> None:
    result = _run(["git", "checkout", "-b", branch_name, from_branch], cwd=repo_path)
    if result.returncode != 0:
        raise RuntimeError(f"Failed to create branch {branch_name}: {result.stderr.strip()}")
    log.info("Created branch %s from %s", branch_name, from_branch)


def checkout_branch(repo_path: str, branch_name: str) -> None:
    """Check out an existing branch. Logs a warning if it fails (non-fatal)."""
    result = _run(["git", "checkout", branch_name], cwd=repo_path)
    if result.returncode != 0:
        log.warning("checkout %s: %s", branch_name, result.stderr.strip())


def delete_branch(repo_path: str, branch_name: str) -> None:
    result = _run(["git", "branch", "-D", branch_name], cwd=repo_path)
    if result.returncode != 0:
        log.warning("delete branch %s: %s", branch_name, result.stderr.strip())
    else:
        log.info("Deleted branch %s", branch_name)


def apply_patch(repo_path: str, patch_content: str) -> tuple[bool, str]:
    # Write to the system temp dir, NOT inside the repo, so git status/apply
    # never accidentally picks up the patch file as an untracked change.
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".patch", delete=False, dir=tempfile.gettempdir(), newline="\n"
    ) as f:
        f.write(patch_content)
        patch_path = f.name

    try:
        check = _run(["git", "apply", "--check", "--recount", patch_path], cwd=repo_path)
        if check.returncode != 0:
            return False, check.stderr.strip()

        apply = _run(["git", "apply", "--recount", patch_path], cwd=repo_path)
        if apply.returncode != 0:
            return False, apply.stderr.strip()

        log.info("Patch applied successfully")
        return True, ""
    finally:
        Path(patch_path).unlink(missing_ok=True)


def commit_changes(repo_path: str, message: str, files: list[str] | None = None) -> str:
    if files:
        _run(["git", "add", "--"] + files, cwd=repo_path)
    else:
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
