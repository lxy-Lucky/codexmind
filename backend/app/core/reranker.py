from __future__ import annotations

"""
Cross-encoder reranker (bge-reranker-v2-m3)
-------------------------------------------
- 多语言交叉编码器，对日/中/英 query 都很强
- 用法：对 dense+sparse 融合后的 top-N 候选打分，作为 final rerank 主信号
- 与 bge-m3 同家族，第一次调用自动从 HuggingFace 拉模型（~1.1GB）
"""

import asyncio
import logging
import warnings
from functools import lru_cache
from typing import Any, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _load_reranker() -> Optional[Any]:
    """懒加载 bge-reranker-v2-m3。模型不可用时返回 None（不阻断主搜索流程）"""
    if not settings.USE_RERANKER:
        return None
    logger.info(
        "Loading reranker %s on %s ...",
        settings.RERANKER_MODEL,
        settings.RERANKER_DEVICE,
    )
    try:
        warnings.filterwarnings("ignore", message="Can't initialize NVML", category=UserWarning)
        from FlagEmbedding import FlagReranker  # type: ignore
        model = FlagReranker(
            settings.RERANKER_MODEL,
            use_fp16=True,
            device=settings.RERANKER_DEVICE,
        )
        logger.info("Reranker ready")
        return model
    except Exception as e:
        logger.warning("Reranker load failed, falling back to non-rerank path: %s", e)
        return None


def _rerank_sync(query: str, docs: list[str]) -> list[float]:
    model = _load_reranker()
    if model is None or not docs:
        return [0.0] * len(docs)
    pairs = [[query, d] for d in docs]
    try:
        scores = model.compute_score(
            pairs,
            batch_size=settings.RERANKER_BATCH_SIZE,
            normalize=True,   # sigmoid 归一化到 [0,1]
        )
        # compute_score 单对返回 float，多对返回 list
        if isinstance(scores, float):
            return [scores]
        return [float(s) for s in scores]
    except Exception as e:
        logger.warning("Reranker compute_score failed: %s", e)
        return [0.0] * len(docs)


async def rerank(query: str, docs: list[str]) -> list[float]:
    """
    异步重排：对 (query, doc) pair 打分，返回与 docs 等长的 score 列表（已归一化到 [0,1]）。
    模型未就绪或失败时返回全 0，调用方应有降级路径。
    """
    if not docs:
        return []
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _rerank_sync, query, docs)


def is_available() -> bool:
    """供 search_service 判断要不要走 rerank 路径"""
    return settings.USE_RERANKER and _load_reranker() is not None
