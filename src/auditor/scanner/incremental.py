from __future__ import annotations

import hashlib
import logging
import subprocess
from typing import Optional

from auditor.config import AuditorConfig, SystemDef
from auditor.paths import normalize_path
from auditor.database import Database
from auditor.models import (
    Category,
    Confidence,
    Finding,
    FindingSource,
    ScanStatus,
    ScanType,
    Scan,
    Severity,
    new_id,
    now_iso,
)
from auditor.analysis.engine import analyze_system
from auditor.scanner.chunker import (
    collect_system_files,
    collect_specific_files,
    chunk_system,
    build_neighbourhood,
)
from auditor.scanner.source_detector import detect_source_dirs

log = logging.getLogger(__name__)


def get_current_commit(repo_path: str) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def get_changed_files(
    repo_path: str,
    since_commit: str,
    extensions: list[str],
) -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--name-only", since_commit, "HEAD"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=True,
    )
    files = [f for f in result.stdout.strip().splitlines() if f]
    return [f for f in files if any(f.endswith(ext) for ext in extensions)]


def map_files_to_systems(
    changed_files: list[str],
    systems: list[SystemDef],
) -> dict[str, list[str]]:
    mapping: dict[str, list[str]] = {}

    for fpath in changed_files:
        norm_fpath = normalize_path(fpath)
        matched = False
        for system in systems:
            for prefix in system.paths:
                norm_prefix = normalize_path(prefix)
                normalized = norm_prefix.rstrip("/") + "/"
                if norm_fpath.startswith(normalized) or norm_fpath.startswith(norm_prefix):
                    mapping.setdefault(system.name, []).append(fpath)
                    matched = True
                    break
            if matched:
                break
        if not matched:
            mapping.setdefault("__uncategorized", []).append(fpath)

    return mapping


def _compute_fingerprint(
    file_path: str,
    line_range: str,
    category: str,
    title: str,
) -> str:
    raw = f"{file_path}|{line_range}|{category}|{title}"
    return hashlib.md5(raw.encode()).hexdigest()


def _process_system(
    system_name: str,
    config: AuditorConfig,
    db: Database,
    scan_id: str,
    fast: bool,
    changed_files: Optional[list[str]] = None,
) -> tuple[int, int]:
    """Return (findings_count, files_scanned). findings_count == -1 means total failure."""
    system = next((s for s in config.systems if s.name == system_name), None)
    if system is None:
        log.warning("System '%s' not found in config, skipping", system_name)
        return 0

    if changed_files is not None:
        # Incremental: collect whole system for include resolution, build
        # neighbourhood around changed files, then chunk semantically.
        all_system_files = collect_system_files(
            config.repo_path, system, config.file_extensions
        )
        if not all_system_files:
            log.info("No files found for system '%s'", system_name)
            return 0, 0
        file_contents = build_neighbourhood(
            changed_files, all_system_files, config.repo_path
        )
        if not file_contents:
            log.info("No neighbourhood files resolved for system '%s'", system_name)
            return 0, 0
    else:
        # Full scan: collect all system files
        file_contents = collect_system_files(
            config.repo_path, system, config.file_extensions
        )
        if not file_contents:
            log.info("No files found for system '%s'", system_name)
            return 0, 0

    # Always chunk semantically — handles both incremental (neighbourhood may
    # span multiple chunks) and full scans
    chunks = chunk_system(file_contents, repo_path=config.repo_path)
    from auditor.ws_manager import manager as ws_manager

    findings_count = 0
    chunks_failed = 0

    for i, chunk in enumerate(chunks):
        log.info(
            "Analyzing system '%s' chunk %d/%d (%d files)",
            system_name, i + 1, len(chunks), len(chunk),
        )
        result = analyze_system(
            system_name=system_name,
            file_contents=chunk,
            fast=fast,
            max_retries=2,
        )
        if result is None:
            log.error("Analysis returned None for system '%s' chunk %d", system_name, i + 1)
            chunks_failed += 1
            continue

        chunk_new = 0
        for fo in result.findings:
            line_range = f"{fo.line_start}-{fo.line_end}"
            fingerprint = _compute_fingerprint(
                fo.file_path, line_range, fo.category, fo.title
            )

            if db.has_fingerprint(fingerprint):
                log.debug("Duplicate fingerprint, skipping: %s", fo.title)
                continue

            source_type = db.classify_path(fo.file_path)
            finding = Finding(
                scan_id=scan_id,
                title=fo.title,
                description=fo.description,
                severity=Severity(fo.severity),
                category=Category(fo.category),
                confidence=Confidence(fo.confidence),
                file_path=fo.file_path,
                line_start=fo.line_start,
                line_end=fo.line_end,
                code_snippet=fo.code_snippet,
                suggested_fix=fo.suggested_fix,
                fix_diff=fo.fix_diff,
                can_auto_fix=fo.can_auto_fix,
                reasoning=fo.reasoning,
                test_code=fo.test_code,
                test_description=fo.test_description,
                source=FindingSource(source_type),
                fingerprint=fingerprint,
            )
            db.insert_finding(finding)
            findings_count += 1
            chunk_new += 1

        log.info(
            "System '%s' chunk %d/%d: %d new finding(s) (%d total so far)",
            system_name, i + 1, len(chunks), chunk_new, findings_count,
        )
        ws_manager.push_findings_update(
            scan_id=scan_id,
            system=system_name,
            chunk=i + 1,
            total_chunks=len(chunks),
            chunk_findings=chunk_new,
            total_findings=findings_count,
        )

    files_scanned = len(file_contents)

    if chunks_failed == len(chunks):
        log.error("ALL chunks failed for system '%s' — returning -1", system_name)
        return -1, files_scanned

    if chunks_failed > 0:
        log.warning(
            "System '%s': %d/%d chunks failed, %d findings from successful chunks",
            system_name, chunks_failed, len(chunks), findings_count,
        )

    return findings_count, files_scanned


