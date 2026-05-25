from __future__ import annotations

import asyncio
import logging
import warnings
from functools import lru_cache
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _load_model() -> Any:
    """懒加载 bge-m3，首次调用时触发，后续复用同一实例"""
    logger.info(
        "Loading embedding model %s on %s ...",
        settings.EMBEDDING_MODEL,
        settings.EMBEDDING_DEVICE,
    )
    # 延迟导入，避免启动时强依赖 GPU
    warnings.filterwarnings("ignore", message="Can't initialize NVML", category=UserWarning)
    from FlagEmbedding import BGEM3FlagModel  # type: ignore

    model = BGEM3FlagModel(
        settings.EMBEDDING_MODEL,
        use_fp16=True,
        device=settings.EMBEDDING_DEVICE,
    )
    logger.info("Embedding model ready (dense + sparse 双输出)")
    return model


# ── Dense-only API（向后兼容旧调用方）─────────────────────────────────────────

def embed_sync(texts: list[str]) -> list[list[float]]:
    """同步 dense embedding，在线程池中调用"""
    model = _load_model()
    result = model.encode(
        texts,
        batch_size=settings.EMBEDDING_BATCH_SIZE,
        max_length=settings.EMBEDDING_MAX_LENGTH,
        return_dense=True,
        return_sparse=False,
        return_colbert_vecs=False,
    )
    return result["dense_vecs"].tolist()


async def embed(texts: list[str]) -> list[list[float]]:
    """异步 dense embedding（向后兼容）"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, embed_sync, texts)


async def embed_query(query: str) -> list[float]:
    """单条 query dense embedding"""
    vecs = await embed([query])
    return vecs[0]


# ── Dense + Sparse 双输出 API（bge-m3 同模型一次前向）─────────────────────────
# bge-m3 的 sparse 是多语言学习稀疏向量（lexical_weights），比 BM25 在日/中场景
# 强一大截，且与 dense 共享模型权重和前向计算，不增加额外 GPU 开销。

def _encode_sync(texts: list[str]) -> tuple[list[list[float]], list[dict[int, float]]]:
    """同步 dense + sparse"""
    model = _load_model()
    result = model.encode(
        texts,
        batch_size=settings.EMBEDDING_BATCH_SIZE,
        max_length=settings.EMBEDDING_MAX_LENGTH,
        return_dense=True,
        return_sparse=True,
        return_colbert_vecs=False,
    )
    dense = result["dense_vecs"].tolist()
    # lexical_weights: List[Dict[token_id_str, weight]]。token_id 是 str，转 int。
    sparse_raw = result.get("lexical_weights", [])
    sparse: list[dict[int, float]] = []
    for d in sparse_raw:
        sparse.append({int(k): float(v) for k, v in d.items()})
    return dense, sparse


async def embed_with_sparse(
    texts: list[str],
) -> tuple[list[list[float]], list[dict[int, float]]]:
    """
    异步 dense + sparse embedding。
    返回 (dense_list, sparse_list)，sparse_list[i] 是 {token_id: weight} 字典。
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _encode_sync, texts)


async def encode_query(query: str) -> tuple[list[float], dict[int, float]]:
    """单条 query 的 dense + sparse"""
    dense, sparse = await embed_with_sparse([query])
    return dense[0], sparse[0]


def sparse_to_indices_values(
    sparse: dict[int, float],
) -> tuple[list[int], list[float]]:
    """把 {token_id: weight} 转成 Qdrant SparseVector 的 (indices, values) 平行数组"""
    if not sparse:
        return [], []
    indices = list(sparse.keys())
    values  = [sparse[i] for i in indices]
    return indices, values
