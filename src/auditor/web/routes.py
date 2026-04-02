from __future__ import annotations

import logging
import threading
from io import BytesIO
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from auditor.database import Database
from auditor.models import BatchStatus, FindingStatus, now_iso

logger = logging.getLogger(__name__)

router = APIRouter()

templates = Jinja2Templates(
    directory=str(__file__).replace("routes.py", "templates")
)


def get_db(request: Request) -> Database:
    return request.app.state.db


def get_config(request: Request):
    return request.app.state.config


# --- Dashboard ---

@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    db = get_db(request)
    stats = db.get_stats()
    batches = db.list_batches(limit=5)
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "stats": stats,
        "batches": batches,
    })


# --- Findings ---

@router.get("/findings", response_class=HTMLResponse)
async def findings_list(
    request: Request,
    status: Optional[str] = None,
    severity: Optional[str] = None,
    category: Optional[str] = None,
    confidence: Optional[str] = None,
    file_path: Optional[str] = None,
    source: Optional[str] = None,
):
    db = get_db(request)
    findings = db.list_findings(
        status=status,
        severity=severity,
        category=category,
        confidence=confidence,
        file_path=file_path,
        source=source,
    )
    approved_count = len(db.get_approved_findings())
    filters = {
        "status": status,
        "severity": severity,
        "category": category,
        "confidence": confidence,
        "file_path": file_path,
        "source": source,
    }
    return templates.TemplateResponse("findings_list.html", {
        "request": request,
        "findings": findings,
        "filters": filters,
        "approved_count": approved_count,
    })


