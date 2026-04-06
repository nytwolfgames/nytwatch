from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

from pydantic import ValidationError

from nytwatch.analysis.schemas import ScanResult, BatchApplyResult
from nytwatch.analysis.prompts import build_scan_prompt, build_batch_apply_prompt, build_finding_chat_prompt, build_recheck_prompt
from nytwatch.models import new_id

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Permission-skip flag — discovered lazily on first tool-using call.
#   None  → not yet tried
#   ""    → confirmed unsupported; omit the flag entirely
#   str   → confirmed working flag
# ---------------------------------------------------------------------------
_skip_perms_flag: Optional[str] = None  # None = not yet confirmed

# Model used for fast (incremental) scans.  Haiku is significantly cheaper and
# faster than Sonnet while still producing accurate findings for focused diffs.
# Set to None to always use the CLI default model for every call.
_FAST_MODEL = "claude-haiku-4-5"


def call_claude(
    prompt: str,
    fast: bool = True,
    timeout: int = 600,
    repo_path: str = None,
    use_tools: bool = True,
) -> str:
    """Invoke the Claude CLI.

    Args:
        use_tools: When True (default) Claude is expected to call file-reading
            tools, so the permission-skip flag is added.  Pass False for
            text-only calls (e.g. suggest-systems) where no tools are needed
            and the flag is irrelevant.
    """
    # global must appear before any read or write of the module-level variable
    global _skip_perms_flag

    if not prompt:
        raise ValueError("Empty prompt")

    call_id = new_id()[:8]
    log_dir = Path.home() / ".nytwatch" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / f"{call_id}_prompt.txt").write_text(prompt, encoding="utf-8")

    def _build_cmd(perms_flag: Optional[str]) -> list[str]:
        claude_exe = shutil.which("claude") or ("claude.cmd" if sys.platform == "win32" else "claude")
        base = [claude_exe, "-p", "-", "--output-format", "json"]
        if fast and _FAST_MODEL:
            base.extend(["--model", _FAST_MODEL])
        if use_tools and perms_flag:
            base.append(perms_flag)
        return base

    # On the first tool-using call, try the standard flag.
    # _run_cmd below will detect "unknown option" and retry without it.
    cmd = _build_cmd(
        "--dangerously-skip-permissions" if _skip_perms_flag is None else _skip_perms_flag
    )

    log.debug("Running claude CLI (timeout=%ds, prompt_len=%d)", timeout, len(prompt))
    log.info(
        "Claude call %s: prompt_len=%d, timeout=%ds, cwd=%s",
        call_id, len(prompt), timeout, repo_path or ".",
    )

    from nytwatch.scan_state import canceller

    class _Result:
        def __init__(self, returncode: int, out: str, err: str) -> None:
            self.returncode = returncode
            self.stdout = out
            self.stderr = err

    def _run_cmd(command: list[str], deadline: float) -> _Result:
        """Write prompt to a temp file, run command, respect timeout + cancellation."""
        # On Windows, piping large payloads via subprocess stdin (input=) can
        # deadlock due to pipe buffer limits.  Writing to a temp file avoids that.
        tf = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8")
        try:
            tf.write(prompt); tf.flush(); tf.close()
            popen_kwargs: dict = {}
            if repo_path:
                popen_kwargs["cwd"] = repo_path
            with open(tf.name, "r", encoding="utf-8") as stdin_file:
                proc = subprocess.Popen(
                    command,
                    stdin=stdin_file,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    **popen_kwargs,
                )
            canceller.register_process(proc)
            try:
                stdout_buf: list[str] = []
                stderr_buf: list[str] = []

                def _read(stream, buf):
                    buf.append(stream.read())

                t_out = threading.Thread(target=_read, args=(proc.stdout, stdout_buf), daemon=True)
                t_err = threading.Thread(target=_read, args=(proc.stderr, stderr_buf), daemon=True)
                t_out.start()
                t_err.start()

                while True:
                    t_out.join(timeout=30)
                    if not t_out.is_alive():
                        break  # process finished

                    if canceller.is_cancelled:
                        proc.kill(); t_out.join(); t_err.join()
                        raise InterruptedError("Scan was cancelled")

                    if time.time() >= deadline:
                        proc.kill(); t_out.join(); t_err.join()
                        log.error("Claude CLI timed out after %ds (call %s)", timeout, call_id)
                        (log_dir / f"{call_id}_timeout.txt").write_text("", encoding="utf-8")
                        raise subprocess.TimeoutExpired(command, timeout)

                    elapsed = time.time() - (deadline - timeout)
                    log.info(
                        "Claude call %s: still running (%.0fs elapsed, %.0fs remaining)",
                        call_id, elapsed, deadline - time.time(),
                    )

                t_err.join()
                proc.wait()

                if canceller.is_cancelled:
                    raise InterruptedError("Scan was cancelled")

                return _Result(
                    proc.returncode,
                    stdout_buf[0] if stdout_buf else "",
                    stderr_buf[0] if stderr_buf else "",
                )
            finally:
                canceller.unregister_process(proc)
        except FileNotFoundError:
            log.error("Claude CLI not found — is 'claude' on PATH?")
            raise
        finally:
            os.unlink(tf.name)

    t0 = time.time()
    result = _run_cmd(cmd, t0 + timeout)

    # Surface Claude's stderr into the Python logger
    if result.stderr.strip():
        for line in result.stderr.strip().splitlines():
            if line.strip():
                log.info("[claude] %s", line.strip())

    # If the permission flag is unsupported, learn that and retry once without it
    if (
        result.returncode != 0
        and use_tools
        and _skip_perms_flag is None
        and "unknown option" in result.stderr.lower()
    ):
        log.warning(
            "Claude CLI does not support '--dangerously-skip-permissions' — "
            "retrying without permission flag"
        )
        _skip_perms_flag = ""
        cmd = _build_cmd("")
        result = _run_cmd(cmd, t0 + timeout)
        if result.stderr.strip():
            for line in result.stderr.strip().splitlines():
                if line.strip():
                    log.info("[claude] %s", line.strip())

    if result.returncode != 0:
        log.error("Claude CLI exited %d (call %s)", result.returncode, call_id)
        raise subprocess.CalledProcessError(
            result.returncode, cmd, result.stdout, result.stderr
        )

    # Confirm the flag is supported so future calls skip the retry path
    if use_tools and _skip_perms_flag is None:
        _skip_perms_flag = "--dangerously-skip-permissions"

    (log_dir / f"{call_id}_response.txt").write_text(result.stdout, encoding="utf-8")
    log.info(
        "Claude call %s: completed in %.1fs, response_len=%d",
        call_id, time.time() - t0, len(result.stdout),
    )
    return result.stdout


