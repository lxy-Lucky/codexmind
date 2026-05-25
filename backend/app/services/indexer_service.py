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
from typing import Iterator, Optional

import aiosqlite
import aiofiles
from qdrant_client.http.models import PointStruct, SparseVector

from app.core.config import settings
from app.core.embedder import embed_with_sparse, sparse_to_indices_values
from app.core.qdrant_client import (
    get_qdrant, ensure_collection, delete_collection, collection_name,
    DENSE_VECTOR_NAME, SPARSE_VECTOR_NAME,
)
from app.core.neo4j_client import run_write, run_write_batch, run_query, run_autocommit
from app.services.repo_service import (
    SKIP_DIRS, SKIP_FILES, SKIP_SUFFIXES, VENDOR_VERSION_RE,
    get_language, detect_encoding, _should_skip_dir,
)
from app.services.parser_service import parse_chunks

logger = logging.getLogger(__name__)

BATCH_SIZE = 32


# ── 主入口 ────────────────────────────────────────────────────────────────────

async def run_index(
    repo_id: str, root_path: str, db: aiosqlite.Connection,
    extra_skip_dirs: frozenset[str] = frozenset(),
) -> None:
    root = Path(root_path)

    # 设置 busy_timeout：等待锁最多 30s，而不是立即报 locked
    await db.execute("PRAGMA busy_timeout = 30000")
    # WAL 模式允许并发读，减少写冲突
    await db.execute("PRAGMA journal_mode = WAL")
    await db.execute("PRAGMA synchronous = NORMAL")

    await _update_status(db, repo_id, 1, "开始扫描文件...")

    # 清理旧数据（先清后建，确保 Qdrant 无残留）
    await _cleanup_old_data(db, repo_id)
    await ensure_collection(repo_id)

    try:
        # ── Pass 1：解析 + 建 symbol 表 + Neo4j 节点 ──────────────────────
        await _update_status(db, repo_id, 1, "Pass 1: 解析符号表...")
        all_chunks, symbol_map = await _pass1_parse(repo_id, root, db, extra_skip_dirs)

        # ── Pass 2：构建 Call Graph ────────────────────────────────────────
        await _update_status(db, repo_id, 1, "Pass 2: 构建调用图...")
        await _pass2_call_graph(repo_id, all_chunks, symbol_map, db)

        # ── Pass 3：embed dense + sparse → Qdrant ─────────────────────────
        await _update_status(db, repo_id, 1, "Pass 3: 向量化索引（dense + sparse）...")
        total_chunks = await _pass3_embed(repo_id, all_chunks, db)

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
    repo_id: str, root: Path, db: aiosqlite.Connection,
    extra_skip_dirs: frozenset[str] = frozenset(),
) -> tuple[list[dict], dict[str, list[str]]]:
    """
    返回：
      all_chunks  - 所有 chunk（含 symbol_id）
      symbol_map  - method_name → [symbol_id, ...]（支持同名方法，Pass2 仅用无歧义映射）
    """
    all_chunks: list[dict] = []
    symbol_rows: list[dict] = []
    neo4j_method_nodes: list[dict] = []
    neo4j_class_nodes: list[dict] = []
    symbol_map: dict[str, list[str]] = {}   # "ClassName.methodName" → [symbol_id]

    for file_path, rel_path, language in _iter_code_files(root, extra_skip_dirs):
        try:
            async with aiofiles.open(file_path, "rb") as f:
                raw = await f.read()
            source = raw.decode(detect_encoding(raw), errors="replace")
        except Exception as e:
            await _log(db, repo_id, "WARNING", f"读取失败 {rel_path}: {e}")
            continue

        chunks = parse_chunks(source, language, rel_path)
        class_ids_in_file: dict[str, str] = {}
        # 切窗后的多个 chunk 共享同一 symbol_id：parser 标 is_primary=True 的是
        # 该方法的代表，is_primary=False 的窗继承前一个 primary 的 symbol_id。
        last_primary_symbol_id: Optional[str] = None

        for chunk in chunks:
            is_primary = chunk.get("is_primary", True)
            if is_primary:
                symbol_id = str(uuid.uuid4())
                last_primary_symbol_id = symbol_id
            else:
                # 继承上一个 primary 的 symbol_id；若意外缺失（不应发生）则降级为独立
                symbol_id = last_primary_symbol_id or str(uuid.uuid4())

            chunk_id  = str(uuid.uuid4())   # 将在 Pass3 写入 Qdrant
            chunk["symbol_id"] = symbol_id
            chunk["chunk_id"]  = chunk_id
            chunk["repo_id"]   = repo_id
            chunk["language"]  = language
            chunk["file_path"] = rel_path

            method_name = chunk.get("symbol", "")
            class_name  = chunk.get("class_name", "")
            all_chunks.append(chunk)

            # 以下只对 primary chunk 生效：避免同名方法歧义 + 一个长方法占多个 top-k 名额
            if not is_primary:
                continue

            chunk_type = chunk.get("chunk_type", "method")

            # symbol_map 注册（支持同名方法：list 追加，Pass2 只取无歧义条目）
            # SQL chunk 不进 symbol_map：service 调用 userMapper.foo 时只应解析到
            # Java interface 方法；XML SQL 通过 IMPLEMENTS 边接入，避免 service
            # 直接连到 SQL（语义不准，且制造无意义重边）。
            if method_name and chunk_type != "sql":
                symbol_map.setdefault(method_name, []).append(symbol_id)
                if class_name:
                    symbol_map.setdefault(f"{class_name}.{method_name}", []).append(symbol_id)

            # method_line_* 是方法的完整行范围（含切窗时的所有窗口）。
            # 对未切窗的 chunk 等价于自己的 line_*。
            # 关键作用：让 /api/repo/{id}/symbol/at（光标 → 方法）
            # 能在长方法的任何一行命中 primary chunk，否则点击依赖图按钮会失灵。
            m_ls = chunk.get("method_line_start", chunk["line_start"])
            m_le = chunk.get("method_line_end",   chunk["line_end"])

            # SQLite symbol 行
            symbol_rows.append({
                "id":          symbol_id,
                "repo_id":     repo_id,
                "file_path":   rel_path,
                "class_name":  class_name,
                "method_name": method_name,
                "signature":   chunk.get("signature", "")[:500],
                "line_start":  m_ls,
                "line_end":    m_le,
                "chunk_id":    chunk_id,
            })

            # Neo4j Method 节点（chunk_type 用于前端区分 Java 方法 vs XML SQL）
            if method_name:
                neo4j_method_nodes.append({
                    "id":          symbol_id,
                    "repo_id":     repo_id,
                    "name":        method_name,
                    "class_name":  class_name,
                    "file_path":   rel_path,
                    "line_start":  m_ls,
                    "line_end":    m_le,
                    "signature":   chunk.get("signature", "")[:300],
                    "chunk_id":    chunk_id,
                    "chunk_type":  chunk_type,
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

    # 批量写 SQLite symbols
    await _bulk_insert_symbols(db, symbol_rows)

    # 批量写 Neo4j 节点
    await _neo4j_create_method_nodes(neo4j_method_nodes)
    await _neo4j_create_class_nodes(neo4j_class_nodes)
    await _neo4j_link_methods_to_classes(repo_id)
    # MyBatis：Java interface method → XML SQL 的 IMPLEMENTS 边
    # 按 (class_name, name) 匹配。前提是 mapper namespace 简名与 Java interface 类名一致
    # （ruoyi / 大多数 MyBatis 工程都遵循此约定）。
    impl_count = await _neo4j_link_interface_to_sql(repo_id)

    logger.info("[%s] Pass1 done: %d chunks, %d symbols, %d IMPLEMENTS edges",
                repo_id, len(all_chunks), len(symbol_map), impl_count)
    return all_chunks, symbol_map


# ── Pass 2：调用边 + PageRank ──────────────────────────────────────────────────

def _resolve_callees(
    symbol_map: dict[str, list[str]],
    call_ref: str,
    caller_id: str,
) -> list[str]:
    """
    返回 0..N 个 callee symbol_id。
    策略：
      - 精确匹配 call_ref（含 "."）：所有候选都连边。
        典型场景是方法重载（`StringUtils.isEmpty(Object)` 和
        `StringUtils.isEmpty(String)` 共享一个 key），不连等于"调用图全空"。
        静态分析无法区分重载，宁可多连不可漏连。
      - 精确匹配无结果且 call_ref 含 "."：用方法名做 fallback，仅唯一才连。
        这一支处理的是 receiver 是变量名（不是类名）的情形——若方法名在
        全 repo 里也歧义（如 `isEmpty`），直接放弃，避免连出一堆假边。
      - call_ref 不含 "."（无 receiver，可能是静态导入）：唯一才连。
    """
    candidates = symbol_map.get(call_ref, [])
    if candidates:
        return [c for c in candidates if c != caller_id]

    if "." in call_ref:
        method_only = call_ref.rsplit(".", 1)[-1]
        by_name = symbol_map.get(method_only, [])
        if len(by_name) == 1 and by_name[0] != caller_id:
            return [by_name[0]]
    return []


async def _pass2_call_graph(
    repo_id: str,
    all_chunks: list[dict],
    symbol_map: dict[str, list[str]],
    db: aiosqlite.Connection,
) -> None:
    edges: list[dict] = []

    # 只遍历 primary chunk：secondary 切窗共享 symbol_id 和 calls 列表，
    # 再遍历会做完全重复的解析（unique_edges 会去重，但是浪费 CPU）。
    for chunk in all_chunks:
        if not chunk.get("is_primary", True):
            continue
        caller_id = chunk.get("symbol_id")
        if not caller_id:
            continue
        for call_ref in chunk.get("calls", []):
            callee_ids = _resolve_callees(symbol_map, call_ref, caller_id)
            if not callee_ids:
                continue
            # 多 callee（重载）时按数量平分 confidence，保留"模糊调用"语义
            conf_base = 1.0 if "." in call_ref else 0.7
            per_conf  = conf_base / len(callee_ids)
            for callee_id in callee_ids:
                edges.append({
                    "caller_id":  caller_id,
                    "callee_id":  callee_id,
                    "callee_raw": call_ref,
                    "confidence": per_conf if len(callee_ids) > 1 else conf_base,
                })

    # 按 (caller, callee) 去重并统计真实调用次数
    edge_map: dict[tuple, dict] = {}
    for e in edges:
        key = (e["caller_id"], e["callee_id"])
        if key not in edge_map:
            e["call_count"] = 1
            edge_map[key] = e
        else:
            edge_map[key]["call_count"] += 1
    if edge_map:
        await run_write_batch(
            """
            UNWIND $rows AS row
            MATCH (caller:Method {id: row.caller_id})
            MATCH (callee:Method {id: row.callee_id})
            MERGE (caller)-[r:CALLS]->(callee)
            ON CREATE SET r.confidence = row.confidence, r.call_count = row.call_count
            ON MATCH  SET r.call_count = r.call_count + row.call_count
            """,
            list(edge_map.values()),
        )

    # 计算 PageRank（Neo4j GDS）
    await _compute_pagerank(repo_id)

    logger.info("[%s] Pass2 done: %d call edges", repo_id, len(edge_map))


async def _compute_pagerank(repo_id: str) -> None:
    """用 Neo4j GDS 计算 PageRank 并写回节点属性。用 try/finally 确保投影始终被清理，
    防止重复索引时因投影残留而报 'graph already exists' 错误。"""
    graph_name = f"cg_{repo_id[:8]}"
    projected = False
    try:
        await run_write(
            """
            MATCH (source:Method {repo_id: $repo_id})
            OPTIONAL MATCH (source)-[:CALLS]->(target:Method {repo_id: $repo_id})
            WITH gds.graph.project($graph_name, source, target) AS g
            RETURN g.graphName AS graphName
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
                await run_write("CALL gds.graph.drop($graph_name) YIELD graphName", {"graph_name": graph_name})
            except Exception:
                pass


# ── Pass 3：embed dense + sparse → Qdrant ─────────────────────────────────────

async def _pass3_embed(
    repo_id: str,
    all_chunks: list[dict],
    db: aiosqlite.Connection,
) -> int:
    """
    用 bge-m3 一次前向同时产出 dense 向量（语义）和 sparse 向量（多语言学习稀疏，
    替代 BM25）。两路向量都写进 Qdrant 同一 collection 的 named vectors 槽位。
    """
    client = get_qdrant()
    col = collection_name(repo_id)

    batch_texts: list[str] = []
    batch_meta:  list[dict] = []
    batch_ids:   list[str] = []
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
            "text":       chunk.get("raw_code", chunk["text"][:3000]),
        })
        batch_ids.append(chunk["chunk_id"])
        total += 1

        if len(batch_texts) >= BATCH_SIZE:
            await _flush_qdrant(client, col, batch_texts, batch_meta, batch_ids)
            batch_texts, batch_meta, batch_ids = [], [], []
            await _log(db, repo_id, "INFO", f"已 embed {total} chunks")

    if batch_texts:
        await _flush_qdrant(client, col, batch_texts, batch_meta, batch_ids)

    logger.info("[%s] Pass3 done: %d chunks embedded (dense + sparse)", repo_id, total)
    return total


async def _flush_qdrant(client, col: str, texts: list[str],
                        meta: list[dict], chunk_ids: list[str]) -> None:
    dense_vecs, sparse_vecs = await embed_with_sparse(texts)
    points = []
    for cid, dvec, svec, m in zip(chunk_ids, dense_vecs, sparse_vecs, meta):
        indices, values = sparse_to_indices_values(svec)
        points.append(PointStruct(
            id=cid,
            vector={
                DENSE_VECTOR_NAME:  dvec,
                SPARSE_VECTOR_NAME: SparseVector(indices=indices, values=values),
            },
            payload=m,
        ))
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
            m.chunk_id   = row.chunk_id,
            m.chunk_type = coalesce(row.chunk_type, 'method')
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


async def _neo4j_link_interface_to_sql(repo_id: str) -> int:
    """
    建 Java interface method → XML SQL 的 [:IMPLEMENTS] 边。
    匹配规则：(class_name, name) 相同；Java 一侧 chunk_type='method' 且文件以
    .java 结尾，XML 一侧 chunk_type='sql'。
    返回新建边数（用于日志）。
    """
    rows = await run_query(
        """
        MATCH (sql:Method {repo_id: $repo_id, chunk_type: 'sql'})
        MATCH (java:Method {repo_id: $repo_id})
        WHERE java.chunk_type = 'method'
          AND java.class_name = sql.class_name
          AND java.name       = sql.name
          AND java.file_path ENDS WITH '.java'
        MERGE (java)-[r:IMPLEMENTS]->(sql)
        RETURN count(r) AS cnt
        """,
        {"repo_id": repo_id},
    )
    return rows[0]["cnt"] if rows else 0


async def _neo4j_link_methods_to_classes(repo_id: str) -> None:
    """
    为同 class_name 的 Method 和 Class 建 [:BELONGS_TO] 关系。
    使用 CALL { } IN TRANSACTIONS OF 300 ROWS（Neo4j 4.4+）分批提交，
    避免大库单事务超时断连。

    注意：CALL {} IN TRANSACTIONS 只能在隐式事务（auto-commit）中运行，
    必须用 run_autocommit 而不是 run_write（后者走 execute_write 显式事务）。
    """
    try:
        await run_autocommit(
            """
            MATCH (m:Method {repo_id: $repo_id})
            WHERE m.class_name IS NOT NULL AND m.class_name <> ''
            MATCH (c:Class {repo_id: $repo_id, name: m.class_name})
            CALL {
              WITH m, c
              MERGE (m)-[:BELONGS_TO]->(c)
            } IN TRANSACTIONS OF 300 ROWS
            """,
            {"repo_id": repo_id},
        )
    except Exception as e:
        # 该版本 Neo4j 不支持 IN TRANSACTIONS，降级为逐批手动处理
        logger.warning("IN TRANSACTIONS not supported, falling back: %s", e)
        rows = await run_query(
            "MATCH (c:Class {repo_id: $repo_id}) RETURN c.name AS name",
            {"repo_id": repo_id},
        )
        for r in rows:
            cname = r.get("name")
            if not cname:
                continue
            await run_write(
                """
                MATCH (m:Method {repo_id: $repo_id, class_name: $cname})
                MATCH (c:Class  {repo_id: $repo_id, name: $cname})
                MERGE (m)-[:BELONGS_TO]->(c)
                """,
                {"repo_id": repo_id, "cname": cname},
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

def _iter_code_files(
    root: Path,
    extra_skip_dirs: frozenset[str] = frozenset(),
) -> Iterator[tuple[Path, str, str]]:
    for dirpath, dirnames, filenames in os.walk(root):
        cur_rel = Path(dirpath).relative_to(root)
        dirnames[:] = [
            d for d in dirnames
            if d not in SKIP_DIRS
            and not _should_skip_dir(d, str(cur_rel / d), extra_skip_dirs)
        ]
        for fname in filenames:
            fname_lower = fname.lower()

            # ① 精确文件名跳过（pom.xml、jquery.js 等）
            if fname_lower in SKIP_FILES:
                continue

            # ② 压缩 / 打包产物（.min.js / .bundle.js 等）
            if any(fname_lower.endswith(s) for s in SKIP_SUFFIXES):
                continue

            # ③ 带版本号的第三方库（jquery-3.6.0.js / bootstrap.5.3.2.js 等）
            if VENDOR_VERSION_RE.match(fname_lower):
                continue

            fpath = Path(dirpath) / fname
            lang = get_language(fpath)
            if not (lang and fpath.suffix.lower() in settings.supported_ext_set):
                continue

            # ④ XML 白名单：只索引 MyBatis Mapper，跳过其他 XML 配置
            if fpath.suffix.lower() == ".xml" and not fname_lower.endswith("mapper.xml"):
                continue

            # ⑤ 跳过超大文件（>500KB）：通常是打包产物或自动生成代码
            try:
                if fpath.stat().st_size > 500_000:
                    logger.debug("Skip large file (%d bytes): %s", fpath.stat().st_size, fpath)
                    continue
            except OSError:
                continue

            rel = str(fpath.relative_to(root))
            yield fpath, rel, lang


async def _cleanup_old_data(db: aiosqlite.Connection, repo_id: str) -> None:
    """重新索引前清理旧数据"""
    await db.execute("DELETE FROM symbols WHERE repo_id=?", (repo_id,))
    await db.commit()
    # 删除 Qdrant collection（含所有旧向量点）；首次索引时 collection 可能不存在，忽略异常
    try:
        await delete_collection(repo_id)
        logger.info("[%s] Qdrant collection dropped", repo_id)
    except Exception:
        pass
    # 分批删除 Neo4j 节点。
    # 必须带 Label（:Method / :Class），不带 Label 会全图扫描 → MemoryPoolOutOfMemory
    for label in ("Method", "Class", "File"):
        while True:
            rows = await run_query(
                f"MATCH (n:{label} {{repo_id: $repo_id}}) RETURN count(n) AS cnt",
                {"repo_id": repo_id},
            )
            if not rows or rows[0].get("cnt", 0) == 0:
                break
            await run_write(
                f"MATCH (n:{label} {{repo_id: $repo_id}}) WITH n LIMIT 200 DETACH DELETE n",
                {"repo_id": repo_id},
            )
            await asyncio.sleep(0)   # 让出事件循环


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
    立即 commit 写入日志。
    旧实现对 INFO 不 commit、等下次写一起落盘，会导致前端轮询 /index/logs
    长时间看不到进度。WAL 模式下小事务的开销已经很低，不再延迟。
    """
    await db.execute(
        "INSERT INTO index_logs(repo_id, level, message) VALUES (?,?,?)",
        (repo_id, level, message),
    )
    await db.commit()
