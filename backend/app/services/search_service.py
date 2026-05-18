from __future__ import annotations

import logging
import re
import time

from qdrant_client.http.models import Filter, FieldCondition, MatchValue

from app.core.embedder import embed_query
from app.core.qdrant_client import get_qdrant, collection_name
from app.models.search import SearchRequest, SearchResponse, SearchResultItem

logger = logging.getLogger(__name__)

# ── 结果质量过滤 ───────────────────────────────────────────────────────────────

def _has_body(snippet: str) -> bool:
    """判断 snippet 是否包含方法体（有花括号且不只是接口签名）"""
    stripped = snippet.strip()
    # 接口方法签名：以 ; 结尾，没有 { }
    if stripped.endswith(';') and '{' not in stripped:
        return False
    # 只有注释（/** ... */ 或 // 开头）
    lines = [l.strip() for l in stripped.splitlines() if l.strip()]
    non_comment = [l for l in lines
                   if not l.startswith('*')
                   and not l.startswith('//')
                   and not l.startswith('/*')
                   and l not in ('', '}')]
    if len(non_comment) <= 1:
        return False
    return True


def _snippet_score(item: dict) -> float:
    """
    对检索结果打质量分，用于重排序。
    综合考虑：向量分、是否有方法体、行数多少（适中最好）、是否包含关键词。
    """
    base   = item["score"]
    snippet = item["snippet"]
    lines  = snippet.count('\n') + 1

    bonus = 0.0
    # 有方法体加分
    if _has_body(snippet):
        bonus += 0.08
    # 行数适中（5-50行）加分
    if 5 <= lines <= 50:
        bonus += 0.03
    # 纯注释、接口签名扣分
    if not _has_body(snippet):
        bonus -= 0.12
    # impl / ServiceImpl / Controller 实现类加分
    fp = item["file_path"].lower()
    if any(k in fp for k in ['impl', 'controller', 'handler', 'processor']):
        bonus += 0.04
    # 接口文件（以 I 开头类名，mapper 文件）轻微扣分
    if any(k in fp for k in ['mapper', '/i', 'interface']):
        bonus -= 0.03

    return round(base + bonus, 4)


def _deduplicate(results: list[SearchResultItem]) -> list[SearchResultItem]:
    """
    去重：同一文件行范围高度重叠的 chunk 只保留分数最高的。
    """
    kept: list[SearchResultItem] = []
    for item in results:
        overlap = False
        for k in kept:
            if k.file_path != item.file_path:
                continue
            # 行范围重叠超过 60%
            overlap_start = max(k.line_start, item.line_start)
            overlap_end   = min(k.line_end,   item.line_end)
            if overlap_end >= overlap_start:
                overlap_len = overlap_end - overlap_start + 1
                item_len    = item.line_end - item.line_start + 1
                if item_len > 0 and overlap_len / item_len > 0.6:
                    overlap = True
                    break
        if not overlap:
            kept.append(item)
    return kept


# ── 主接口 ────────────────────────────────────────────────────────────────────

async def semantic_search(req: SearchRequest) -> SearchResponse:
    t0 = time.monotonic()

    query_vec = await embed_query(req.query)

    client = get_qdrant()
    col    = collection_name(req.repo_id)

    # 多拉一些候选，后处理过滤后再截断到 top_k
    fetch_k = req.top_k * 3

    query_filter = None
    if req.language_filter:
        query_filter = Filter(
            must=[FieldCondition(
                key="language",
                match=MatchValue(value=req.language_filter),
            )]
        )

    res = await client.query_points(
        collection_name=col,
        query=query_vec,
        limit=fetch_k,
        query_filter=query_filter,
        with_payload=True,
    )

    # 构建原始结果列表（带原始 score 用于重排）
    raw: list[dict] = [
        {
            "file_path":  h.payload["file_path"],
            "line_start": h.payload["line_start"],
            "line_end":   h.payload["line_end"],
            "snippet":    h.payload.get("text", ""),
            "score":      round(h.score, 4),
            "language":   h.payload.get("language", ""),
            "chunk_type": h.payload.get("chunk_type", "block"),
        }
        for h in res.points
    ]

    # 重排序：用质量分替换原始分
    for item in raw:
        item["rerank_score"] = _snippet_score(item)
    raw.sort(key=lambda x: x["rerank_score"], reverse=True)

    # 构建 SearchResultItem，score 展示 rerank 后的值
    reranked = [
        SearchResultItem(
            file_path  = r["file_path"],
            line_start = r["line_start"],
            line_end   = r["line_end"],
            snippet    = r["snippet"],
            score      = r["rerank_score"],
            language   = r["language"],
            chunk_type = r["chunk_type"],
        )
        for r in raw
    ]

    # 去重 + 截断
    deduped  = _deduplicate(reranked)
    final    = deduped[:req.top_k]

    latency_ms = int((time.monotonic() - t0) * 1000)
    logger.info("Search [%s] '%s' → %d results (fetched %d, reranked, deduped) in %dms",
                req.repo_id, req.query, len(final), len(raw), latency_ms)

    return SearchResponse(
        results    = final,
        total      = len(final),
        latency_ms = latency_ms,
        query      = req.query,
    )
