from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)


def _detect_project_name(repo_path: str) -> str:
    repo = Path(repo_path)
    uproject_files = list(repo.glob("*.uproject"))
    if not uproject_files:
        raise FileNotFoundError(f"No .uproject file found in {repo_path}")
    return uproject_files[0].stem


def write_test_files(repo_path: str, findings: list[dict]) -> list[str]:
    project_name = _detect_project_name(repo_path)
    test_dir = Path(repo_path) / "Source" / project_name / "Tests" / "Auditor"
    test_dir.mkdir(parents=True, exist_ok=True)

    written: list[str] = []
    test_names: list[str] = []

    for finding in findings:
        test_code = finding.get("test_code")
        if not test_code:
            continue

        finding_id = finding.get("id", "unknown")
        test_name = f"Test_{finding_id}"
        test_file = test_dir / f"{test_name}.cpp"
        test_file.write_text(test_code, encoding="utf-8")
        written.append(str(test_file))
        test_names.append(test_name)
        log.info("Wrote test file: %s", test_file)

    if test_names:
        header_file = test_dir / "AuditorTests.h"
        lines = [
            "#pragma once",
            "",
            '#include "CoreMinimal.h"',
            '#include "Misc/AutomationTest.h"',
            "",
        ]
        for name in test_names:
            lines.append(f'#include "{name}.cpp"')
        lines.append("")
        header_file.write_text("\n".join(lines), encoding="utf-8")
        written.append(str(header_file))
        log.info("Wrote test header: %s", header_file)

    return written


def cleanup_test_files(repo_path: str, findings: list[dict]) -> None:
    project_name = _detect_project_name(repo_path)
    test_dir = Path(repo_path) / "Source" / project_name / "Tests" / "Auditor"

    for finding in findings:
        finding_id = finding.get("id", "unknown")
        test_file = test_dir / f"Test_{finding_id}.cpp"
        if test_file.exists():
            test_file.unlink()
            log.info("Removed test file: %s", test_file)

    header_file = test_dir / "AuditorTests.h"
    if header_file.exists():
        header_file.unlink()
        log.info("Removed test header: %s", header_file)
