from __future__ import annotations

"""
Indexer Service v2
------------------
三遍扫描：
  Pass 1 - 解析所有文件 → chunk + symbol 表（SQLite）+ Neo4j 节点
  Pass 2 - 解析调用关系 → Neo4j [:CALLS] 边 + PageRank
  Pass 3 - embed 结构化 chunk → Qdrant；构建 BM25 索引
"""

import asyncio
import logging
import os
import uuid
from pathlib import Path
from typing import Iterator

import aiosqlite
import aiofiles
from qdrant_client.http.models import PointStruct

from app.core.config import settings
from app.core.embedder import embed
from app.core.qdrant_client import get_qdrant, ensure_collection, collection_name
from app.core.neo4j_client import run_write, run_write_batch, run_query
from app.core.bm25_index import BM25Index, save_bm25, invalidate_bm25
from app.services.repo_service import SKIP_DIRS, get_language
from app.services.parser_service import parse_chunks

logger = logging.getLogger(__name__)

BATCH_SIZE = 32


# ── 主入口 ────────────────────────────────────────────────────────────────────

async def run_index(repo_id: str, root_path: str, db: aiosqlite.Connection) -> None:
    root = Path(root_path)

    # 设置 busy_timeout：等待锁最多 30s，而不是立即报 locked
    await db.execute("PRAGMA busy_timeout = 30000")
    # WAL 模式允许并发读，减少写冲突
    await db.execute("PRAGMA journal_mode = WAL")
    await db.execute("PRAGMA synchronous = NORMAL")

    await _update_status(db, repo_id, 1, "开始扫描文件...")
    await ensure_collection(repo_id)

    # 清理旧数据
    await _cleanup_old_data(db, repo_id)
    invalidate_bm25(repo_id)

    try:
        # ── Pass 1：解析 + 建 symbol 表 + Neo4j 节点 ──────────────────────
        await _update_status(db, repo_id, 1, "Pass 1: 解析符号表...")
        all_chunks, symbol_map = await _pass1_parse(repo_id, root, db)

        # ── Pass 2：构建 Call Graph ────────────────────────────────────────
        await _update_status(db, repo_id, 1, "Pass 2: 构建调用图...")
        await _pass2_call_graph(repo_id, all_chunks, symbol_map, db)

        # ── Pass 3：embed + BM25 ──────────────────────────────────────────
        await _update_status(db, repo_id, 1, "Pass 3: 向量化索引...")
        total_chunks = await _pass3_embed_bm25(repo_id, all_chunks, db)

        # 完成
        symbol_count = len(symbol_map)
        edge_count   = await _count_edges(repo_id)
        await db.execute(
            """UPDATE repos
               SET indexed=2, chunk_count=?, symbol_count=?, edge_count=?,
                   updated_at=CURRENT_TIMESTAMP
               WHERE id=?""",
            (total_chunks, symbol_count, edge_count, repo_id),
        )
        await db.commit()
        await _log(db, repo_id, "INFO",
                   f"索引完成：{total_chunks} chunks，{symbol_count} 符号，{edge_count} 调用边")
        logger.info("[%s] Index done: %d chunks, %d symbols, %d edges",
                    repo_id, total_chunks, symbol_count, edge_count)

    except Exception as e:
        logger.exception("[%s] Index failed: %s", repo_id, e)
        await db.execute(
            "UPDATE repos SET indexed=3, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (repo_id,),
        )
        await db.commit()
        await _log(db, repo_id, "ERROR", f"索引失败: {e}")


# ── Pass 1：解析 + 符号表 + Neo4j 节点 ────────────────────────────────────────

