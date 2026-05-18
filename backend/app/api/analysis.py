from __future__ import annotations

import logging

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app.db.database import get_db
from app.models.analysis import AnalysisRequest
from app.services.llm_service import stream_analysis

router = APIRouter(prefix="/api/analyze", tags=["analysis"])
logger = logging.getLogger(__name__)

VALID_MODES = {"summary", "bug", "deps"}


@router.post("/stream")
async def analyze_stream(
    body: AnalysisRequest,
    db: aiosqlite.Connection = Depends(get_db),
):
    if body.mode not in VALID_MODES:
        raise HTTPException(400, f"mode 必须是 {VALID_MODES} 之一")

    # 检查仓库存在
    async with db.execute("SELECT id FROM repos WHERE id=?", (body.repo_id,)) as cur:
        if not await cur.fetchone():
            raise HTTPException(404, "仓库不存在")

    if not body.code.strip():
        raise HTTPException(400, "code 不能为空")

    return StreamingResponse(
        stream_analysis(body),
        media_type="text/event-stream",
        headers={
            "Cache-Control":               "no-cache",
            "X-Accel-Buffering":           "no",   # 关闭 nginx 缓冲
            "Access-Control-Allow-Origin": "*",
        },
    )
