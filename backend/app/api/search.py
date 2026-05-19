from __future__ import annotations

import logging

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Query

from app.db.database import get_db
from app.models.search import SearchRequest, SearchResponse
from app.services.search_service import semantic_search

router = APIRouter(prefix="/api/search", tags=["search"])
logger = logging.getLogger(__name__)


@router.post("", response_model=SearchResponse)
async def search(
    body: SearchRequest,
    db: aiosqlite.Connection = Depends(get_db),
):
    async with db.execute("SELECT indexed FROM repos WHERE id=?", (body.repo_id,)) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "仓库不存在")
    if dict(row)["indexed"] != 2:
        raise HTTPException(400, "仓库尚未完成索引")

    # search_service v2 自己写查询历史
    return await semantic_search(body, db)


@router.get("/history", summary="查询历史记录")
async def get_history(
    repo_id: str = Query(...),
    limit: int   = Query(50, le=200),
    db: aiosqlite.Connection = Depends(get_db),
):
    async with db.execute(
        """SELECT id, repo_id, query, intent, result_count, top_score, latency_ms, created_at
           FROM query_history
           WHERE repo_id=?
           ORDER BY id DESC
           LIMIT ?""",
        (repo_id, limit),
    ) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


@router.delete("/history/{history_id}", status_code=204)
async def delete_history(
    history_id: int,
    db: aiosqlite.Connection = Depends(get_db),
):
    await db.execute("DELETE FROM query_history WHERE id=?", (history_id,))
    await db.commit()
