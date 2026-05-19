from __future__ import annotations

import logging
from functools import lru_cache

from neo4j import AsyncGraphDatabase, AsyncDriver

from app.core.config import settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_neo4j() -> AsyncDriver:
    """返回 Neo4j AsyncDriver 单例（懒加载）"""
    driver = AsyncGraphDatabase.driver(
        settings.NEO4J_URI,
        auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
        max_connection_pool_size=20,
    )
    return driver


async def close_neo4j() -> None:
    """应用关闭时调用"""
    driver = get_neo4j()
    await driver.close()


async def init_neo4j() -> None:
    """
    建立约束和索引（幂等）。
    Neo4j 5 社区版支持单属性约束和索引。
    """
    driver = get_neo4j()
    async with driver.session() as session:
        stmts = [
            # ── 唯一约束 ──────────────────────────────────────────────
            """
            CREATE CONSTRAINT method_id_unique IF NOT EXISTS
            FOR (m:Method) REQUIRE m.id IS UNIQUE
            """,
            """
            CREATE CONSTRAINT class_id_unique IF NOT EXISTS
            FOR (c:Class) REQUIRE c.id IS UNIQUE
            """,
            """
            CREATE CONSTRAINT file_id_unique IF NOT EXISTS
            FOR (f:File) REQUIRE f.id IS UNIQUE
            """,

            # ── 检索索引 ──────────────────────────────────────────────
            """
            CREATE INDEX method_name_idx IF NOT EXISTS
            FOR (m:Method) ON (m.repo_id, m.name)
            """,
            """
            CREATE INDEX method_class_idx IF NOT EXISTS
            FOR (m:Method) ON (m.repo_id, m.class_name)
            """,
            """
            CREATE INDEX class_name_idx IF NOT EXISTS
            FOR (c:Class) ON (c.repo_id, c.name)
            """,
        ]
        for stmt in stmts:
            try:
                await session.run(stmt)
            except Exception as e:
                # 约束已存在时 Neo4j 会报错，忽略即可
                logger.debug("Neo4j schema stmt skipped: %s", e)

    logger.info("Neo4j schema initialized")


# ── Cypher 工具函数 ────────────────────────────────────────────────────────────

async def run_query(cypher: str, params: dict | None = None) -> list[dict]:
    """执行 Cypher，返回记录列表（dict）"""
    driver = get_neo4j()
    async with driver.session() as session:
        result = await session.run(cypher, params or {})
        records = await result.data()
        return records


async def run_write(cypher: str, params: dict | None = None) -> None:
    """执行写操作 Cypher（MERGE / CREATE / SET）"""
    driver = get_neo4j()
    async with driver.session() as session:
        await session.run(cypher, params or {})


async def run_write_batch(cypher: str, batch: list[dict]) -> None:
    """
    批量写入。
    cypher 应使用 UNWIND $rows AS row ... 模式。
    """
    if not batch:
        return
    driver = get_neo4j()
    async with driver.session() as session:
        await session.run(cypher, {"rows": batch})
