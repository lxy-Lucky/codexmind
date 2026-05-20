from __future__ import annotations

import asyncio
import logging
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
    from FlagEmbedding import BGEM3FlagModel  # type: ignore

    model = BGEM3FlagModel(
        settings.EMBEDDING_MODEL,
        use_fp16=True,
        device=settings.EMBEDDING_DEVICE,
    )
    logger.info("Embedding model ready")
    return model


def embed_sync(texts: list[str]) -> list[list[float]]:
    """同步 embedding，在线程池中调用"""
    model = _load_model()
    result = model.encode(
        texts,
        batch_size=settings.EMBEDDING_BATCH_SIZE,
        max_length=settings.EMBEDDING_MAX_LENGTH,
        return_dense=True,
        return_sparse=False,
        return_colbert_vecs=False,
    )
    # dense_vecs: numpy array (N, 1024)
    return result["dense_vecs"].tolist()


async def embed(texts: list[str]) -> list[list[float]]:
    """异步 embedding：把同步调用推入线程池，不阻塞事件循环"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, embed_sync, texts)


async def embed_query(query: str) -> list[float]:
    """单条 query embedding"""
    vecs = await embed([query])
    return vecs[0]
