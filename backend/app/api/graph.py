from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from typing import Optional

from app.services.graph_service import (
    get_method_graph,
    get_impact,
    get_class_graph,
    get_call_path,
)
from app.models.graph import GraphResponse, ImpactResponse, PathResponse

router = APIRouter(prefix="/api/graph", tags=["graph"])


@router.get("/{repo_id}/method/{symbol_id}", response_model=GraphResponse)
async def method_graph(
    repo_id: str,
    symbol_id: str,
    depth: int = Query(default=2, ge=1, le=5),
):
    """方法级调用图（双向，N 跳）"""
    try:
        return await get_method_graph(repo_id, symbol_id, depth)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{repo_id}/impact/{symbol_id}", response_model=ImpactResponse)
async def impact_graph(
    repo_id: str,
    symbol_id: str,
    max_depth: int = Query(default=3, ge=1, le=5),
):
    """影响域：修改此方法后哪些调用者受影响"""
    try:
        return await get_impact(repo_id, symbol_id, max_depth)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{repo_id}/class", response_model=GraphResponse)
async def class_graph(
    repo_id: str,
    class_name: Optional[str] = Query(default=None),
):
    """类级依赖图"""
    try:
        return await get_class_graph(repo_id, class_name)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{repo_id}/path", response_model=PathResponse)
async def call_path(
    repo_id: str,
    from_id: str = Query(...),
    to_id: str   = Query(...),
):
    """两个方法之间的最短调用路径"""
    try:
        return await get_call_path(repo_id, from_id, to_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
