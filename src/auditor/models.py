from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class Category(str, Enum):
    BUG = "bug"
    PERFORMANCE = "performance"
    UE_ANTIPATTERN = "ue-antipattern"
    MODERN_CPP = "modern-cpp"
    MEMORY = "memory"
    READABILITY = "readability"


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class FindingSource(str, Enum):
    PROJECT = "project"
    PLUGIN = "plugin"
    IGNORED = "ignored"


class FindingStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    APPLIED = "applied"
    VERIFIED = "verified"
    FAILED = "failed"
    SUPERSEDED = "superseded"


class ScanType(str, Enum):
    INCREMENTAL = "incremental"
    FULL = "full"
    MANUAL = "manual"


class ScanStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class BatchStatus(str, Enum):
    PENDING = "pending"
    APPLYING = "applying"
    BUILDING = "building"
    TESTING = "testing"
    VERIFIED = "verified"
    FAILED = "failed"


class Finding(BaseModel):
    id: str = Field(default_factory=new_id)
    scan_id: str
    title: str
    description: str
    severity: Severity
    category: Category
    confidence: Confidence
    file_path: str
    line_start: int
    line_end: int
    code_snippet: str
    suggested_fix: Optional[str] = None
    fix_diff: Optional[str] = None
    can_auto_fix: bool = False
    reasoning: str
    test_code: Optional[str] = None
    test_description: Optional[str] = None
    source: FindingSource = FindingSource.PROJECT
    status: FindingStatus = FindingStatus.PENDING
    batch_id: Optional[str] = None
    fingerprint: str = ""
    created_at: str = Field(default_factory=now_iso)
    reviewed_at: Optional[str] = None


class Scan(BaseModel):
    id: str = Field(default_factory=new_id)
    scan_type: ScanType
    system_name: Optional[str] = None
    started_at: str = Field(default_factory=now_iso)
    completed_at: Optional[str] = None
    base_commit: str = ""
    files_scanned: int = 0
    findings_count: int = 0
    status: ScanStatus = ScanStatus.RUNNING


class Batch(BaseModel):
    id: str = Field(default_factory=new_id)
    created_at: str = Field(default_factory=now_iso)
    status: BatchStatus = BatchStatus.PENDING
    branch_name: Optional[str] = None
    build_log: Optional[str] = None
    test_log: Optional[str] = None
    commit_sha: Optional[str] = None
    pr_url: Optional[str] = None
    finding_ids: list[str] = Field(default_factory=list)
    completed_at: Optional[str] = None