def run_incremental_scan(config: AuditorConfig, db: Database) -> str:
    # Insert the scan record immediately so the UI can show it as running
    # before any slow setup work (source detection, git calls) begins.
    scan_id = new_id()
    scan = Scan(
        id=scan_id,
        scan_type=ScanType.INCREMENTAL,
        base_commit="",
    )
    db.insert_scan(scan)

    from auditor.scan_state import ScanLogHandler
    from auditor.ws_manager import manager as ws_manager
    _log_handler = ScanLogHandler(scan_id, db)
    _log_handler.setFormatter(logging.Formatter("%(message)s"))
    logging.getLogger("auditor").addHandler(_log_handler)

    ws_manager.push_scan_status(running=True, scan=db.get_scan(scan_id), cancelling=False)

    try:
        detect_source_dirs(config.repo_path, db)

        current_commit = get_current_commit(config.repo_path)
        last_commit = db.get_config("last_scan_commit")

        if not last_commit:
            log.warning("No previous scan commit found. Running against HEAD~20 as baseline.")
            try:
                result = subprocess.run(
                    ["git", "rev-parse", "HEAD~20"],
                    cwd=config.repo_path,
                    capture_output=True,
                    text=True,
                    check=True,
                )
                last_commit = result.stdout.strip()
            except subprocess.CalledProcessError:
                log.error("Could not determine baseline commit. Aborting incremental scan.")
                db.update_scan(scan_id, status=ScanStatus.FAILED, completed_at=now_iso())
                return scan_id

        db.update_scan(scan_id, base_commit=last_commit)
    except Exception:
        log.exception("Incremental scan %s failed during setup", scan_id)
        db.update_scan(scan_id, status=ScanStatus.FAILED, completed_at=now_iso())
        return scan_id

    try:
        changed = get_changed_files(
            config.repo_path, last_commit, config.file_extensions
        )
        log.info("Found %d changed files since %s", len(changed), last_commit[:8])

        if not changed:
            log.info("No relevant file changes detected.")
            db.update_scan(
                scan_id,
                status=ScanStatus.COMPLETED,
                completed_at=now_iso(),
                files_scanned=0,
                findings_count=0,
            )
            db.set_config("last_scan_commit", current_commit)
            return scan_id

        system_map = map_files_to_systems(changed, config.systems)
        total_findings = 0
        total_files = len(changed)
        systems_attempted = 0
        systems_failed = 0

        for system_name in system_map:
            if system_name == "__uncategorized":
                log.info("Skipping %d uncategorized files", len(system_map[system_name]))
                continue
            systems_attempted += 1
            count, _files = _process_system(
                system_name, config, db, scan_id, config.claude_fast_mode,
                changed_files=system_map[system_name],
            )
            if count == -1:
                systems_failed += 1
                log.error(
                    "System '%s' failed — stopping scan early to avoid wasting further calls",
                    system_name,
                )
                break
            else:
                total_findings += count

        all_failed = systems_attempted > 0 and systems_failed == systems_attempted
        final_status = ScanStatus.FAILED if all_failed else ScanStatus.COMPLETED

        if all_failed:
            log.error(
                "Incremental scan %s: ALL %d systems failed analysis",
                scan_id, systems_attempted,
            )

        db.update_scan(
            scan_id,
            status=final_status,
            completed_at=now_iso(),
            files_scanned=total_files,
            findings_count=total_findings,
        )
        db.set_config("last_scan_commit", current_commit)
        log.info(
            "Incremental scan %s %s: %d files, %d findings, %d/%d systems failed",
            scan_id, final_status.value, total_files, total_findings,
            systems_failed, systems_attempted,
        )

    except InterruptedError:
        log.info("Incremental scan %s was cancelled", scan_id)
        db.update_scan(
            scan_id,
            status=ScanStatus.CANCELLED,
            completed_at=now_iso(),
        )
    except Exception:
        log.exception("Incremental scan %s failed", scan_id)
        db.update_scan(
            scan_id,
            status=ScanStatus.FAILED,
            completed_at=now_iso(),
        )
    finally:
        logging.getLogger("auditor").removeHandler(_log_handler)
        ws_manager.push_scan_status(running=False, scan=db.get_scan(scan_id), cancelling=False)

    return scan_id
