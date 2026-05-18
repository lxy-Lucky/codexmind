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

            CREATE TABLE IF NOT EXISTS repos (
                id          TEXT PRIMARY KEY,
                name        TEXT NOT NULL,
                root_path   TEXT NOT NULL UNIQUE,
                language    TEXT,              -- 主要语言（扫描后更新）
                file_count  INTEGER DEFAULT 0,
                chunk_count INTEGER DEFAULT 0,
                indexed     INTEGER DEFAULT 0, -- 0=未索引 1=索引中 2=完成 3=失败
                created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS query_history (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                repo_id      TEXT NOT NULL,
                mode         TEXT NOT NULL,    -- semantic|bug|explain|deps
                query        TEXT NOT NULL,
                result_count INTEGER DEFAULT 0,
                latency_ms   INTEGER DEFAULT 0,
                created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (repo_id) REFERENCES repos(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_history_repo
                ON query_history(repo_id, created_at DESC);

            CREATE TABLE IF NOT EXISTS index_logs (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                repo_id    TEXT NOT NULL,
                level      TEXT NOT NULL,  -- INFO|WARNING|ERROR
                message    TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
        """)
        await db.commit()
    logger.info("SQLite initialized at %s", DB_PATH)
