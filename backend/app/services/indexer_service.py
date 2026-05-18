from __future__ import annotations

"""
Indexer Service
--------------
流程：
  1. os.walk 扫描 repo 目录，过滤支持的扩展名
  2. 每个文件用 tree-sitter 解析，按函数/方法/类切分 chunk
  3. 超长 chunk 做 sliding window 切分
  4. 批量 embed（bge-m3）
  5. 批量写入 Qdrant
  6. 更新 SQLite repos 表状态
"""

import asyncio
import logging
import os
import uuid
from pathlib import Path
from typing import Iterator, Optional

import aiofiles
from qdrant_client.http.models import PointStruct

from app.core.config import settings
from app.core.embedder import embed
from app.core.qdrant_client import get_qdrant, ensure_collection, collection_name
from app.services.repo_service import SKIP_DIRS, get_language
from app.services.parser_service import parse_chunks

logger = logging.getLogger(__name__)

BATCH_SIZE = 32  # 每批 embed + 写入 Qdrant 的 chunk 数


# ── 主入口（在后台任务中调用）────────────────────────────────────────────────

async def run_index(repo_id: str, root_path: str, db) -> None:
    """
    异步索引整个仓库。
    db: aiosqlite.Connection，用于更新进度和写日志。
    """
    root = Path(root_path)

    # 标记为"索引中"
    await _update_status(db, repo_id, status=1, message="开始扫描文件...")
    await ensure_collection(repo_id)

    client = get_qdrant()
    col = collection_name(repo_id)

    total_files = 0
    total_chunks = 0
    batch_points: list[PointStruct] = []
    batch_texts: list[str] = []
    batch_meta: list[dict] = []

    try:
        for file_path, rel_path, language in _iter_code_files(root):
            total_files += 1
            await _log(db, repo_id, "INFO", f"解析: {rel_path}")

            try:
                async with aiofiles.open(file_path, encoding="utf-8", errors="replace") as f:
                    source = await f.read()
            except Exception as e:
                await _log(db, repo_id, "WARNING", f"读取失败 {rel_path}: {e}")
                continue

            chunks = parse_chunks(source, language, rel_path)

            for chunk in chunks:
                batch_texts.append(chunk["text"])
                batch_meta.append({
                    "repo_id":    repo_id,
                    "file_path":  rel_path,
                    "line_start": chunk["line_start"],
                    "line_end":   chunk["line_end"],
                    "language":   language,
                    "chunk_type": chunk["chunk_type"],
                    "symbol":     chunk.get("symbol", ""),
                })
                total_chunks += 1

                # 攒够一批就 embed + 写入
                if len(batch_texts) >= BATCH_SIZE:
                    await _flush(client, col, batch_texts, batch_meta)
                    batch_texts, batch_meta = [], []
                    await _update_status(
                        db, repo_id, status=1,
                        message=f"已处理 {total_files} 文件 / {total_chunks} chunks"
                    )

        # 写剩余
        if batch_texts:
            await _flush(client, col, batch_texts, batch_meta)

        # 完成
        await db.execute(
            """UPDATE repos SET indexed=2, file_count=?, chunk_count=?, updated_at=CURRENT_TIMESTAMP
               WHERE id=?""",
            (total_files, total_chunks, repo_id),
        )
        await db.commit()
        await _log(db, repo_id, "INFO",
                   f"索引完成：{total_files} 文件，{total_chunks} chunks")
        logger.info("[%s] Index done: %d files, %d chunks", repo_id, total_files, total_chunks)

    except Exception as e:
        logger.exception("[%s] Index failed: %s", repo_id, e)
        await db.execute(
            "UPDATE repos SET indexed=3, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (repo_id,),
        )
        await db.commit()
        await _log(db, repo_id, "ERROR", f"索引失败: {e}")


# ── 内部工具 ──────────────────────────────────────────────────────────────────

def _iter_code_files(root: Path) -> Iterator[tuple[Path, str, str]]:
    """yield (绝对路径, 相对路径, 语言)"""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fname in filenames:
            fpath = Path(dirpath) / fname
            lang = get_language(fpath)
            if lang and fpath.suffix.lower() in settings.supported_ext_set:
                rel = str(fpath.relative_to(root))
                yield fpath, rel, lang


async def _flush(client, col: str, texts: list[str], meta: list[dict]) -> None:
    """embed 一批文本并写入 Qdrant"""
    vectors = await embed(texts)
    points = [
        PointStruct(
            id=str(uuid.uuid4()),
            vector=vec,
            payload=m,
        )
        for vec, m in zip(vectors, meta)
    ]
    await client.upsert(collection_name=col, points=points, wait=False)


async def _update_status(db, repo_id: str, status: int, message: str) -> None:
    await db.execute(
        "UPDATE repos SET indexed=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (status, repo_id),
    )
    await db.commit()


async def _log(db, repo_id: str, level: str, message: str) -> None:
    await db.execute(
        "INSERT INTO index_logs(repo_id, level, message) VALUES (?,?,?)",
        (repo_id, level, message),
    )
    await db.commit()
