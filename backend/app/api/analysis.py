from __future__ import annotations

import logging
from typing import Any

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.core.i18n import get_locale, t
from app.db.database import get_db
from app.models.analysis import AnalysisRequest, ChatMessage
from app.services.analysis_service import stream_analysis, generate_file_docs, find_similar_code

router = APIRouter(prefix="/api/analyze", tags=["analysis"])
logger = logging.getLogger(__name__)

VALID_MODES = {"summary", "bug", "deps", "custom", "docs"}


@router.post("/stream")
async def analyze_stream(
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    locale: str = Depends(get_locale),
):
    body_raw: dict[str, Any] = await request.json()

    try:
        req = AnalysisRequest(**{k: v for k, v in body_raw.items() if k != "_history"})
    except Exception as e:
        raise HTTPException(422, str(e))

    raw_history = body_raw.get("_history", []) or []
    history = [ChatMessage(**m) for m in raw_history if isinstance(m, dict)]

    if req.mode not in VALID_MODES:
        raise HTTPException(400, t("analyze.invalid_mode", locale, modes=sorted(VALID_MODES)))

    async with db.execute("SELECT id FROM repos WHERE id=?", (req.repo_id,)) as cur:
        if not await cur.fetchone():
            raise HTTPException(404, t("repo.not_found", locale))

    if not req.code.strip():
        raise HTTPException(400, t("analyze.empty_code", locale))

    if req.mode == "custom" and not (req.custom_prompt or "").strip():
        raise HTTPException(400, t("analyze.custom_prompt_required", locale))

    return StreamingResponse(
        stream_analysis(req, history, locale=locale),
        media_type="text/event-stream",
        headers={
            "Cache-Control":               "no-cache",
            "X-Accel-Buffering":           "no",
            "Access-Control-Allow-Origin": "*",
        },
    )


@router.get("/docs-file")
async def docs_file_stream(
    repo_id: str,
    file_path: str,
    request: Request,
    db: aiosqlite.Connection = Depends(get_db),
    locale: str = Depends(get_locale),
):
    """整文件文档生成：按类逐一调用 LLM，SSE 流式返回（含 progress 事件）。"""
    async with db.execute("SELECT id, root_path FROM repos WHERE id=?", (repo_id,)) as cur:
        row = await cur.fetchone()
        if not row:
            raise HTTPException(404, t("repo.not_found", locale))
        root_path: str = row[1] or ""

    return StreamingResponse(
        generate_file_docs(repo_id, file_path, root_path=root_path, locale=locale),
        media_type="text/event-stream",
        headers={
            "Cache-Control":               "no-cache",
            "X-Accel-Buffering":           "no",
            "Access-Control-Allow-Origin": "*",
        },
    )


@router.get("/{repo_id}/similar")
async def similar_code(
    repo_id: str,
    symbol_id: str,
    top_k: int = 5,
    db: aiosqlite.Connection = Depends(get_db),
    locale: str = Depends(get_locale),
):
    """查询与指定 symbol 最相似的代码片段（Qdrant 向量 NN）。"""
    async with db.execute("SELECT id FROM repos WHERE id=?", (repo_id,)) as cur:
        if not await cur.fetchone():
            raise HTTPException(404, t("repo.not_found", locale))
    results = await find_similar_code(repo_id, symbol_id, top_k=min(top_k, 20))
    return {"items": results}
