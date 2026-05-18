from __future__ import annotations

import logging
from functools import lru_cache

from qdrant_client import AsyncQdrantClient
from qdrant_client.http.models import (
    Distance,
    VectorParams,
    PayloadSchemaType,
)

from app.core.config import settings

logger = logging.getLogger(__name__)

# ── 单例 ─────────────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def get_qdrant() -> AsyncQdrantClient:
    return AsyncQdrantClient(
        host=settings.QDRANT_HOST,
        port=settings.QDRANT_PORT,
    )


# ── Collection 命名规则 ───────────────────────────────────────────────────────

def collection_name(repo_id: str) -> str:
    """每个仓库独立 collection：codex_<repo_id>"""
    return f"codex_{repo_id}"


# ── 初始化 collection（幂等）─────────────────────────────────────────────────

async def ensure_collection(repo_id: str) -> None:
    client = get_qdrant()
    name = collection_name(repo_id)

    existing = await client.get_collections()
    names = [c.name for c in existing.collections]

    if name in names:
        logger.info("Collection %s already exists", name)
        return

    await client.create_collection(
        collection_name=name,
        vectors_config=VectorParams(
            size=settings.vector_size,
            distance=Distance.COSINE,
        ),
    )

    # 为常用 payload 字段建索引，加速 filter
    for field, schema in [
        ("file_path", PayloadSchemaType.KEYWORD),
        ("language", PayloadSchemaType.KEYWORD),
        ("chunk_type", PayloadSchemaType.KEYWORD),
    ]:
        await client.create_payload_index(
            collection_name=name,
            field_name=field,
            field_schema=schema,
        )

    logger.info("Collection %s created (dim=%d)", name, settings.vector_size)


async def delete_collection(repo_id: str) -> None:
    client = get_qdrant()
    name = collection_name(repo_id)
    await client.delete_collection(collection_name=name)
    logger.info("Collection %s deleted", name)


async def collection_info(repo_id: str) -> dict:
    client = get_qdrant()
    name = collection_name(repo_id)
    info = await client.get_collection(collection_name=name)
    return {
        "vectors_count": info.vectors_count,
        "indexed_vectors_count": info.indexed_vectors_count,
        "status": info.status.value,
    }
