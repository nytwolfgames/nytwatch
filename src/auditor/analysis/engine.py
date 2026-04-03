from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

from pydantic import ValidationError

from auditor.analysis.schemas import ScanResult, BatchApplyResult
from auditor.analysis.prompts import build_scan_prompt, build_batch_apply_prompt
from auditor.models import new_id

log = logging.getLogger(__name__)


def call_claude(prompt: str, fast: bool = True, timeout: int = 600) -> str:
    if not prompt:
        raise ValueError("Empty prompt")

    call_id = new_id()[:8]
    log_dir = Path.home() / ".code-auditor" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    (log_dir / f"{call_id}_prompt.txt").write_text(prompt, encoding="utf-8")

    cmd = ["claude", "-p", "-", "--output-format", "json"]

    log.debug("Running claude CLI (timeout=%ds, prompt_len=%d)", timeout, len(prompt))
    log.info("Claude call %s: prompt_len=%d, timeout=%ds", call_id, len(prompt), timeout)

    from auditor.scan_state import canceller

    # Write prompt to a temp file and redirect stdin from it.
    # On Windows, piping large payloads via subprocess stdin (input=) can
    # deadlock or hang due to pipe buffer limits. Reading from a file avoids
    # that entirely — the OS streams directly without Python holding both
    # ends of a pipe open simultaneously.
    tf = tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8"
    )
    try:
        tf.write(prompt)
        tf.flush()
        tf.close()

        t0 = time.time()
        try:
            with open(tf.name, "r", encoding="utf-8") as stdin_file:
                proc = subprocess.Popen(
                    cmd,
                    stdin=stdin_file,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )

            canceller.register_process(proc)
            try:
                # Read stdout/stderr in background threads so we can poll
                # for cancellation and log progress at regular intervals.
                _stdout_buf: list[str] = []
                _stderr_buf: list[str] = []

                def _read(stream, buf):
                    buf.append(stream.read())

                t_out = threading.Thread(target=_read, args=(proc.stdout, _stdout_buf), daemon=True)
                t_err = threading.Thread(target=_read, args=(proc.stderr, _stderr_buf), daemon=True)
                t_out.start()
                t_err.start()

                check_interval = 30  # seconds between status log lines
                deadline = t0 + timeout

                while True:
                    t_out.join(timeout=check_interval)
                    if not t_out.is_alive():
                        break  # stdout closed — process finished

                    elapsed = time.time() - t0

                    if canceller.is_cancelled:
                        proc.kill()
                        t_out.join()
                        t_err.join()
                        raise InterruptedError("Scan was cancelled")

                    if time.time() >= deadline:
                        proc.kill()
                        t_out.join()
                        t_err.join()
                        log.error("Claude CLI timed out after %ds (call %s)", timeout, call_id)
                        (log_dir / f"{call_id}_timeout.txt").write_text("", encoding="utf-8")
                        raise subprocess.TimeoutExpired(cmd, timeout)

                    log.info(
                        "Claude call %s: still running (%.0fs elapsed, %.0fs remaining)",
                        call_id, elapsed, deadline - time.time(),
                    )

                t_err.join()
                proc.wait()

                stdout = _stdout_buf[0] if _stdout_buf else ""
                stderr = _stderr_buf[0] if _stderr_buf else ""

            finally:
                canceller.unregister_process()

            if canceller.is_cancelled:
                raise InterruptedError("Scan was cancelled")

        except FileNotFoundError:
            log.error("Claude CLI not found — is 'claude' on PATH?")
            raise
    finally:
        os.unlink(tf.name)

    # Build a result-like object matching what subprocess.run returns
    class _Result:
        def __init__(self, returncode, out, err):
            self.returncode = returncode
            self.stdout = out
            self.stderr = err

    result = _Result(proc.returncode, stdout, stderr)

    elapsed = time.time() - t0

    # Surface stderr from the Claude CLI into the Python logger so it appears
    # in the per-scan log viewer regardless of exit code.
    if result.stderr and result.stderr.strip():
        for line in result.stderr.strip().splitlines():
            if line.strip():
                log.info("[claude] %s", line.strip())

    if result.returncode != 0:
        log.error(
            "Claude CLI exited %d (call %s)",
            result.returncode,
            call_id,
        )
        raise subprocess.CalledProcessError(
            result.returncode, cmd, result.stdout, result.stderr
        )

    (log_dir / f"{call_id}_response.txt").write_text(result.stdout, encoding="utf-8")
    log.info("Claude call %s: completed in %.1fs, response_len=%d", call_id, elapsed, len(result.stdout))

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


def analyze_system(
    system_name: str,
    file_contents: dict[str, str],
    fast: bool = True,
    max_retries: int = 2,
) -> Optional[ScanResult]:
    if not file_contents:
        log.warning("No files provided for system '%s', skipping", system_name)
        return None

    prompt = build_scan_prompt(system_name, file_contents)
    if not prompt:
        log.error("Failed to build scan prompt for '%s'", system_name)
        return None

    for attempt in range(1, max_retries + 1):
        log.info(
            "Scanning system '%s' (attempt %d/%d, files=%d)",
            system_name,
            attempt,
            max_retries,
            len(file_contents),
        )

        try:
            raw = call_claude(prompt, fast=fast)
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
    file_contents: dict[str, str],
    max_retries: int = 2,
) -> Optional[BatchApplyResult]:
    if not findings:
        log.warning("No findings provided for batch patch")
        return None
    if not file_contents:
        log.warning("No file contents provided for batch patch")
        return None

    prompt = build_batch_apply_prompt(findings, file_contents)
    if not prompt:
        log.error("Failed to build batch apply prompt")
        return None

    for attempt in range(1, max_retries + 1):
        log.info(
            "Generating batch patch (attempt %d/%d, findings=%d, files=%d)",
            attempt,
            max_retries,
            len(findings),
            len(file_contents),
        )

        try:
            raw = call_claude(prompt, fast=False)
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