async def _pass1_parse(
    repo_id: str, root: Path, db: aiosqlite.Connection
) -> tuple[list[dict], dict[str, str]]:
    """
    返回：
      all_chunks  - 所有 chunk（含 symbol_id）
      symbol_map  - method_name → symbol_id（用于 Pass2 调用解析）
    """
    all_chunks: list[dict] = []
    symbol_rows: list[dict] = []
    neo4j_method_nodes: list[dict] = []
    neo4j_class_nodes: list[dict] = []
    symbol_map: dict[str, str] = {}   # "ClassName.methodName" → symbol_id

    for file_path, rel_path, language in _iter_code_files(root):
        try:
            async with aiofiles.open(file_path, encoding="utf-8", errors="replace") as f:
                source = await f.read()
        except Exception as e:
            await _log(db, repo_id, "WARNING", f"读取失败 {rel_path}: {e}")
            continue

        chunks = parse_chunks(source, language, rel_path)
        class_ids_in_file: dict[str, str] = {}

        for chunk in chunks:
            symbol_id = str(uuid.uuid4())
            chunk_id  = str(uuid.uuid4())   # 将在 Pass3 写入 Qdrant
            chunk["symbol_id"] = symbol_id
            chunk["chunk_id"]  = chunk_id
            chunk["repo_id"]   = repo_id
            chunk["language"]  = language
            chunk["file_path"] = rel_path

            method_name = chunk.get("symbol", "")
            class_name  = chunk.get("class_name", "")

            # symbol_map 注册（支持 ClassName.method 和 method 两种 key）
            if method_name:
                symbol_map[method_name] = symbol_id
                if class_name:
                    symbol_map[f"{class_name}.{method_name}"] = symbol_id

            # SQLite symbol 行
            symbol_rows.append({
                "id":          symbol_id,
                "repo_id":     repo_id,
                "file_path":   rel_path,
                "class_name":  class_name,
                "method_name": method_name,
                "signature":   chunk.get("signature", "")[:500],
                "line_start":  chunk["line_start"],
                "line_end":    chunk["line_end"],
                "chunk_id":    chunk_id,
            })

            # Neo4j Method 节点
            if method_name:
                neo4j_method_nodes.append({
                    "id":          symbol_id,
                    "repo_id":     repo_id,
                    "name":        method_name,
                    "class_name":  class_name,
                    "file_path":   rel_path,
                    "line_start":  chunk["line_start"],
                    "line_end":    chunk["line_end"],
                    "signature":   chunk.get("signature", "")[:300],
                    "chunk_id":    chunk_id,
                })

            # Neo4j Class 节点（去重）
            if class_name and class_name not in class_ids_in_file:
                class_id = str(uuid.uuid4())
                class_ids_in_file[class_name] = class_id
                neo4j_class_nodes.append({
                    "id":       class_id,
                    "repo_id":  repo_id,
                    "name":     class_name,
                    "file_path": rel_path,
                })

            all_chunks.append(chunk)

    # 批量写 SQLite symbols
    await _bulk_insert_symbols(db, symbol_rows)

    # 批量写 Neo4j 节点
    await _neo4j_create_method_nodes(neo4j_method_nodes)
    await _neo4j_create_class_nodes(neo4j_class_nodes)
    await _neo4j_link_methods_to_classes(repo_id)

    logger.info("[%s] Pass1 done: %d chunks, %d symbols", repo_id, len(all_chunks), len(symbol_map))
    return all_chunks, symbol_map


# ── Pass 2：调用边 + PageRank ──────────────────────────────────────────────────

async def _pass2_call_graph(
    repo_id: str,
    all_chunks: list[dict],
    symbol_map: dict[str, str],
    db: aiosqlite.Connection,
) -> None:
    edges: list[dict] = []

    for chunk in all_chunks:
        caller_id = chunk.get("symbol_id")
        if not caller_id:
            continue
        for call_ref in chunk.get("calls", []):
            # 尝试精确匹配 ClassName.method，再尝试 method
            callee_id = symbol_map.get(call_ref) or symbol_map.get(call_ref.split(".")[-1])
            if callee_id and callee_id != caller_id:
                edges.append({
                    "caller_id":  caller_id,
                    "callee_id":  callee_id,
                    "callee_raw": call_ref,
                    "confidence": 1.0 if "." in call_ref else 0.7,
                })

    # 批量写 Neo4j [:CALLS] 边（去重）
    unique_edges = {(e["caller_id"], e["callee_id"]): e for e in edges}
    if unique_edges:
        await run_write_batch(
            """
            UNWIND $rows AS row
            MATCH (caller:Method {id: row.caller_id})
            MATCH (callee:Method {id: row.callee_id})
            MERGE (caller)-[r:CALLS]->(callee)
            ON CREATE SET r.confidence = row.confidence, r.call_count = 1
            ON MATCH  SET r.call_count = r.call_count + 1
            """,
            list(unique_edges.values()),
        )

    # 计算 PageRank（Neo4j GDS）
    await _compute_pagerank(repo_id)

    logger.info("[%s] Pass2 done: %d call edges", repo_id, len(unique_edges))


