from __future__ import annotations

from datetime import datetime
from enum import IntEnum
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, field_validator


# ── 枚举 ──────────────────────────────────────────────────────────────────────

class IndexStatus(IntEnum):
    PENDING = 0
    RUNNING = 1
    DONE    = 2
    FAILED  = 3


# ── Repo ─────────────────────────────────────────────────────────────────────

class RepoRegisterRequest(BaseModel):
    name: str
    root_path: str          # 服务器本地绝对路径
    skip_dirs: list[str] = []   # 该仓库专属的额外跳过目录名（目录名，非路径）

    @field_validator("root_path")
    @classmethod
    def must_be_absolute(cls, v: str) -> str:
        p = Path(v)
        if not p.is_absolute():
            raise ValueError("root_path 必须是绝对路径")
        if not p.exists():
            raise ValueError(f"路径不存在: {v}")
        if not p.is_dir():
            raise ValueError(f"路径不是目录: {v}")
        return str(p.resolve())


class RepoResponse(BaseModel):
    id: str
    name: str
    root_path: str
    language: Optional[str]
    file_count: int
    chunk_count: int
    indexed: IndexStatus
    skip_dirs: list[str] = []
    created_at: datetime
    updated_at: datetime


class RepoListResponse(BaseModel):
    items: list[RepoResponse]
    total: int


# ── File Tree ────────────────────────────────────────────────────────────────

class FileNode(BaseModel):
    name: str
    path: str           # 相对于 repo root_path
    type: str           # "file" | "dir"
    language: Optional[str] = None
    size: Optional[int] = None
    children: Optional[list["FileNode"]] = None


class FileContentResponse(BaseModel):
    path: str
    language: str
    content: str
    line_count: int
    size: int


# ── Index ────────────────────────────────────────────────────────────────────

class IndexProgressResponse(BaseModel):
    repo_id: str
    status: IndexStatus
    message: str
    chunk_count: int
    file_count: int
