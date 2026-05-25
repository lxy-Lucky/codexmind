from __future__ import annotations

import logging
from functools import lru_cache

from qdrant_client import AsyncQdrantClient
from qdrant_client.http.models import (
    Distance,
    VectorParams,
    SparseVectorParams,
    SparseIndexParams,
    PayloadSchemaType,
    HnswConfigDiff,
    ScalarQuantization,
    ScalarQuantizationConfig,
    ScalarType,
)

# collection 内的向量字段名（dense + sparse 都用具名向量）
DENSE_VECTOR_NAME  = "dense"
SPARSE_VECTOR_NAME = "sparse"

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
        # Named vectors：dense（bge-m3 dense 1024d）+ sparse（bge-m3 lexical_weights）
        vectors_config={
            DENSE_VECTOR_NAME: VectorParams(
                size=settings.vector_size,
                distance=Distance.COSINE,
                on_disk=False,          # 向量常驻内存，不走磁盘 mmap
            ),
        },
        sparse_vectors_config={
            SPARSE_VECTOR_NAME: SparseVectorParams(
                index=SparseIndexParams(on_disk=False),
            ),
        },
        # HNSW：ef_construct 128 → 索引质量更好，搜索时 recall 更高
        hnsw_config=HnswConfigDiff(
            m=16,
            ef_construct=128,
            on_disk=False,
        ),
        # 标量量化 INT8：内存降至 1/4，搜索速度提升 2–4×，精度损失 < 1%
        # rescore=True（默认）：用原始向量对 top 候选重排，保证最终精度
        quantization_config=ScalarQuantization(
            scalar=ScalarQuantizationConfig(
                type=ScalarType.INT8,
                quantile=0.99,      # 裁剪 1% 极端值，减少量化误差
                always_ram=True,    # 量化向量也常驻内存
            ),
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
        "vectors_count": info.points_count or 0,
        "indexed_vectors_count": info.indexed_vectors_count or 0,
        "status": info.status.value,
    }