async def _compute_pagerank(repo_id: str) -> None:
    """用 Neo4j GDS 计算 PageRank 并写回节点属性。用 try/finally 确保投影始终被清理，
    防止重复索引时因投影残留而报 'graph already exists' 错误。"""
    graph_name = f"cg_{repo_id[:8]}"
    projected = False
    try:
        await run_write(
            """
            CALL gds.graph.project.cypher(
              $graph_name,
              'MATCH (m:Method {repo_id: $repo_id}) RETURN id(m) AS id',
              'MATCH (a:Method {repo_id: $repo_id})-[:CALLS]->(b:Method {repo_id: $repo_id})
               RETURN id(a) AS source, id(b) AS target',
              {parameters: {repo_id: $repo_id}}
            )
            """,
            {"graph_name": graph_name, "repo_id": repo_id},
        )
        projected = True
        await run_write(
            """
            CALL gds.pageRank.write($graph_name, {
              maxIterations: 20,
              dampingFactor: 0.85,
              writeProperty: 'pagerank'
            })
            """,
            {"graph_name": graph_name},
        )
        logger.info("[%s] PageRank computed", repo_id)
    except Exception as e:
        logger.warning("[%s] PageRank failed (GDS not available?): %s", repo_id, e)
    finally:
        if projected:
            try:
                await run_write("CALL gds.graph.drop($graph_name)", {"graph_name": graph_name})
            except Exception:
                pass


# ── Pass 3：embed + Qdrant + BM25 ─────────────────────────────────────────────

async def _pass3_embed_bm25(
    repo_id: str,
    all_chunks: list[dict],
    db: aiosqlite.Connection,
) -> int:
    client = get_qdrant()
    col = collection_name(repo_id)

    batch_texts: list[str] = []
    batch_meta:  list[dict] = []
    bm25_docs:   list[dict] = []
    total = 0

    for chunk in all_chunks:
        batch_texts.append(chunk["text"])
        batch_meta.append({
            "repo_id":    repo_id,
            "file_path":  chunk["file_path"],
            "line_start": chunk["line_start"],
            "line_end":   chunk["line_end"],
            "language":   chunk["language"],
            "chunk_type": chunk["chunk_type"],
            "symbol":     chunk.get("symbol", ""),
            "class_name": chunk.get("class_name", ""),
            "symbol_id":  chunk.get("symbol_id", ""),
            "text":       chunk.get("raw_code", chunk["text"][:800]),
        })
        bm25_docs.append({
            "chunk_id": chunk["chunk_id"],
            "text":     chunk["text"],   # 结构化文本也进 BM25
        })
        total += 1

        if len(batch_texts) >= BATCH_SIZE:
            await _flush_qdrant(client, col, batch_texts, batch_meta,
                                [c["chunk_id"] for c in all_chunks[total - BATCH_SIZE:total]])
            batch_texts, batch_meta = [], []
            await _log(db, repo_id, "INFO", f"已 embed {total} chunks")

    if batch_texts:
        start = total - len(batch_texts)
        await _flush_qdrant(client, col, batch_texts, batch_meta,
                            [c["chunk_id"] for c in all_chunks[start:total]])

    # 构建 BM25
    bm25_idx = BM25Index()
    bm25_idx.build(bm25_docs)
    idx_path = save_bm25(repo_id, bm25_idx)
    await db.execute(
        """INSERT OR REPLACE INTO bm25_meta(repo_id, index_path, doc_count)
           VALUES (?, ?, ?)""",
        (repo_id, str(idx_path), len(bm25_docs)),
    )
    await db.commit()

    logger.info("[%s] Pass3 done: %d chunks embedded, BM25 built", repo_id, total)
    return total


async def _flush_qdrant(client, col: str, texts: list[str],
                        meta: list[dict], chunk_ids: list[str]) -> None:
    vectors = await embed(texts)
    points = [
        PointStruct(id=cid, vector=vec, payload=m)
        for cid, vec, m in zip(chunk_ids, vectors, meta)
    ]
    await client.upsert(collection_name=col, points=points, wait=False)


# ── Neo4j 批量操作 ─────────────────────────────────────────────────────────────

