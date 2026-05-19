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
    # 新增：symbol_id 用于从 Neo4j 查调用链上下文
    symbol_id: Optional[str] = None

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
