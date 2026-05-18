from __future__ import annotations

from typing import Optional
from pydantic import BaseModel


class AnalysisRequest(BaseModel):
    repo_id: str
    file_path: str
    line_start: int
    line_end: int
    code: str
    mode: str = "summary"           # summary | bug | deps


class BugItem(BaseModel):
    severity: str                   # Critical | Warning | Suggestion
    line: Optional[int]
    title: str
    desc: str
    suggestion: str
    code_ref: Optional[str] = None


class AnalysisDoneEvent(BaseModel):
    confidence: float
    latency_ms: int
    mode: str