def _strip_markdown_fences(text: str) -> str:
    """Remove markdown code fences from Claude's response.

    Handles both closed fences (```json...```) and unclosed fences
    where Claude omits the trailing ``` (common with large JSON output).
    """
    # Try closed fence first
    match = re.search(r'```(?:json)?\s*\n(.*?)```', text, re.DOTALL)
    if match:
        result = match.group(1).strip()
        log.debug("Stripped closed markdown fence (%d chars)", len(result))
        return result
    # Try unclosed fence (Claude often omits closing ```)
    match = re.search(r'```(?:json)?\s*\n(.*)', text, re.DOTALL)
    if match:
        result = match.group(1).strip()
        log.debug("Stripped unclosed markdown fence (%d chars)", len(result))
        return result
    log.debug("No markdown fences found in response")
    return text.strip()


def _extract_json(raw: str) -> dict:
    """Extract the inner JSON from Claude's --output-format json wrapper.

    The CLI wraps the response in a JSON envelope like:
      {"type":"result","result":"<escaped json string>", ...}
    The result string may contain markdown fences around the JSON.
    """
    try:
        envelope = json.loads(raw)
    except json.JSONDecodeError:
        log.debug("Raw output is not JSON, attempting direct parse")
        raise

    log.debug("Envelope keys: %s", list(envelope.keys()))

    if isinstance(envelope, dict) and "result" in envelope:
        inner = envelope["result"]
        if isinstance(inner, str):
            # Try direct parse first
            try:
                parsed = json.loads(inner)
                log.debug("JSON parsed directly from result string")
                return parsed
            except json.JSONDecodeError:
                pass
            # Strip markdown fences and retry
            stripped = _strip_markdown_fences(inner)
            try:
                parsed = json.loads(stripped)
                log.debug("JSON parsed after fence stripping")
                return parsed
            except json.JSONDecodeError:
                log.warning("Could not parse result as JSON even after stripping fences")
                log.debug("Result starts with: %s", inner[:200])
                return envelope
        if isinstance(inner, dict):
            return inner

    return envelope


def parse_and_validate(raw: str, schema_class):
    try:
        data = _extract_json(raw)
    except json.JSONDecodeError:
        log.error("Failed to parse JSON from Claude response")
        return None

    try:
        return schema_class.model_validate(data)
    except ValidationError as exc:
        log.warning(
            "Validation failed for %s: %s",
            schema_class.__name__,
            exc.error_count(),
        )
        log.debug("Validation errors: %s", exc.errors())
        return None


def _extract_text_result(raw: str) -> str:
    """Pull the plain-text ``result`` field out of the CLI JSON envelope."""
    try:
        envelope = json.loads(raw)
        if isinstance(envelope, dict):
            result = envelope.get("result", "")
            if isinstance(result, str):
                return result
    except json.JSONDecodeError:
        pass
    return raw


