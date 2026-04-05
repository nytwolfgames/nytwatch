from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from nytwatch.config import AuditorConfig, SystemDef
from nytwatch.paths import normalize_path
from nytwatch.database import Database
from nytwatch.models import (
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
from nytwatch.analysis.engine import analyze_system
from nytwatch.scanner.chunker import (
    collect_system_files,
    list_system_files,
    chunk_paths_by_count,
    build_neighbourhood,
)
from nytwatch.scanner.source_detector import detect_source_dirs

log = logging.getLogger(__name__)

# Max parallel Claude processes per scan.
_MAX_PARALLEL_SYSTEMS = 4

# Files per agent chunk.
_CHUNK_SIZE = 40

# Confidence level ordering — higher number = higher confidence.
_CONFIDENCE_RANK: dict[str, int] = {"high": 2, "medium": 1, "low": 0}


def _meets_confidence(finding_confidence: str, min_confidence: str) -> bool:
    return (
        _CONFIDENCE_RANK.get(finding_confidence, 0)
        >= _CONFIDENCE_RANK.get(min_confidence, 1)
    )


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


def find_owning_system(file_path: str, systems: list[SystemDef]) -> Optional[str]:
    """Return the system name whose path prefix most specifically matches file_path."""
    norm_file = normalize_path(file_path)
    best_system: Optional[str] = None
    best_len = -1
    for system in systems:
        for prefix in system.paths:
            norm_prefix = normalize_path(prefix).rstrip("/") + "/"
            if norm_file.startswith(norm_prefix) and len(norm_prefix) > best_len:
                best_len = len(norm_prefix)
                best_system = system.name
    return best_system


def map_files_to_systems(
    changed_files: list[str],
    systems: list[SystemDef],
) -> dict[str, list[str]]:
    mapping: dict[str, list[str]] = {}
    for fpath in changed_files:
        owner = find_owning_system(fpath, systems)
        mapping.setdefault(owner or "__uncategorized", []).append(fpath)
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
    from nytwatch.config import SystemDef
    all_systems = [SystemDef(**s) for s in db.list_systems()]
    system = next((s for s in all_systems if s.name == system_name), None)
    if system is None:
        log.warning("System '%s' not found in database, skipping", system_name)
        return 0, 0

    # ── Per-system overrides (fall back to global config when not set) ───────
    effective_fast = system.claude_fast_mode if system.claude_fast_mode is not None else fast
    effective_extensions = system.file_extensions if system.file_extensions else config.file_extensions
    effective_min_confidence = system.min_confidence or config.min_confidence

    # ── Build source-dir classification cache (avoids N DB round-trips) ──────
    source_dirs = db.list_source_dirs()
    # Sort longest prefix first for correct longest-prefix-match classification
    source_dirs_sorted = sorted(
        source_dirs, key=lambda d: len(d["path"]), reverse=True
    )

    def _classify_path(file_path: str) -> str:
        norm = file_path.replace("\\", "/")
        for sd in source_dirs_sorted:
            stored = sd["path"].replace("\\", "/")
            prefix = stored.rstrip("/") + "/"
            if norm.startswith(prefix) or norm.startswith(stored):
                stype = sd["source_type"]
                return stype if stype == "ignored" else "active"
        return "active"

    ignored_prefixes = [
        sd["path"].replace("\\", "/").rstrip("/") + "/"
        for sd in source_dirs
        if sd["source_type"] == "ignored"
    ]

    def _is_ignored(path: str) -> bool:
        norm = path.replace("\\", "/")
        return any(norm.startswith(pfx) for pfx in ignored_prefixes)

    if changed_files is not None:
        all_system_files = collect_system_files(
            config.repo_path, system, effective_extensions
        )
        if not all_system_files:
            log.info("No files found for system '%s'", system_name)
            return 0, 0
        neighbourhood = build_neighbourhood(
            changed_files, all_system_files, config.repo_path
        )
        if not neighbourhood:
            log.info("No neighbourhood files resolved for system '%s'", system_name)
            return 0, 0
        file_paths = [p for p in neighbourhood.keys() if not _is_ignored(p)]
    else:
        all_paths = list_system_files(config.repo_path, system, effective_extensions)
        if not all_paths:
            log.info("No files found for system '%s'", system_name)
            return 0, 0
        file_paths = [
            p for p in all_paths
            if find_owning_system(p, all_systems) == system_name
            and not _is_ignored(p)
        ]
        excluded = len(all_paths) - len(file_paths)
        if excluded:
            log.info(
                "System '%s': excluded %d file(s) (sub-system ownership or ignored dir)",
                system_name, excluded,
            )
        if not file_paths:
            log.info("No files remain for system '%s' after ownership/ignored filter", system_name)
            return 0, 0

    chunks = chunk_paths_by_count(file_paths, max_files=_CHUNK_SIZE)
    from nytwatch.ws_manager import manager as ws_manager

    findings_count = 0
    chunks_failed = 0

    for i, chunk in enumerate(chunks):
        log.info(
            "Analyzing system '%s' chunk %d/%d (%d files)",
            system_name, i + 1, len(chunks), len(chunk),
        )
        for fname in sorted(chunk):
            log.info("  %s", fname)

        result = analyze_system(
            system_name=system_name,
            file_paths=chunk,
            repo_path=config.repo_path,
            fast=effective_fast,
            max_retries=2,
        )
        if result is None:
            log.error("Analysis returned None for system '%s' chunk %d", system_name, i + 1)
            chunks_failed += 1
            continue

        # ── Batch fingerprint check — one query instead of N ─────────────────
        candidate_fingerprints = [
            _compute_fingerprint(
                fo.file_path, f"{fo.line_start}-{fo.line_end}", fo.category, fo.title
            )
            for fo in result.findings
        ]
        existing_fingerprints = db.has_fingerprints_batch(candidate_fingerprints)

        chunk_new = 0
        for fo, fingerprint in zip(result.findings, candidate_fingerprints):
            # Skip duplicates
            if fingerprint in existing_fingerprints:
                log.debug("Duplicate fingerprint, skipping: %s", fo.title)
                continue

            # Skip findings below the configured confidence threshold
            if not _meets_confidence(fo.confidence, effective_min_confidence):
                log.debug(
                    "Skipping low-confidence finding (%s < %s): %s",
                    fo.confidence, effective_min_confidence, fo.title,
                )
                continue

            source_type = _classify_path(fo.file_path)
            locations_json = (
                json.dumps([loc.model_dump() for loc in fo.locations])
                if fo.locations else None
            )
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
                locations=locations_json,
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

    files_scanned = len(file_paths)

    if chunks_failed == len(chunks):
        log.error("ALL chunks failed for system '%s' — returning -1", system_name)
        return -1, files_scanned

    if chunks_failed > 0:
        log.warning(
            "System '%s': %d/%d chunks failed, %d findings from successful chunks",
            system_name, chunks_failed, len(chunks), findings_count,
        )

    return findings_count, files_scanned


def run_incremental_scan(config: AuditorConfig, db: Database, system_name: Optional[str] = None) -> str:
    scan_id = new_id()
    scan = Scan(
        id=scan_id,
        scan_type=ScanType.INCREMENTAL,
        system_name=system_name,
        base_commit="",
    )
    db.insert_scan(scan)

    from nytwatch.scan_state import ScanLogHandler
    from nytwatch.ws_manager import manager as ws_manager
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

        from nytwatch.config import SystemDef
        all_systems = [SystemDef(**s) for s in db.list_systems()]
        systems_to_scan = all_systems
        if system_name:
            systems_to_scan = [s for s in all_systems if s.name == system_name]
        system_map = map_files_to_systems(changed, systems_to_scan)

        active_entries = [
            (sname, files)
            for sname, files in system_map.items()
            if sname != "__uncategorized"
        ]
        skipped = len(system_map.get("__uncategorized", []))
        if skipped:
            log.info("Skipping %d uncategorized file(s)", skipped)

        total_findings = 0
        total_files = len(changed)
        systems_attempted = 0
        systems_failed = 0
        _lock = threading.Lock()

        n_workers = max(1, min(len(active_entries), _MAX_PARALLEL_SYSTEMS))
        log.info(
            "Incremental scan: %d system(s) to analyse, %d parallel worker(s)",
            len(active_entries), n_workers,
        )

        def _run_one(entry: tuple[str, list[str]]) -> tuple[int, int]:
            sname, files = entry
            return _process_system(sname, config, db, scan_id, config.claude_fast_mode, changed_files=files)

        with ThreadPoolExecutor(max_workers=n_workers) as executor:
            future_to_sname = {executor.submit(_run_one, e): e[0] for e in active_entries}
            for future in as_completed(future_to_sname):
                sname = future_to_sname[future]
                systems_attempted += 1
                try:
                    count, _files = future.result()
                except InterruptedError:
                    raise
                except Exception as exc:
                    log.error("System '%s' raised unexpected exception: %s", sname, exc)
                    systems_failed += 1
                    continue

                if count == -1:
                    systems_failed += 1
                    log.error("System '%s' failed analysis", sname)
                else:
                    with _lock:
                        total_findings += count

        all_failed = systems_attempted > 0 and systems_failed == systems_attempted
        final_status = ScanStatus.FAILED if all_failed else ScanStatus.COMPLETED

        if all_failed:
            log.error("Incremental scan %s: ALL %d systems failed analysis", scan_id, systems_attempted)

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