async def _neo4j_create_method_nodes(nodes: list[dict]) -> None:
    if not nodes:
        return
    await run_write_batch(
        """
        UNWIND $rows AS row
        MERGE (m:Method {id: row.id})
        SET m.repo_id    = row.repo_id,
            m.name       = row.name,
            m.class_name = row.class_name,
            m.file_path  = row.file_path,
            m.line_start = row.line_start,
            m.line_end   = row.line_end,
            m.signature  = row.signature,
            m.chunk_id   = row.chunk_id
        """,
        nodes,
    )


async def _neo4j_create_class_nodes(nodes: list[dict]) -> None:
    if not nodes:
        return
    await run_write_batch(
        """
        UNWIND $rows AS row
        MERGE (c:Class {id: row.id})
        SET c.repo_id   = row.repo_id,
            c.name      = row.name,
            c.file_path = row.file_path
        """,
        nodes,
    )


async def _neo4j_link_methods_to_classes(repo_id: str) -> None:
    """为同 class_name 的 Method 和 Class 建 [:BELONGS_TO] 关系"""
    await run_write(
        """
        MATCH (m:Method {repo_id: $repo_id})
        WHERE m.class_name IS NOT NULL AND m.class_name <> ''
        MATCH (c:Class {repo_id: $repo_id, name: m.class_name})
        MERGE (m)-[:BELONGS_TO]->(c)
        """,
        {"repo_id": repo_id},
    )


# ── SQLite 批量写 ──────────────────────────────────────────────────────────────

async def _bulk_insert_symbols(db: aiosqlite.Connection, rows: list[dict]) -> None:
    """分批写入，每批 500 行独立 commit，避免超长事务持锁"""
    if not rows:
        return
    CHUNK = 500
    sql = """INSERT OR REPLACE INTO symbols
             (id, repo_id, file_path, class_name, method_name, signature,
              line_start, line_end, chunk_id)
             VALUES (:id, :repo_id, :file_path, :class_name, :method_name,
                     :signature, :line_start, :line_end, :chunk_id)"""
    for i in range(0, len(rows), CHUNK):
        await db.executemany(sql, rows[i: i + CHUNK])
        await db.commit()
        # 短暂让出事件循环，避免长时间霸占写锁
        await asyncio.sleep(0)


# ── 工具函数 ───────────────────────────────────────────────────────────────────

def _iter_code_files(root: Path) -> Iterator[tuple[Path, str, str]]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fname in filenames:
            fpath = Path(dirpath) / fname
            lang = get_language(fpath)
            if lang and fpath.suffix.lower() in settings.supported_ext_set:
                rel = str(fpath.relative_to(root))
                yield fpath, rel, lang


async def _cleanup_old_data(db: aiosqlite.Connection, repo_id: str) -> None:
    """重新索引前清理旧数据"""
    await db.execute("DELETE FROM symbols WHERE repo_id=?", (repo_id,))
    await db.execute("DELETE FROM bm25_meta WHERE repo_id=?", (repo_id,))
    await db.commit()
    # 清理 Neo4j
    await run_write(
        "MATCH (n {repo_id: $repo_id}) DETACH DELETE n",
        {"repo_id": repo_id},
    )


async def _count_edges(repo_id: str) -> int:
    rows = await run_query(
        """
        MATCH (:Method {repo_id: $repo_id})-[r:CALLS]->(:Method {repo_id: $repo_id})
        RETURN count(r) AS cnt
        """,
        {"repo_id": repo_id},
    )
    return rows[0]["cnt"] if rows else 0


async def _update_status(db, repo_id: str, status: int, message: str) -> None:
    """commit 失败时最多重试 3 次，每次等 500ms"""
    await db.execute(
        "UPDATE repos SET indexed=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (status, repo_id),
    )
    for attempt in range(3):
        try:
            await db.commit()
            return
        except Exception as e:
            if attempt < 2:
                logger.warning("DB commit retry %d: %s", attempt + 1, e)
                await asyncio.sleep(0.5)
            else:
                logger.error("DB commit failed after 3 retries: %s", e)


async def _log(db, repo_id: str, level: str, message: str) -> None:
    """
    只插入日志，不立即 commit（由调用方控制 commit 时机）。
    WARNING/ERROR 级别立即 commit，INFO 随下次批量写一起落盘，
    减少锁竞争。
    """
    await db.execute(
        "INSERT INTO index_logs(repo_id, level, message) VALUES (?,?,?)",
        (repo_id, level, message),
    )
    if level in ("WARNING", "ERROR"):
        await db.commit()