def _parse_chat_response(text: str) -> tuple[str, dict]:
    """Split a chat response into (display_text, updated_fields).

    If Claude appended a trailing ```json { ... } ``` block it is extracted and
    stripped from the display text.  Only the fields in the allow-list
    (suggested_fix, fix_diff, test_code, test_description) are returned.
    """
    updated_fields: dict = {}
    display_text = text.strip()

    match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```\s*$', display_text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(1))
            allowed = {"suggested_fix", "fix_diff", "test_code", "test_description"}
            for key in allowed:
                if key in data and data[key]:
                    updated_fields[key] = str(data[key])
        except (json.JSONDecodeError, ValueError):
            pass
        display_text = display_text[:match.start()].strip()

    return display_text, updated_fields


def run_finding_recheck(finding: dict, repo_path: str) -> tuple[bool, str]:
    """Ask Claude to verify the finding is still present in the current file.

    Returns:
        still_valid — True if the issue still exists, False if it has been fixed
        reason      — Claude's explanation
    """
    prompt = build_recheck_prompt(finding)
    raw = call_claude(prompt, fast=False, repo_path=repo_path, use_tools=True)
    text = _extract_text_result(raw).strip()

    # Claude may wrap in fences despite instructions
    text = _strip_markdown_fences(text) if text.startswith("```") else text

    try:
        data = json.loads(text)
        still_valid = bool(data.get("still_valid", True))
        reason = str(data.get("reason", ""))
        return still_valid, reason
    except (json.JSONDecodeError, ValueError):
        log.warning("Could not parse recheck JSON; assuming still valid. Raw: %s", text[:200])
        return True, text


def run_finding_chat(
    finding: dict,
    history: list[dict],
    user_message: str,
    repo_path: str,
) -> tuple[str, dict]:
    """Send a chat message about a finding to Claude.

    Returns:
        display_text   — conversational reply to show the user
        updated_fields — dict of finding fields Claude changed (may be empty)
    """
    prompt = build_finding_chat_prompt(finding, history, user_message)
    raw = call_claude(prompt, fast=False, repo_path=repo_path, use_tools=True)
    text = _extract_text_result(raw)
    return _parse_chat_response(text)


def analyze_system(
    system_name: str,
    file_paths: list[str],
    repo_path: str,
    fast: bool = True,
    max_retries: int = 2,
) -> Optional[ScanResult]:
    if not file_paths:
        log.warning("No files provided for system '%s', skipping", system_name)
        return None

    prompt = build_scan_prompt(system_name, file_paths)
    if not prompt:
        log.error("Failed to build scan prompt for '%s'", system_name)
        return None

    for attempt in range(1, max_retries + 1):
        log.info(
            "Scanning system '%s' (attempt %d/%d, files=%d)",
            system_name,
            attempt,
            max_retries,
            len(file_paths),
        )

        try:
            raw = call_claude(prompt, fast=fast, repo_path=repo_path)
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError, FileNotFoundError) as exc:
            log.error("Claude call failed on attempt %d: %s", attempt, exc)
            if attempt == max_retries:
                return None
            continue

        result = parse_and_validate(raw, ScanResult)
        if result is not None:
            log.info(
                "Scan complete for '%s': %d findings",
                system_name,
                len(result.findings),
            )
            return result

        log.warning(
            "Validation failed on attempt %d/%d for '%s'",
            attempt,
            max_retries,
            system_name,
        )

    log.error("All %d attempts failed for system '%s'", max_retries, system_name)
    return None


def generate_batch_patch(
    findings: list[dict],
    file_paths: list[str],
    repo_path: str,
    max_retries: int = 2,
) -> Optional[BatchApplyResult]:
    """Generate a unified patch for all approved findings using agent mode.

    Claude reads the affected files itself via the Read tool — no file contents
    are embedded in the prompt.  ``repo_path`` is set as the working directory
    so relative paths resolve correctly.
    """
    if not findings:
        log.warning("No findings provided for batch patch")
        return None
    if not file_paths:
        log.warning("No file paths provided for batch patch")
        return None

    prompt = build_batch_apply_prompt(findings, file_paths)
    if not prompt:
        log.error("Failed to build batch apply prompt")
        return None

    for attempt in range(1, max_retries + 1):
        log.info(
            "Generating batch patch (attempt %d/%d, findings=%d, files=%d)",
            attempt,
            max_retries,
            len(findings),
            len(file_paths),
        )

        try:
            raw = call_claude(prompt, fast=False, repo_path=repo_path, use_tools=True)
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError, FileNotFoundError) as exc:
            log.error("Claude call failed on attempt %d: %s", attempt, exc)
            if attempt == max_retries:
                return None
            continue

        result = parse_and_validate(raw, BatchApplyResult)
        if result is not None:
            log.info(
                "Batch patch generated: %d files modified",
                len(result.files_modified),
            )
            return result

        log.warning(
            "Validation failed on attempt %d/%d for batch patch",
            attempt,
            max_retries,
        )

    log.error("All %d attempts failed for batch patch generation", max_retries)
    return None
