from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class FindingOutput(BaseModel):
    title: str
    description: str
    severity: str
    category: str
    confidence: str
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


class ScanResult(BaseModel):
    findings: list[FindingOutput] = Field(default_factory=list)
    files_analyzed: list[str] = Field(default_factory=list)
    scan_notes: str = ""


class BatchApplyResult(BaseModel):
    unified_diff: str
    files_modified: list[str] = Field(default_factory=list)
    notes: str = ""
