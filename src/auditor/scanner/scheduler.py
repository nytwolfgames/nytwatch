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
from auditor.scanner.chunker import collect_system_files, chunk_system
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
        log.info("Starting incremental scan")
        return run_incremental_scan(config, db)
    elif scan_type == "full":
        if system_name is None:
            system_name = get_next_rotation_system(config, db)
        log.info("Starting full scan for system '%s'", system_name)
        return run_full_system_scan(config, db, system_name)
    else:
        raise ValueError(f"Unknown scan_type: {scan_type!r}. Expected 'incremental' or 'full'.")


def run_full_system_scan(
    config: AuditorConfig,
    db: Database,
    system_name: str,
) -> str:
    detect_source_dirs(config.repo_path, db)

    scan_id = new_id()

    system = next((s for s in config.systems if s.name == system_name), None)
    if system is None:
        log.error("System '%s' not found in config", system_name)
        scan = Scan(
            id=scan_id,
            scan_type=ScanType.FULL,
            system_name=system_name,
            status=ScanStatus.FAILED,
            completed_at=now_iso(),
        )
        db.insert_scan(scan)
        return scan_id

    try:
        current_commit = get_current_commit(config.repo_path)
    except Exception:
        current_commit = ""
        log.warning("Could not determine current commit")

    scan = Scan(
        id=scan_id,
        scan_type=ScanType.FULL,
        system_name=system_name,
        base_commit=current_commit,
    )
    db.insert_scan(scan)

    from auditor.scan_state import ScanLogHandler
    from auditor.ws_manager import manager as ws_manager
    _log_handler = ScanLogHandler(scan_id, db)
    _log_handler.setFormatter(logging.Formatter("%(message)s"))
    logging.getLogger("auditor").addHandler(_log_handler)

    ws_manager.push_scan_status(running=True, scan=db.get_scan(scan_id), cancelling=False)

    try:
        file_contents = collect_system_files(
            config.repo_path, system, config.file_extensions
        )
        total_files = len(file_contents)

        if not file_contents:
            log.info("No files found for system '%s'", system_name)
            db.update_scan(
                scan_id,
                status=ScanStatus.COMPLETED,
                completed_at=now_iso(),
                files_scanned=0,
                findings_count=0,
            )
            return scan_id

        findings_count = _process_system(
            system_name, config, db, scan_id, config.claude_fast_mode
        )

        if findings_count == -1:
            log.error("Full scan %s: analysis failed entirely for '%s'", scan_id, system_name)
            db.update_scan(
                scan_id,
                status=ScanStatus.FAILED,
                completed_at=now_iso(),
                files_scanned=total_files,
                findings_count=0,
            )
        else:
            db.update_scan(
                scan_id,
                status=ScanStatus.COMPLETED,
                completed_at=now_iso(),
                files_scanned=total_files,
                findings_count=findings_count,
            )
            log.info(
                "Full scan %s complete for '%s': %d files, %d findings",
                scan_id, system_name, total_files, findings_count,
            )

    except InterruptedError:
        log.info("Full scan %s was cancelled", scan_id)
        db.update_scan(
            scan_id,
            status=ScanStatus.CANCELLED,
            completed_at=now_iso(),
        )
    except Exception:
        log.exception("Full scan %s failed for system '%s'", scan_id, system_name)
        db.update_scan(
            scan_id,
            status=ScanStatus.FAILED,
            completed_at=now_iso(),
        )
    finally:
        logging.getLogger("auditor").removeHandler(_log_handler)
        ws_manager.push_scan_status(running=False, scan=db.get_scan(scan_id), cancelling=False)

    return scan_id


def get_next_rotation_system(config: AuditorConfig, db: Database) -> str:
    if not config.systems:
        raise ValueError("No systems defined in config")

    raw = db.get_config("rotation_index", "0")
    try:
        idx = int(raw)
    except ValueError:
        idx = 0

    idx = idx % len(config.systems)
    system_name = config.systems[idx].name

    next_idx = (idx + 1) % len(config.systems)
    db.set_config("rotation_index", str(next_idx))

    log.info("Rotation: selected system '%s' (index %d)", system_name, idx)
    return system_name
