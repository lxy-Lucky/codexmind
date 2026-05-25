from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import aiosqlite
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

from app.core.config import settings
from app.core.qdrant_client import delete_collection, collection_info
from app.core.neo4j_client import run_query, run_write
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

    repo_id   = make_repo_id(body.root_path)
    language  = detect_primary_language(root)
    files     = count_code_files(root)
    skip_json = json.dumps(body.skip_dirs, ensure_ascii=False)

    # upsert（同路径重新注册时更新名称和 skip_dirs）
    await db.execute(
        """INSERT INTO repos(id, name, root_path, language, file_count, indexed, skip_dirs)
           VALUES (?,?,?,?,?,0,?)
           ON CONFLICT(root_path) DO UPDATE SET
               name=excluded.name,
               language=excluded.language,
               file_count=excluded.file_count,
               skip_dirs=excluded.skip_dirs,
               updated_at=CURRENT_TIMESTAMP""",
        (repo_id, body.name, body.root_path, language, files, skip_json),
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
    if dict(row)["indexed"] == 1:
        # 索引正在跑，删除会和后台任务 race
        raise HTTPException(409, "索引进行中，无法删除")

    # Qdrant collection
    try:
        await delete_collection(repo_id)
    except Exception as e:
        logger.warning("Delete Qdrant collection failed: %s", e)

    # Neo4j 节点（必须带 Label，否则全图扫描）
    for label in ("Method", "Class", "File"):
        try:
            while True:
                cnt_rows = await run_query(
                    f"MATCH (n:{label} {{repo_id: $repo_id}}) RETURN count(n) AS cnt",
                    {"repo_id": repo_id},
                )
                if not cnt_rows or cnt_rows[0].get("cnt", 0) == 0:
                    break
                await run_write(
                    f"MATCH (n:{label} {{repo_id: $repo_id}}) WITH n LIMIT 200 DETACH DELETE n",
                    {"repo_id": repo_id},
                )
        except Exception as e:
            logger.warning("Delete Neo4j %s nodes failed: %s", label, e)

    # v3：BM25 已退役，旧版本可能在 data/bm25/ 留 pickle，清理一下
    try:
        pickle_path = settings.BM25_INDEX_DIR / f"{repo_id}.pkl"
        if pickle_path.exists():
            pickle_path.unlink()
    except Exception as e:
        logger.warning("Delete legacy BM25 pickle failed: %s", e)

    # SQLite（foreign_keys=ON 已在 get_db 开启，CASCADE 自动清 symbols / query_history）
    await db.execute("DELETE FROM repos WHERE id=?", (repo_id,))
    # index_logs 没有 FK，手动清
    await db.execute("DELETE FROM index_logs WHERE repo_id=?", (repo_id,))
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

    d = dict(row)
    extra_skip = frozenset(json.loads(d.get("skip_dirs") or "[]"))
    # 后台异步执行，不阻塞响应
    background_tasks.add_task(
        _run_index_bg, repo_id, d["root_path"], extra_skip
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
    d = dict(row)
    root = Path(d["root_path"])
    extra = frozenset(json.loads(d.get("skip_dirs") or "[]"))
    nodes = scan_file_tree(root, extra_skip_dirs=extra)
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


# ── 光标位置方法符号查找 ──────────────────────────────────────────────────────────

@router.get("/{repo_id}/symbol/at", summary="光标位置的方法符号查找")
async def symbol_at_line(
    repo_id: str,
    file_path: str = Query(..., description="相对于 repo root 的文件路径"),
    line: int      = Query(..., ge=1, description="光标行号（1-indexed）"),
    db: aiosqlite.Connection = Depends(get_db),
) -> Optional[dict[str, Any]]:
    """
    给定 file_path + 行号，返回包含该行的最小方法符号。
    用于从侧边栏打开文件后，通过光标位置触发方法调用图。
    找不到时返回 null（HTTP 200）。
    """
    # 兼容 Windows 反斜杠 / Linux 正斜杠两种存储格式
    fp_fwd  = file_path.replace("\\", "/")
    fp_back = file_path.replace("/", "\\")
    async with db.execute(
        """
        SELECT id, method_name AS name, class_name, file_path, line_start, line_end
        FROM symbols
        WHERE repo_id    = ?
          AND (file_path = ? OR file_path = ?)
          AND line_start <= ?
          AND line_end   >= ?
          AND method_name IS NOT NULL
        ORDER BY (line_end - line_start) ASC
        LIMIT 1
        """,
        (repo_id, fp_fwd, fp_back, line, line),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return None
    return dict(row)


# ── 索引日志 ──────────────────────────────────────────────────────────────────

@router.get("/{repo_id}/index/stats", summary="索引统计（按 language / chunk_type 分组）")
async def index_stats(repo_id: str, db: aiosqlite.Connection = Depends(get_db)):
    """
    诊断用：返回该 repo 在 Qdrant / Neo4j / SQLite 中的分布情况。
    便于排查"为什么搜不到某种类型的文件"——比如 XML mapper 没建索引时
    qdrant.by_language.xml 就会是 0。
    """
    row = await _fetch_repo(db, repo_id)
    if not row:
        raise HTTPException(404, "仓库不存在")

    stats: dict[str, Any] = {"repo_id": repo_id}

    # ── Qdrant：按 language / chunk_type 分别 count ───────────────────────
    from app.core.qdrant_client import get_qdrant, collection_name
    from qdrant_client.http.models import Filter, FieldCondition, MatchValue
    client = get_qdrant()
    col = collection_name(repo_id)

    qdrant_stats: dict[str, Any] = {}
    try:
        info = await client.get_collection(collection_name=col)
        qdrant_stats["total_points"] = info.points_count or 0
    except Exception as e:
        qdrant_stats["error"] = f"collection not found: {e}"

    by_language: dict[str, int] = {}
    by_chunk_type: dict[str, int] = {}
    for lang in ("java", "javascript", "typescript", "xml", "vue"):
        try:
            r = await client.count(
                collection_name=col, exact=True,
                count_filter=Filter(must=[FieldCondition(
                    key="language", match=MatchValue(value=lang))]),
            )
            by_language[lang] = r.count
        except Exception:
            pass
    for ct in ("method", "sql", "block", "class"):
        try:
            r = await client.count(
                collection_name=col, exact=True,
                count_filter=Filter(must=[FieldCondition(
                    key="chunk_type", match=MatchValue(value=ct))]),
            )
            by_chunk_type[ct] = r.count
        except Exception:
            pass
    qdrant_stats["by_language"]   = by_language
    qdrant_stats["by_chunk_type"] = by_chunk_type
    stats["qdrant"] = qdrant_stats

    # ── SQLite symbols：按 file_path 后缀分组 ────────────────────────────
    async with db.execute(
        "SELECT file_path FROM symbols WHERE repo_id=?", (repo_id,)
    ) as cur:
        rows = await cur.fetchall()
    by_ext: dict[str, int] = {}
    for r in rows:
        fp = r["file_path"] or ""
        ext = fp.rsplit(".", 1)[-1].lower() if "." in fp else "<noext>"
        by_ext[ext] = by_ext.get(ext, 0) + 1
    stats["sqlite_symbols_by_ext"] = by_ext
    stats["sqlite_symbols_total"]  = len(rows)

    # ── Neo4j Method 节点按 chunk_type ───────────────────────────────────
    try:
        from app.core.neo4j_client import run_query
        rows = await run_query(
            """
            MATCH (m:Method {repo_id: $repo_id})
            RETURN coalesce(m.chunk_type,'method') AS ct, count(m) AS cnt
            """,
            {"repo_id": repo_id},
        )
        stats["neo4j_method_by_type"] = {r["ct"]: r["cnt"] for r in rows}
    except Exception as e:
        stats["neo4j_method_by_type"] = {"error": str(e)}

    return stats


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
    d = dict(row)
    return RepoResponse(
        id          = d["id"],
        name        = d["name"],
        root_path   = d["root_path"],
        language    = d.get("language"),
        file_count  = d["file_count"],
        chunk_count = d["chunk_count"],
        indexed     = IndexStatus(d["indexed"]),
        skip_dirs   = json.loads(d.get("skip_dirs") or "[]"),
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


async def _run_index_bg(
    repo_id: str, root_path: str,
    extra_skip_dirs: frozenset[str] = frozenset(),
) -> None:
    """后台任务：需要独立的 db 连接（BackgroundTasks 不共享请求上下文）"""
    import aiosqlite as _aiosqlite
    async with _aiosqlite.connect(settings.SQLITE_PATH) as db:
        db.row_factory = _aiosqlite.Row
        await db.execute("PRAGMA busy_timeout = 30000")
        await db.execute("PRAGMA journal_mode = WAL")
        await db.execute("PRAGMA synchronous = NORMAL")
        # foreign_keys 是 per-connection 设置
        await db.execute("PRAGMA foreign_keys = ON")
        await indexer_service.run_index(repo_id, root_path, db, extra_skip_dirs)
