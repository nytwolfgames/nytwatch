from __future__ import annotations

import logging
import re
import subprocess

from auditor.config import AuditorConfig

log = logging.getLogger(__name__)


def run_tests(config: AuditorConfig) -> tuple[bool, str, dict[str, bool]]:
    cmd = [
        config.build.ue_editor_cmd,
        config.build.project_file,
        '-ExecCmds=Automation RunTests Auditor',
        "-unattended",
        "-nopause",
        "-NullRHI",
        "-log",
    ]

    log.info("Running UE tests: %s", " ".join(cmd))

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=config.build.test_timeout_seconds,
        )
        output = result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        msg = f"Tests timed out after {config.build.test_timeout_seconds}s"
        log.error(msg)
        return False, msg, {}

    test_results = _parse_test_output(output)
    all_passed = all(test_results.values()) if test_results else result.returncode == 0

    if all_passed:
        log.info("All tests passed (%d tests)", len(test_results))
    else:
        failed = [k for k, v in test_results.items() if not v]
        log.error("Tests failed: %s", ", ".join(failed))

    return all_passed, output, test_results


def _parse_test_output(output: str) -> dict[str, bool]:
    results: dict[str, bool] = {}

    for line in output.splitlines():
        pass_match = re.search(r"Test Completed\.\s+(CodeAuditor\.\S+)\s+Success", line)
        if pass_match:
            results[pass_match.group(1)] = True
            continue

        fail_match = re.search(r"Test Completed\.\s+(CodeAuditor\.\S+)\s+Fail", line)
        if fail_match:
            results[fail_match.group(1)] = False
            continue

        success_match = re.search(r"\[Passed\]\s+(CodeAuditor\.\S+)", line)
        if success_match:
            results[success_match.group(1)] = True
            continue

        failure_match = re.search(r"\[Failed\]\s+(CodeAuditor\.\S+)", line)
        if failure_match:
            results[failure_match.group(1)] = False

    return results
