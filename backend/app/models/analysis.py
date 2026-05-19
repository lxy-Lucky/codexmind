from __future__ import annotations

from typing import Optional, List
from pydantic import BaseModel


class ChatMessage(BaseModel):
    role: str        # "user" | "assistant"
    content: str


class AnalysisRequest(BaseModel):
    repo_id: str
    file_path: str
    line_start: int
    line_end: int
    code: str
    mode: str = "summary"
    custom_prompt: Optional[str] = None
    _history: Optional[List[ChatMessage]] = None   # 多轮对话历史

    model_config = {"populate_by_name": True}


class BugItem(BaseModel):
    severity: str
    line: Optional[int]
    title: str
    desc: str
    suggestion: str
    code_ref: Optional[str] = None


class AnalysisDoneEvent(BaseModel):
    confidence: float
    latency_ms: int
    mode: str