@router.get("/findings/export")
async def findings_export(
    request: Request,
    status: Optional[str] = None,
    severity: Optional[str] = None,
    category: Optional[str] = None,
    confidence: Optional[str] = None,
    file_path: Optional[str] = None,
    source: Optional[str] = None,
):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter

    config = get_config(request)
    db = get_db(request)
    findings = db.list_findings(
        status=status,
        severity=severity,
        category=category,
        confidence=confidence,
        file_path=file_path,
        source=source,
    )
    scans = db.list_scans()
    stats = db.get_stats()

    wb = Workbook()

    # --- Sheet 1: Overview ---
    ws_overview = wb.active
    ws_overview.title = "Overview"
    bold = Font(bold=True)
    white_bold = Font(bold=True, color="FFFFFFFF")

    ws_overview.merge_cells("A1:C1")
    cell_title = ws_overview["A1"]
    cell_title.value = "Code Auditor Report"
    cell_title.font = bold

    project_name = config.repo_path.rstrip("/").split("/")[-1] if "/" in config.repo_path else config.repo_path
    ws_overview["A3"] = "Project"
    ws_overview["B3"] = project_name
    ws_overview["A4"] = "Generated"
    ws_overview["B4"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    ws_overview["A5"] = "Confidence Threshold"
    ws_overview["B5"] = config.min_confidence
    ws_overview["A6"] = "File Extensions"
    ws_overview["B6"] = ", ".join(config.file_extensions)

    row = 8
    ws_overview.cell(row=row, column=1, value="Systems Configured").font = bold
    row += 1
    for system in config.systems:
        ws_overview.cell(row=row, column=2, value=system.name)
        ws_overview.cell(row=row, column=3, value=", ".join(system.paths))
        row += 1

    row += 1
    ws_overview.cell(row=row, column=1, value="Severity Breakdown").font = bold
    row += 1
    severity_counts = stats.get("severity_counts", {})
    for sev in ["Critical", "High", "Medium", "Low", "Info"]:
        ws_overview.cell(row=row, column=2, value=sev)
        ws_overview.cell(row=row, column=3, value=severity_counts.get(sev.lower(), 0))
        row += 1

    row += 1
    ws_overview.cell(row=row, column=1, value="Scan History").font = bold
    row += 1
    for col_idx, header in enumerate(["System", "Files", "Findings", "Status", "Date"], start=2):
        cell = ws_overview.cell(row=row, column=col_idx, value=header)
        cell.font = bold
    row += 1
    for scan in scans:
        ws_overview.cell(row=row, column=2, value=scan.get("system_name", ""))
        ws_overview.cell(row=row, column=3, value=scan.get("files_scanned", 0))
        ws_overview.cell(row=row, column=4, value=scan.get("findings_count", 0))
        ws_overview.cell(row=row, column=5, value=scan.get("status", ""))
        ws_overview.cell(row=row, column=6, value=scan.get("started_at", ""))
        row += 1

    # --- Sheet 2: Findings ---
    ws_findings = wb.create_sheet("Findings")
    headers = [
        "Severity", "Source", "Title", "File", "Line Start", "Line End",
        "Category", "Confidence", "Status", "Description",
        "Suggested Fix", "Reasoning", "Test Description",
        "Created", "Reviewed",
    ]
    for col_idx, header in enumerate(headers, start=1):
        cell = ws_findings.cell(row=1, column=col_idx, value=header)
        cell.font = bold

    severity_fills = {
        "critical": PatternFill(start_color="FFDC2626", end_color="FFDC2626", fill_type="solid"),
        "high": PatternFill(start_color="FFEA580C", end_color="FFEA580C", fill_type="solid"),
        "medium": PatternFill(start_color="FFB45309", end_color="FFB45309", fill_type="solid"),
        "low": PatternFill(start_color="FF2563EB", end_color="FF2563EB", fill_type="solid"),
        "info": PatternFill(start_color="FF737686", end_color="FF737686", fill_type="solid"),
    }
    white_font = Font(color="FFFFFFFF")

    for row_idx, f in enumerate(findings, start=2):
        sev = f.get("severity", "")
        ws_findings.cell(row=row_idx, column=1, value=sev)
        ws_findings.cell(row=row_idx, column=2, value=f.get("source", "project"))
        ws_findings.cell(row=row_idx, column=3, value=f.get("title", ""))
        ws_findings.cell(row=row_idx, column=4, value=f.get("file_path", ""))
        ws_findings.cell(row=row_idx, column=5, value=f.get("line_start", 0))
        ws_findings.cell(row=row_idx, column=6, value=f.get("line_end", 0))
        ws_findings.cell(row=row_idx, column=7, value=f.get("category", ""))
        ws_findings.cell(row=row_idx, column=8, value=f.get("confidence", ""))
        ws_findings.cell(row=row_idx, column=9, value=f.get("status", ""))
        ws_findings.cell(row=row_idx, column=10, value=f.get("description", ""))
        ws_findings.cell(row=row_idx, column=11, value=f.get("suggested_fix", ""))
        ws_findings.cell(row=row_idx, column=12, value=f.get("reasoning", ""))
        ws_findings.cell(row=row_idx, column=13, value=f.get("test_description", ""))
        ws_findings.cell(row=row_idx, column=14, value=f.get("created_at", ""))
        ws_findings.cell(row=row_idx, column=15, value=f.get("reviewed_at", ""))

        sev_cell = ws_findings.cell(row=row_idx, column=1)
        if sev in severity_fills:
            sev_cell.fill = severity_fills[sev]
            sev_cell.font = white_font

    for col_idx in range(1, len(headers) + 1):
        max_length = 0
        column_letter = get_column_letter(col_idx)
        for row_cells in ws_findings.iter_rows(min_col=col_idx, max_col=col_idx):
            for cell in row_cells:
                if cell.value:
                    max_length = max(max_length, len(str(cell.value)))
        adjusted_width = min(max_length + 2, 60)
        ws_findings.column_dimensions[column_letter].width = adjusted_width

    ws_findings.freeze_panes = "A2"

    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=code_auditor_findings.xlsx"},
    )


@router.get("/findings/{finding_id}", response_class=HTMLResponse)
async def finding_detail(request: Request, finding_id: str):
    db = get_db(request)
    finding = db.get_finding(finding_id)
    if not finding:
        return HTMLResponse("<h1>Finding not found</h1>", status_code=404)
    return templates.TemplateResponse("finding_detail.html", {
        "request": request,
        "finding": finding,
    })


@router.post("/findings/{finding_id}/approve")
async def approve_finding(request: Request, finding_id: str):
    db = get_db(request)
    finding = db.get_finding(finding_id)
    if not finding:
        return JSONResponse({"error": "Finding not found"}, status_code=404)
    if finding["status"] not in ("pending", "rejected"):
        return JSONResponse({"error": f"Cannot approve finding with status '{finding['status']}'"}, status_code=400)
    db.update_finding_status(finding_id, FindingStatus.APPROVED)
    logger.info("Finding %s approved", finding_id)
    return JSONResponse({"ok": True, "status": "approved"})


