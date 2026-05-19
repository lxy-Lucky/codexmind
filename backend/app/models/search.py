from __future__ import annotations

from typing import Optional
from pydantic import BaseModel


class SearchRequest(BaseModel):
    repo_id: str
    query: str
    top_k: int = 10
    language_filter: Optional[str] = None


class CallChainItem(BaseModel):
    symbol_id: str
    method_name: str
    class_name: Optional[str] = None
    file_path: str
    direction: str   # "caller" | "callee"
    depth: int = 1


class SearchResultItem(BaseModel):
    file_path: str
    line_start: int
    line_end: int
    snippet: str
    score: float
    language: str = ""
    chunk_type: str = "method"
    symbol_id: Optional[str] = None
    method_name: Optional[str] = None
    class_name: Optional[str] = None
    # 调用链上下文（1-hop）
    callers: list[CallChainItem] = []
    callees: list[CallChainItem] = []


class SearchResponse(BaseModel):
    results: list[SearchResultItem]
    total: int
    latency_ms: int
    query: str
    intent: str = "semantic"    # symbol_lookup | semantic | call_chain | impact
