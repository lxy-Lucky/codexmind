from __future__ import annotations

import logging
from typing import Any

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.db.database import get_db
from app.models.analysis import AnalysisRequest, ChatMessage
from app.services.llm_service import stream_analysis

router = APIRouter(prefix="/api/analyze", tags=["analysis"])
logger = logging.getLogger(__name__)

VALID_MODES = {"summary", "bug", "deps", "custom"}


@router.post("/stream")
async def analyze_stream(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
):
    # 直接读 JSON，支持前端附加的 _history 字段
    body_raw: dict[str, Any] = await request.json()

    try:
        req = AnalysisRequest(**{k: v for k, v in body_raw.items() if k != '_history'})
    except Exception as e:
        raise HTTPException(422, str(e))

    # 解析对话历史
    raw_history = body_raw.get("_history", []) or []
    history = [ChatMessage(**m) for m in raw_history if isinstance(m, dict)]

    if req.mode not in VALID_MODES:
        raise HTTPException(400, f"mode 必须是 {VALID_MODES} 之一")

    async with db.execute("SELECT id FROM repos WHERE id=?", (req.repo_id,)) as cur:
        if not await cur.fetchone():
            raise HTTPException(404, "仓库不存在")

    if not req.code.strip():
        raise HTTPException(400, "code 不能为空")

    if req.mode == "custom" and not (req.custom_prompt or "").strip():
        raise HTTPException(400, "custom 模式需要提供 custom_prompt")

    return StreamingResponse(
        stream_analysis(req, history),
        media_type="text/event-stream",
        headers={
            "Cache-Control":               "no-cache",
            "X-Accel-Buffering":           "no",
            "Access-Control-Allow-Origin": "*",
        },
    )
