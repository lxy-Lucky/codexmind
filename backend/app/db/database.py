from __future__ import annotations

import logging

import aiosqlite

from app.core.config import settings

logger = logging.getLogger(__name__)

DB_PATH = settings.SQLITE_PATH


async def get_db() -> aiosqlite.Connection:
    """FastAPI Depends 使用的连接工厂（每次请求独立连接）"""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        yield db


async def init_db() -> None:
    """应用启动时建表（幂等）"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
            PRAGMA journal_mode=WAL;

            -- ── 仓库元数据 ────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS repos (
                id           TEXT PRIMARY KEY,
                name         TEXT NOT NULL,
                root_path    TEXT NOT NULL UNIQUE,
                language     TEXT,
                file_count   INTEGER DEFAULT 0,
                chunk_count  INTEGER DEFAULT 0,
                symbol_count INTEGER DEFAULT 0,
                edge_count   INTEGER DEFAULT 0,
                indexed      INTEGER DEFAULT 0,  -- 0=未索引 1=索引中 2=完成 3=失败
                created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at   DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            -- ── 符号表（供 BM25 和 graph_service 快速查 chunk_id）────
            -- 图结构本体在 Neo4j；此表是轻量索引，避免跨库 JOIN
            CREATE TABLE IF NOT EXISTS symbols (
                id          TEXT PRIMARY KEY,   -- uuid，与 Neo4j node id 一致
                repo_id     TEXT NOT NULL,
                file_path   TEXT NOT NULL,
                class_name  TEXT,
                method_name TEXT,
                signature   TEXT,
                line_start  INTEGER,
                line_end    INTEGER,
                chunk_id    TEXT,               -- 对应 Qdrant point id
                pagerank    REAL DEFAULT 0.0,
                FOREIGN KEY (repo_id) REFERENCES repos(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_symbols_repo
                ON symbols(repo_id);
            CREATE INDEX IF NOT EXISTS idx_symbols_method
                ON symbols(repo_id, method_name);
            CREATE INDEX IF NOT EXISTS idx_symbols_class
                ON symbols(repo_id, class_name);
            CREATE INDEX IF NOT EXISTS idx_symbols_chunk
                ON symbols(chunk_id);

            -- ── BM25 索引元数据（序列化文件路径）─────────────────────
            CREATE TABLE IF NOT EXISTS bm25_meta (
                repo_id    TEXT PRIMARY KEY,
                index_path TEXT NOT NULL,       -- pickle 文件路径
                doc_count  INTEGER DEFAULT 0,
                built_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (repo_id) REFERENCES repos(id) ON DELETE CASCADE
            );

            -- ── 索引日志 ──────────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS index_logs (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                repo_id    TEXT NOT NULL,
                level      TEXT NOT NULL,       -- INFO|WARNING|ERROR
                message    TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            -- ── 查询历史 ──────────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS query_history (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                repo_id      TEXT NOT NULL,
                query        TEXT NOT NULL,
                intent       TEXT,              -- symbol_lookup|semantic|call_chain|impact
                result_count INTEGER DEFAULT 0,
                top_score    REAL DEFAULT 0.0,
                latency_ms   INTEGER DEFAULT 0,
                created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (repo_id) REFERENCES repos(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_history_repo
                ON query_history(repo_id, created_at DESC);
        """)
        await db.commit()
    logger.info("SQLite initialized at %s", DB_PATH)
