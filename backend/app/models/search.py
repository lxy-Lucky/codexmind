from __future__ import annotations

from typing import Optional
from pydantic import BaseModel


class SearchRequest(BaseModel):
    query: str
    repo_id: str
    mode: str = "semantic"          # semantic | file
    language_filter: Optional[str] = None
    top_k: int = 10


class SearchResultItem(BaseModel):
    file_path: str
    line_start: int
    line_end: int
    snippet: str
    score: float
    language: str
    chunk_type: str                 # method | class | block


class SearchResponse(BaseModel):
    results: list[SearchResultItem]
    total: int
    latency_ms: int
    query: str
