from __future__ import annotations

import logging
from typing import Optional

from auditor.config import AuditorConfig
from auditor.database import Database
from auditor.models import (
    Scan,
    ScanStatus,
    ScanType,
    new_id,
    now_iso,
)
from auditor.scanner.incremental import run_incremental_scan, get_current_commit, _process_system
from auditor.scanner.source_detector import detect_source_dirs

log = logging.getLogger(__name__)


def run_scan(
    config: AuditorConfig,
    db: Database,
    scan_type: str = "incremental",
    system_name: Optional[str] = None,
) -> str:
    if scan_type == "incremental":
        log.info("Starting incremental scan%s", f" for '{system_name}'" if system_name else "")
        return run_incremental_scan(config, db, system_name=system_name)
    elif scan_type in ("full", "rotation"):
        # "rotation" is an alias for a scheduled full scan
        log.info("Starting %s scan%s", scan_type, f" for '{system_name}'" if system_name else "")
        return run_full_scan(config, db, system_name=system_name)
    else:
        raise ValueError(f"Unknown scan_type: {scan_type!r}. Expected 'incremental', 'full', or 'rotation'.")


def run_full_scan(
    config: AuditorConfig,
    db: Database,
    system_name: Optional[str] = None,
) -> str:
    """Run a full scan across all systems (or a single named system).

    Each system is analysed in turn; findings_count and files_scanned on the
    scan record are updated after every system so the UI reflects progress.
    """
    from auditor.scan_state import ScanLogHandler
    from auditor.ws_manager import manager as ws_manager

    # Determine which systems to scan
    from auditor.config import SystemDef
    all_systems = [SystemDef(**s) for s in db.list_systems()]

    if system_name:
        systems = [s for s in all_systems if s.name == system_name]
        if not systems:
            log.error("System '%s' not found in database", system_name)
            scan_id = new_id()
            db.insert_scan(Scan(
                id=scan_id,
                scan_type=ScanType.FULL,
                system_name=system_name,
                status=ScanStatus.FAILED,
                completed_at=now_iso(),
            ))
            return scan_id
        label = system_name
    else:
        systems = all_systems
        label = None  # UI shows "All"

    scan_id = new_id()

    # Insert immediately so the UI shows the scan as running right away
    db.insert_scan(Scan(
        id=scan_id,
        scan_type=ScanType.FULL,
        system_name=label,
        base_commit="",
    ))

    _log_handler = ScanLogHandler(scan_id, db)
    _log_handler.setFormatter(logging.Formatter("%(message)s"))
    logging.getLogger("auditor").addHandler(_log_handler)
    ws_manager.push_scan_status(running=True, scan=db.get_scan(scan_id), cancelling=False)

    total_findings = 0
    total_files = 0
    systems_attempted = 0
    systems_failed = 0

    try:
        try:
            current_commit = get_current_commit(config.repo_path)
            db.update_scan(scan_id, base_commit=current_commit)
        except Exception:
            log.warning("Could not determine current commit")

        detect_source_dirs(config.repo_path, db)

        for system in systems:
            systems_attempted += 1
            log.info("Full scan: processing system '%s' (%d/%d)", system.name, systems_attempted, len(systems))

            findings_count, files_scanned = _process_system(
                system.name, config, db, scan_id, config.claude_fast_mode
            )

            if findings_count == -1:
                systems_failed += 1
                log.error("Full scan: system '%s' failed entirely", system.name)
            else:
                total_findings += findings_count
                total_files += files_scanned
                log.info(
                    "Full scan: system '%s' done — %d files, %d findings",
                    system.name, files_scanned, findings_count,
                )

            # Update the scan record after each system so the UI stays current
            db.update_scan(
                scan_id,
                files_scanned=total_files,
                findings_count=total_findings,
            )

        all_failed = systems_attempted > 0 and systems_failed == systems_attempted
        final_status = ScanStatus.FAILED if all_failed else ScanStatus.COMPLETED

        if all_failed:
            log.error("Full scan %s: ALL %d systems failed", scan_id, systems_attempted)
        else:
            log.info(
                "Full scan %s complete: %d systems, %d files, %d findings (%d failed)",
                scan_id, systems_attempted, total_files, total_findings, systems_failed,
            )

        db.update_scan(
            scan_id,
            status=final_status,
            completed_at=now_iso(),
            files_scanned=total_files,
            findings_count=total_findings,
        )

    except InterruptedError:
        log.info("Full scan %s was cancelled", scan_id)
        db.update_scan(
            scan_id,
            status=ScanStatus.CANCELLED,
            completed_at=now_iso(),
            files_scanned=total_files,
            findings_count=total_findings,
        )
    except Exception:
        log.exception("Full scan %s failed", scan_id)
        db.update_scan(
            scan_id,
            status=ScanStatus.FAILED,
            completed_at=now_iso(),
            files_scanned=total_files,
            findings_count=total_findings,
        )
    finally:
        logging.getLogger("auditor").removeHandler(_log_handler)
        ws_manager.push_scan_status(running=False, scan=db.get_scan(scan_id), cancelling=False)

    return scan_id
