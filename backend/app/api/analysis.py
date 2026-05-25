from __future__ import annotations

import logging
from typing import Any

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.core.i18n import get_locale, t
from app.db.database import get_db
from app.models.analysis import AnalysisRequest, ChatMessage
from app.services.analysis_service import stream_analysis

router = APIRouter(prefix="/api/analyze", tags=["analysis"])
logger = logging.getLogger(__name__)

VALID_MODES = {"summary", "bug", "deps", "custom"}


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
