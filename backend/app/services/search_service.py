from __future__ import annotations

import logging
import time

from qdrant_client.http.models import Filter, FieldCondition, MatchValue

from app.core.embedder import embed_query
from app.core.qdrant_client import get_qdrant, collection_name
from app.models.search import SearchRequest, SearchResponse, SearchResultItem

logger = logging.getLogger(__name__)


async def semantic_search(req: SearchRequest) -> SearchResponse:
    t0 = time.monotonic()

    query_vec = await embed_query(req.query)

    client = get_qdrant()
    col    = collection_name(req.repo_id)

    # 可选：按语言过滤
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
        limit=req.top_k,
        query_filter=query_filter,
        with_payload=True,
    )

    results = [
        SearchResultItem(
            file_path  = h.payload["file_path"],
            line_start = h.payload["line_start"],
            line_end   = h.payload["line_end"],
            snippet    = h.payload.get("text", ""),
            score      = round(h.score, 4),
            language   = h.payload.get("language", ""),
            chunk_type = h.payload.get("chunk_type", "block"),
        )
        for h in res.points
    ]

    latency_ms = int((time.monotonic() - t0) * 1000)
    logger.info("Search [%s] '%s' → %d results in %dms",
                req.repo_id, req.query, len(results), latency_ms)

    return SearchResponse(
        results    = results,
        total      = len(results),
        latency_ms = latency_ms,
        query      = req.query,
    )