@router.post("/findings/{finding_id}/reject")
async def reject_finding(request: Request, finding_id: str):
    db = get_db(request)
    finding = db.get_finding(finding_id)
    if not finding:
        return JSONResponse({"error": "Finding not found"}, status_code=404)
    if finding["status"] not in ("pending", "approved"):
        return JSONResponse({"error": f"Cannot reject finding with status '{finding['status']}'"}, status_code=400)
    db.update_finding_status(finding_id, FindingStatus.REJECTED)
    logger.info("Finding %s rejected", finding_id)
    return JSONResponse({"ok": True, "status": "rejected"})


# --- Scans ---

@router.get("/scans", response_class=HTMLResponse)
async def scans_list(request: Request):
    db = get_db(request)
    scans = db.list_scans()
    return templates.TemplateResponse("scans.html", {
        "request": request,
        "scans": scans,
    })


@router.post("/scans/trigger")
async def trigger_scan(request: Request):
    config = get_config(request)
    db = get_db(request)

    def _run():
        try:
            from auditor.scanner.scheduler import run_scan
            scan_id = run_scan(config, db, scan_type="incremental")
            logger.info("Scan completed: %s", scan_id)
        except Exception:
            logger.exception("Scan failed")

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return JSONResponse({"ok": True, "scan_id": "started"})


# --- Settings ---

@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    db = get_db(request)
    source_dirs = db.list_source_dirs()
    project_dirs = [d for d in source_dirs if d["source_type"] == "project"]
    plugin_dirs = [d for d in source_dirs if d["source_type"] == "plugin"]
    ignored_dirs = [d for d in source_dirs if d["source_type"] == "ignored"]
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "project_dirs": project_dirs,
        "plugin_dirs": plugin_dirs,
        "ignored_dirs": ignored_dirs,
    })


@router.post("/settings/source-dirs")
async def update_source_dir(request: Request):
    db = get_db(request)
    body = await request.json()
    path = body.get("path", "").strip()
    source_type = body.get("source_type", "").strip()
    if not path or source_type not in ("project", "plugin", "ignored"):
        return JSONResponse({"error": "Invalid path or source_type"}, status_code=400)
    db.upsert_source_dir(path, source_type)
    logger.info("Source dir updated: '%s' -> '%s'", path, source_type)
    return JSONResponse({"ok": True, "path": path, "source_type": source_type})


@router.delete("/settings/source-dirs")
async def delete_source_dir(request: Request):
    db = get_db(request)
    body = await request.json()
    path = body.get("path", "").strip()
    if not path:
        return JSONResponse({"error": "Invalid path"}, status_code=400)
    db.delete_source_dir(path)
    logger.info("Source dir deleted: '%s'", path)
    return JSONResponse({"ok": True, "path": path})


# --- Batches ---

@router.get("/batches", response_class=HTMLResponse)
async def batches_list(request: Request):
    db = get_db(request)
    batches = db.list_batches()
    return templates.TemplateResponse("batches.html", {
        "request": request,
        "batches": batches,
    })


@router.get("/batches/{batch_id}", response_class=HTMLResponse)
async def batch_detail(request: Request, batch_id: str):
    db = get_db(request)
    batch = db.get_batch(batch_id)
    if not batch:
        return HTMLResponse("<h1>Batch not found</h1>", status_code=404)
    findings = [db.get_finding(fid) for fid in batch["finding_ids"]]
    findings = [f for f in findings if f]
    return templates.TemplateResponse("batch_status.html", {
        "request": request,
        "batch": batch,
        "findings": findings,
    })


@router.post("/batch/apply")
async def apply_batch(request: Request):
    config = get_config(request)
    db = get_db(request)

    approved = db.get_approved_findings()
    if not approved:
        return JSONResponse({"error": "No approved findings to apply"}, status_code=400)

    from auditor.models import Batch, new_id
    batch = Batch(
        id=new_id(),
        finding_ids=[f["id"] for f in approved],
    )
    db.insert_batch(batch)

    for f in approved:
        db.set_finding_batch(f["id"], batch.id)

    def _run():
        try:
            from auditor.pipeline.batch import run_batch_pipeline
            run_batch_pipeline(config, db, batch.id)
        except Exception:
            logger.exception("Batch pipeline failed for %s", batch.id)
            db.update_batch(batch.id, status=BatchStatus.FAILED)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    return JSONResponse({"ok": True, "batch_id": batch.id})


# --- API ---

@router.get("/api/stats")
async def api_stats(request: Request):
    db = get_db(request)
    return JSONResponse(db.get_stats())
