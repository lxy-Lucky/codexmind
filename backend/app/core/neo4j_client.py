from __future__ import annotations

import asyncio
import logging
from functools import lru_cache

from neo4j import AsyncGraphDatabase, AsyncDriver

from app.core.config import settings

logger = logging.getLogger(__name__)

# 每个写事务最多处理的行数，避免单事务过大导致连接超时
NEO4J_WRITE_CHUNK = 300


@lru_cache(maxsize=1)
def get_neo4j() -> AsyncDriver:
    """返回 Neo4j AsyncDriver 单例（懒加载）"""
    driver = AsyncGraphDatabase.driver(
        settings.NEO4J_URI,
        auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
        max_connection_pool_size=20,
        max_connection_lifetime=3600,        # 连接最长存活 1h，防止僵尸连接
        connection_acquisition_timeout=60,   # 等待空闲连接最多 60s
        keep_alive=True,
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
    """执行 Cypher，返回记录列表（dict）。连接断开时自动重试最多 3 次。"""
    driver = get_neo4j()
    _params = params or {}
    for attempt in range(3):
        try:
            async with driver.session() as session:
                result = await session.run(cypher, _params)
                return await result.data()
        except Exception as e:
            if attempt < 2:
                wait = 1 << attempt   # 1s, 2s
                logger.warning("Neo4j query retry %d/3 in %ds: %s", attempt + 1, wait, e)
                await asyncio.sleep(wait)
            else:
                raise
    return []   # unreachable, but satisfies type checker


async def run_write(cypher: str, params: dict | None = None) -> None:
    """执行写操作 Cypher（MERGE / CREATE / SET），失败时自动重试最多 3 次。"""
    _params = params or {}
    driver = get_neo4j()
    for attempt in range(3):
        try:
            async with driver.session() as session:
                async def _work(tx, p=_params):
                    await tx.run(cypher, p)
                await session.execute_write(_work)
            return
        except Exception as e:
            if attempt < 2:
                wait = 1 << attempt
                logger.warning("Neo4j write retry %d/3 in %ds: %s", attempt + 1, wait, e)
                await asyncio.sleep(wait)
            else:
                raise


async def run_write_batch(cypher: str, batch: list[dict]) -> None:
    """
    批量写入，分块提交（每块 NEO4J_WRITE_CHUNK 行），每块独立重试。
    cypher 应使用 UNWIND $rows AS row ... 模式。
    大库几千节点拆成多个小事务，避免单事务过长导致 Neo4j 连接断开。
    """
    if not batch:
        return
    driver = get_neo4j()
    for chunk_start in range(0, len(batch), NEO4J_WRITE_CHUNK):
        chunk = batch[chunk_start: chunk_start + NEO4J_WRITE_CHUNK]
        for attempt in range(3):
            try:
                async with driver.session() as session:
                    async def _work(tx, rows=chunk):
                        await tx.run(cypher, {"rows": rows})
                    await session.execute_write(_work)
                break   # 成功，跳出重试循环
            except Exception as e:
                if attempt < 2:
                    wait = 1 << attempt
                    logger.warning(
                        "Neo4j batch write retry %d/3 (chunk %d–%d) in %ds: %s",
                        attempt + 1, chunk_start, chunk_start + len(chunk), wait, e,
                    )
                    await asyncio.sleep(wait)
                else:
                    raise
        # 每块提交后让出事件循环，避免长时间阻塞 asyncio
        await asyncio.sleep(0)
