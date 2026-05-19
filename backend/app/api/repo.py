from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import aiosqlite
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

from app.core.config import settings
from app.core.qdrant_client import delete_collection, collection_info
from app.db.database import get_db
from app.models.repo import (
    FileContentResponse,
    FileNode,
    IndexProgressResponse,
    IndexStatus,
    RepoListResponse,
    RepoRegisterRequest,
    RepoResponse,
)
from app.services import indexer_service
from app.services.repo_service import (
    count_code_files,
    detect_primary_language,
    make_repo_id,
    read_file_content,
    scan_file_tree,
)

router = APIRouter(prefix="/api/repo", tags=["repo"])
logger = logging.getLogger(__name__)


# ── 注册仓库 ──────────────────────────────────────────────────────────────────

@router.post("", response_model=RepoResponse, status_code=201)
async def register_repo(
    body: RepoRegisterRequest,
    db: aiosqlite.Connection = Depends(get_db),
):
    root = Path(body.root_path)

    if not settings.is_path_allowed(root):
        raise HTTPException(403, "该路径不在允许的白名单内")

    repo_id  = make_repo_id(body.root_path)
    language = detect_primary_language(root)
    files    = count_code_files(root)

    # upsert（同路径重新注册时更新名称）
    await db.execute(
        """INSERT INTO repos(id, name, root_path, language, file_count, indexed)
           VALUES (?,?,?,?,?,0)
           ON CONFLICT(root_path) DO UPDATE SET
               name=excluded.name,
               language=excluded.language,
               file_count=excluded.file_count,
               updated_at=CURRENT_TIMESTAMP""",
        (repo_id, body.name, body.root_path, language, files),
    )
    await db.commit()

    row = await _fetch_repo(db, repo_id)
    return _row_to_response(row)


# ── 仓库列表 ──────────────────────────────────────────────────────────────────

@router.get("", response_model=RepoListResponse)
async def list_repos(db: aiosqlite.Connection = Depends(get_db)):
    async with db.execute(
        "SELECT * FROM repos ORDER BY updated_at DESC"
    ) as cur:
        rows = await cur.fetchall()
    return RepoListResponse(
        items=[_row_to_response(r) for r in rows],
        total=len(rows),
    )


# ── 仓库详情 ──────────────────────────────────────────────────────────────────

@router.get("/{repo_id}", response_model=RepoResponse)
async def get_repo(repo_id: str, db: aiosqlite.Connection = Depends(get_db)):
    row = await _fetch_repo(db, repo_id)
    if not row:
        raise HTTPException(404, "仓库不存在")
    return _row_to_response(row)


# ── 删除仓库 ──────────────────────────────────────────────────────────────────

@router.delete("/{repo_id}", status_code=204)
async def delete_repo(repo_id: str, db: aiosqlite.Connection = Depends(get_db)):
    row = await _fetch_repo(db, repo_id)
    if not row:
        raise HTTPException(404, "仓库不存在")
    try:
        await delete_collection(repo_id)
    except Exception as e:
        logger.warning("Delete Qdrant collection failed: %s", e)
    await db.execute("DELETE FROM repos WHERE id=?", (repo_id,))
    await db.commit()


# ── 触发索引 ──────────────────────────────────────────────────────────────────

@router.post("/{repo_id}/index", response_model=IndexProgressResponse)
async def trigger_index(
    repo_id: str,
    background_tasks: BackgroundTasks,
    db: aiosqlite.Connection = Depends(get_db),
):
    row = await _fetch_repo(db, repo_id)
    if not row:
        raise HTTPException(404, "仓库不存在")
    if dict(row)["indexed"] == 1:
        raise HTTPException(409, "索引任务已在运行中")

    # 后台异步执行，不阻塞响应
    background_tasks.add_task(
        _run_index_bg, repo_id, dict(row)["root_path"]
    )

    return IndexProgressResponse(
        repo_id     = repo_id,
        status      = IndexStatus.RUNNING,
        message     = "索引任务已启动",
        chunk_count = 0,
        file_count  = dict(row)["file_count"],
    )


@router.get("/{repo_id}/index/status", response_model=IndexProgressResponse)
async def index_status(repo_id: str, db: aiosqlite.Connection = Depends(get_db)):
    row = await _fetch_repo(db, repo_id)
    if not row:
        raise HTTPException(404, "仓库不存在")
    d = dict(row)
    return IndexProgressResponse(
        repo_id     = repo_id,
        status      = IndexStatus(d["indexed"]),
        message     = _status_message(d["indexed"]),
        chunk_count = d["chunk_count"],
        file_count  = d["file_count"],
    )


# ── 文件树 ────────────────────────────────────────────────────────────────────

@router.get("/{repo_id}/tree", response_model=list[FileNode])
async def get_file_tree(
    repo_id: str,
    db: aiosqlite.Connection = Depends(get_db),
):
    row = await _fetch_repo(db, repo_id)
    if not row:
        raise HTTPException(404, "仓库不存在")
    root = Path(dict(row)["root_path"])
    nodes = scan_file_tree(root)
    return nodes


# ── 文件内容 ──────────────────────────────────────────────────────────────────

@router.get("/{repo_id}/file", response_model=FileContentResponse)
async def get_file_content(
    repo_id: str,
    path: str = Query(..., description="相对于 repo root 的路径"),
    db: aiosqlite.Connection = Depends(get_db),
):
    row = await _fetch_repo(db, repo_id)
    if not row:
        raise HTTPException(404, "仓库不存在")
    root = Path(dict(row)["root_path"])
    try:
        return await read_file_content(root, path)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except (PermissionError, IsADirectoryError, ValueError) as e:
        raise HTTPException(400, str(e))


# ── 索引日志 ──────────────────────────────────────────────────────────────────

@router.get("/{repo_id}/index/logs")
async def get_index_logs(
    repo_id: str,
    limit: int = 50,
    db: aiosqlite.Connection = Depends(get_db),
):
    async with db.execute(
        "SELECT level, message, created_at FROM index_logs WHERE repo_id=? ORDER BY id DESC LIMIT ?",
        (repo_id, limit),
    ) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


# ── 内部工具 ──────────────────────────────────────────────────────────────────

async def _fetch_repo(db, repo_id: str):
    async with db.execute("SELECT * FROM repos WHERE id=?", (repo_id,)) as cur:
        return await cur.fetchone()


def _row_to_response(row) -> RepoResponse:
    from datetime import datetime
    d = dict(row)
    return RepoResponse(
        id          = d["id"],
        name        = d["name"],
        root_path   = d["root_path"],
        language    = d.get("language"),
        file_count  = d["file_count"],
        chunk_count = d["chunk_count"],
        indexed     = IndexStatus(d["indexed"]),
        created_at  = d["created_at"],
        updated_at  = d["updated_at"],
    )


def _status_message(status: int) -> str:
    return {
        0: "尚未索引",
        1: "索引进行中...",
        2: "索引完成",
        3: "索引失败",
    }.get(status, "未知状态")


async def _run_index_bg(repo_id: str, root_path: str) -> None:
    """后台任务：需要独立的 db 连接（BackgroundTasks 不共享请求上下文）"""
    import aiosqlite as _aiosqlite
    async with _aiosqlite.connect(settings.SQLITE_PATH) as db:
        db.row_factory = _aiosqlite.Row
        # busy_timeout：等待写锁最多 30s
        await db.execute("PRAGMA busy_timeout = 30000")
        await db.execute("PRAGMA journal_mode = WAL")
        await db.execute("PRAGMA synchronous = NORMAL")
        await indexer_service.run_index(repo_id, root_path, db)
