from __future__ import annotations

"""
Search Service v2
-----------------
四层检索：
  Layer 1 - 查询理解（意图分类 + 标识符提取）
  Layer 2 - 双通道检索（Dense Qdrant + Sparse BM25）+ RRF 融合
  Layer 3 - Call Graph 增强（1-hop 邻居 + PageRank 加权）
  Layer 4 - 综合 Rerank
"""

import asyncio
import logging
import re
import time
from typing import Optional

import aiosqlite
from qdrant_client.http.models import Filter, FieldCondition, MatchValue

from app.core.embedder import embed_query
from app.core.qdrant_client import get_qdrant, collection_name
from app.core.bm25_index import get_bm25
from app.core.neo4j_client import run_query
from app.models.search import (
    SearchRequest, SearchResponse, SearchResultItem, CallChainItem
)

logger = logging.getLogger(__name__)

# Rerank 权重
W_VECTOR   = 0.35
W_BM25     = 0.25
W_GRAPH    = 0.20
W_SYMBOL   = 0.15
W_STRUCT   = 0.05

RRF_K = 60   # RRF 平滑常数


# ── Layer 1：查询理解 ──────────────────────────────────────────────────────────

_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_IDENT_RE  = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]{2,}")


def _extract_identifiers(raw: str) -> list[str]:
    """从查询文本中提取代码标识符（驼峰/下划线风格的词）"""
    return _IDENT_RE.findall(raw)


def _split_identifier(ident: str) -> list[str]:
    """将驼峰或下划线标识符拆分为小写词元"""
    text = _CAMEL_RE.sub(" ", ident)
    text = re.sub(r"[_]", " ", text)
    return [t.lower() for t in text.split() if len(t) >= 2]


# ── 中文查询检测 ──────────────────────────────────────────────────────────────

_QUERY_INSTRUCTION = (
    "Represent this code search query for retrieving relevant code: "
)

def _is_chinese_dominant(query: str) -> bool:
    """超过 40% 是中文字符 → 中文主导查询"""
    if not query:
        return False
    chinese = sum(1 for c in query if "\u4e00" <= c <= "\u9fff")
    return chinese / len(query) > 0.4


def _needs_llm_expansion(query: str) -> bool:
    """
    纯中文且没有英文标识符时，才触发 Ollama query expansion。
    混合查询（"processPayment 实现"）直接走向量检索，不需要展开。
    """
    return _is_chinese_dominant(query) and not bool(
        re.search(r"[a-zA-Z_][a-zA-Z0-9_]{2,}", query)
    )


async def _expand_query_with_llm(query: str) -> str:
    """
    懒加载 Ollama：把中文查询翻译成等价的英文代码关键词。
    失败时静默降级，不阻断主搜索流程。
    prompt 要求模型只输出关键词，不解释。
    """
    try:
        import httpx
        from app.core.config import settings
        prompt = (
            f"Translate this Chinese code search query to English code keywords only. "
            f"Output only space-separated keywords, method names, and identifiers. "
            f"No explanation. No sentences.\n\nQuery: {query}"
        )
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                f"{settings.OLLAMA_HOST}/api/generate",
                json={
                    "model":  settings.OLLAMA_MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"num_predict": 40, "temperature": 0},
                },
            )
            if resp.status_code == 200:
                keywords = resp.json().get("response", "").strip()
                # 只保留 ASCII 可打印字符，防止模型乱输出
                keywords = " ".join(
                    w for w in keywords.split()
                    if all(ord(c) < 128 for c in w) and len(w) >= 2
                )
                return keywords
    except Exception as e:
        logger.debug("LLM query expansion skipped: %s", e)
    return ""


class QueryIntent:
    def __init__(self, raw: str):
        self.raw = raw
        self.identifiers = _extract_identifiers(raw)
        self.expanded_tokens: list[str] = []
        self.llm_expansion: str = ""   # 由 semantic_search 异步填充

        # 驼峰/下划线标识符拆分
        for ident in self.identifiers:
            self.expanded_tokens.extend(_split_identifier(ident))

        # 意图分类
        low = raw.lower()
        if any(k in low for k in ["调用", "谁调用", "被调用", "caller", "who calls"]):
            self.intent = "call_chain"
        elif any(k in low for k in ["影响", "依赖", "impact", "affect", "depend"]):
            self.intent = "impact"
        elif self.identifiers and len(raw.split()) <= 4:
            self.intent = "symbol_lookup"
        else:
            self.intent = "semantic"

    def embed_query_text(self) -> str:
        """
        向量检索用的文本。
        中文主导查询加 instruction 前缀，激活 bge-m3 的跨语言对齐能力。
        """
        if _is_chinese_dominant(self.raw):
            return f"{_QUERY_INSTRUCTION}{self.raw}"
        return self.raw

    def build_bm25_query(self) -> str:
        """
        BM25 查询：标识符原词 + 拆分词 + LLM 展开词 + 原始查询兜底。
        """
        parts = (
            self.identifiers          # 精确标识符
            + self.expanded_tokens    # 拆分词
            + (self.llm_expansion.split() if self.llm_expansion else [])
            + [self.raw]              # 兜底
        )
        seen: set[str] = set()
        deduped = []
        for p in parts:
            if p not in seen and p:
                seen.add(p)
                deduped.append(p)
        return " ".join(deduped)


# ── Layer 2：双通道检索 + RRF ──────────────────────────────────────────────────

async def _dense_search(
    query: str, repo_id: str, top_k: int,
    language_filter: Optional[str],
) -> tuple[list[tuple[str, float]], dict[str, dict]]:
    """
    返回 ([(chunk_id, score), ...], {chunk_id: payload})。
    payload 随 query_points 一并拿回，避免后续再发一次 retrieve 请求。
    """
    query_vec = await embed_query(query)
    client = get_qdrant()
    col = collection_name(repo_id)

    qfilter = None
    if language_filter:
        qfilter = Filter(must=[FieldCondition(
            key="language", match=MatchValue(value=language_filter)
        )])

    try:
        res = await client.query_points(
            collection_name=col,
            query=query_vec,
            limit=top_k,
            query_filter=qfilter,
            with_payload=True,
        )
        scores   = [(str(h.id), round(h.score, 4)) for h in res.points]
        payloads = {str(h.id): (h.payload or {}) for h in res.points}
        return scores, payloads
    except Exception as e:
        logger.error("Dense search failed for collection %s: %s", col, e)
        return [], {}


def _sparse_search(
    query: str, repo_id: str, top_k: int
) -> list[tuple[str, float]]:
    """返回 [(chunk_id, score), ...] BM25 稀疏搜索"""
    idx = get_bm25(repo_id)
    if idx is None:
        return []
    return idx.query(query, top_k=top_k)


def _rrf_merge(
    dense: list[tuple[str, float]],
    sparse: list[tuple[str, float]],
    k: int = RRF_K,
) -> list[tuple[str, float, float, float]]:
    """
    RRF 融合，返回 [(chunk_id, rrf_score, dense_score, bm25_score), ...]
    """
    dense_rank  = {cid: rank for rank, (cid, _) in enumerate(dense,  start=1)}
    sparse_rank = {cid: rank for rank, (cid, _) in enumerate(sparse, start=1)}
    dense_score  = {cid: s for cid, s in dense}
    sparse_score = {cid: s for cid, s in sparse}

    all_ids = set(dense_rank) | set(sparse_rank)
    results = []
    for cid in all_ids:
        rrf = 0.0
        if cid in dense_rank:
            rrf += 1.0 / (k + dense_rank[cid])
        if cid in sparse_rank:
            rrf += 1.0 / (k + sparse_rank[cid])
        results.append((
            cid,
            round(rrf, 6),
            dense_score.get(cid, 0.0),
            sparse_score.get(cid, 0.0),
        ))

    results.sort(key=lambda x: x[1], reverse=True)
    return results


# ── Layer 3：Call Graph 增强 ───────────────────────────────────────────────────

async def _graph_enhance(
    candidates: list[tuple[str, float, float, float]],
    repo_id: str,
    intent: QueryIntent,
    top_n: int = 100,
) -> dict[str, float]:
    """
    对候选 chunk_id 列表，查 Neo4j 取：
      - pagerank
      - symbol_exact_match（方法名是否命中查询标识符）
      - neighbor_bonus（邻居也在候选池中）
    返回 {chunk_id: graph_score}
    """
    if not candidates:
        return {}

    candidate_ids = [c[0] for c in candidates[:top_n]]

    # 查 symbol_id → chunk_id 映射 + pagerank（从 Neo4j）
    rows = await run_query(
        """
        MATCH (m:Method {repo_id: $repo_id})
        WHERE m.chunk_id IN $chunk_ids
        RETURN m.chunk_id AS chunk_id,
               m.name      AS method_name,
               coalesce(m.pagerank, 0.0) AS pagerank,
               size([(m)<-[:CALLS]-() | 1]) AS in_degree
        """,
        {"repo_id": repo_id, "chunk_ids": candidate_ids},
    )

    candidate_set = set(candidate_ids)
    graph_scores: dict[str, float] = {}

    # 最大 pagerank 用于归一化
    max_pr = max((r["pagerank"] for r in rows), default=1.0) or 1.0

    for row in rows:
        cid        = row["chunk_id"]
        pr_norm    = row["pagerank"] / max_pr
        in_degree  = min(row["in_degree"] / 10.0, 1.0)  # 归一化，上限1

        # symbol 精确命中：方法名是否在查询标识符里
        symbol_hit = 0.0
        name_lower = (row["method_name"] or "").lower()
        for ident in intent.identifiers:
            if ident.lower() in name_lower or name_lower in ident.lower():
                symbol_hit = 1.0
                break
        # 拆分词命中
        if symbol_hit == 0.0:
            for token in intent.expanded_tokens:
                if token in name_lower:
                    symbol_hit = 0.5
                    break

        graph_scores[cid] = round(
            pr_norm   * 0.4 +
            in_degree * 0.3 +
            symbol_hit * 0.3,
            4,
        )

    return graph_scores


# ── Layer 4：综合 Rerank ───────────────────────────────────────────────────────

def _final_rerank(
    candidates: list[tuple[str, float, float, float]],
    graph_scores: dict[str, float],
    payloads: dict[str, dict],
    intent: QueryIntent,
) -> list[tuple[str, float]]:
    """
    综合打分：
      final = vector*0.35 + bm25*0.25 + graph*0.20 + symbol*0.15 + struct*0.05
    """
    scored = []
    for chunk_id, rrf_score, dense_score, bm25_score in candidates:
        payload = payloads.get(chunk_id, {})
        graph_score = graph_scores.get(chunk_id, 0.0)

        # symbol 精确匹配（从 payload 的 symbol 字段）
        symbol_score = 0.0
        sym = (payload.get("symbol") or "").lower()
        cls = (payload.get("class_name") or "").lower()
        for ident in intent.identifiers:
            il = ident.lower()
            if il == sym or il == cls:
                symbol_score = 1.0
                break
            if il in sym or il in cls:
                symbol_score = 0.6

        # struct 匹配（签名 token 重叠）
        text = (payload.get("text") or "").lower()
        struct_score = 0.0
        if intent.expanded_tokens:
            hits = sum(1 for t in intent.expanded_tokens if t in text)
            struct_score = min(hits / len(intent.expanded_tokens), 1.0)

        final = (
            dense_score  * W_VECTOR +
            bm25_score   * W_BM25   +
            graph_score  * W_GRAPH  +
            symbol_score * W_SYMBOL +
            struct_score * W_STRUCT
        )
        scored.append((chunk_id, round(final, 4)))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


# ── 主接口 ────────────────────────────────────────────────────────────────────

async def semantic_search(req: SearchRequest, db: aiosqlite.Connection) -> SearchResponse:
    t0 = time.monotonic()

    intent = QueryIntent(req.query)
    fetch_k = max(req.top_k * 10, 100)

    # ── collection 存在性检查（防止索引未完成时抛 404）─────────────────────────
    col = collection_name(req.repo_id)
    try:
        existing = await get_qdrant().get_collections()
        if col not in {c.name for c in existing.collections}:
            logger.warning("Collection %s not found — repo not indexed yet", col)
            return SearchResponse(results=[], total=0, latency_ms=0,
                                  query=req.query, intent=intent.intent)
    except Exception as e:
        logger.error("Qdrant unreachable: %s", e)
        return SearchResponse(results=[], total=0, latency_ms=0,
                              query=req.query, intent=intent.intent)

    # Layer 2：双通道检索
    # 中文查询：LLM 展开与向量检索并行，不阻塞主路径
    llm_task = None
    if _needs_llm_expansion(req.query):
        llm_task = asyncio.create_task(_expand_query_with_llm(req.query))

    dense_results, dense_payloads = await _dense_search(
        intent.embed_query_text(), req.repo_id, fetch_k, req.language_filter
    )

    if llm_task is not None:
        try:
            intent.llm_expansion = await asyncio.wait_for(llm_task, timeout=5.0)
            if intent.llm_expansion:
                logger.info("LLM expansion: %r → %r", req.query, intent.llm_expansion)
        except asyncio.TimeoutError:
            logger.debug("LLM expansion timeout, skipping")
            intent.llm_expansion = ""

    # BM25 可能触发 pickle 磁盘加载（同步阻塞），放入线程池避免阻塞事件循环
    loop = asyncio.get_running_loop()
    sparse_results = await loop.run_in_executor(
        None, _sparse_search, intent.build_bm25_query(), req.repo_id, fetch_k
    )
    candidates = _rrf_merge(dense_results, sparse_results)

    # dense_payloads 已覆盖向量检索命中的所有候选；
    # 只对 BM25-only 候选（未被 dense 覆盖）补一次 retrieve
    candidate_ids = [c[0] for c in candidates]
    missing_ids   = [cid for cid in candidate_ids if cid not in dense_payloads]
    extra_payloads = await _fetch_payloads(req.repo_id, missing_ids) if missing_ids else {}
    payloads = {**dense_payloads, **extra_payloads}

    # Layer 3：Call Graph 增强
    graph_scores = await _graph_enhance(candidates, req.repo_id, intent)

    # Layer 4：Rerank
    ranked = _final_rerank(candidates, graph_scores, payloads, intent)

    # 去重 + 截断
    deduped = _deduplicate(ranked, payloads)
    final_ids = [cid for cid, _ in deduped[:req.top_k]]

    # 构建结果，附加 1-hop 调用链
    results = await _build_results(final_ids, ranked, payloads, req.repo_id, db)

    latency_ms = int((time.monotonic() - t0) * 1000)
    logger.info("Search [%s] '%s' intent=%s → %d results in %dms",
                req.repo_id, req.query, intent.intent, len(results), latency_ms)

    # 记录查询历史
    top_score = results[0].score if results else 0.0
    await db.execute(
        "INSERT INTO query_history(repo_id, query, intent, result_count, top_score, latency_ms) "
        "VALUES (?,?,?,?,?,?)",
        (req.repo_id, req.query, intent.intent, len(results), top_score, latency_ms),
    )
    await db.commit()

    return SearchResponse(
        results    = results,
        total      = len(results),
        latency_ms = latency_ms,
        query      = req.query,
        intent     = intent.intent,
    )


# ── 辅助函数 ──────────────────────────────────────────────────────────────────

async def _fetch_payloads(repo_id: str, chunk_ids: list[str]) -> dict[str, dict]:
    """从 Qdrant 批量获取 payload"""
    if not chunk_ids:
        return {}
    client = get_qdrant()
    col = collection_name(repo_id)
    try:
        points = await client.retrieve(
            collection_name=col,
            ids=chunk_ids,
            with_payload=True,
        )
        return {str(p.id): p.payload for p in points}
    except Exception as e:
        logger.warning("Fetch payloads failed: %s", e)
        return {}


def _deduplicate(
    ranked: list[tuple[str, float]],
    payloads: dict[str, dict],
) -> list[tuple[str, float]]:
    kept: list[tuple[str, float, int, int, str]] = []
    for chunk_id, score in ranked:
        p = payloads.get(chunk_id, {})
        fp   = p.get("file_path", "")
        ls   = p.get("line_start", 0)
        le   = p.get("line_end", 0)
        overlap = False
        for _, _, kls, kle, kfp in kept:
            if kfp != fp:
                continue
            ov_start = max(kls, ls)
            ov_end   = min(kle, le)
            if ov_end >= ov_start:
                item_len = max(le - ls + 1, 1)
                if (ov_end - ov_start + 1) / item_len > 0.6:
                    overlap = True
                    break
        if not overlap:
            kept.append((chunk_id, score, ls, le, fp))
    return [(cid, sc) for cid, sc, *_ in kept]


async def _build_results(
    final_ids: list[str],
    ranked: list[tuple[str, float]],
    payloads: dict[str, dict],
    repo_id: str,
    db: aiosqlite.Connection,
) -> list[SearchResultItem]:
    score_map = {cid: sc for cid, sc in ranked}
    results = []

    # 批量查 Neo4j 1-hop 调用链
    callers_map, callees_map = await _fetch_call_chains(final_ids, repo_id)

    for chunk_id in final_ids:
        p = payloads.get(chunk_id)
        if not p:
            continue

        # 从 SQLite 查 symbol_id（用于关联）
        symbol_id = p.get("symbol_id", "")

        results.append(SearchResultItem(
            file_path  = p.get("file_path", ""),
            line_start = p.get("line_start", 0),
            line_end   = p.get("line_end", 0),
            snippet    = p.get("text", "")[:600],
            score      = score_map.get(chunk_id, 0.0),
            language   = p.get("language", ""),
            chunk_type = p.get("chunk_type", "method"),
            symbol_id  = symbol_id,
            method_name = p.get("symbol", ""),
            class_name  = p.get("class_name", ""),
            callers    = callers_map.get(chunk_id, []),
            callees    = callees_map.get(chunk_id, []),
        ))

    return results


async def _fetch_call_chains(
    chunk_ids: list[str],
    repo_id: str,
) -> tuple[dict[str, list[CallChainItem]], dict[str, list[CallChainItem]]]:
    """批量查各 chunk 的 1-hop caller 和 callee"""
    if not chunk_ids:
        return {}, {}

    rows = await run_query(
        """
        MATCH (m:Method {repo_id: $repo_id})
        WHERE m.chunk_id IN $chunk_ids
        OPTIONAL MATCH (caller:Method)-[:CALLS]->(m)
        OPTIONAL MATCH (m)-[:CALLS]->(callee:Method)
        RETURN m.chunk_id    AS center_chunk,
               caller.chunk_id AS caller_chunk,
               caller.name     AS caller_name,
               caller.class_name AS caller_class,
               caller.file_path  AS caller_file,
               callee.chunk_id AS callee_chunk,
               callee.name     AS callee_name,
               callee.class_name AS callee_class,
               callee.file_path  AS callee_file
        """,
        {"repo_id": repo_id, "chunk_ids": chunk_ids},
    )

    callers_map: dict[str, list[CallChainItem]] = {cid: [] for cid in chunk_ids}
    callees_map: dict[str, list[CallChainItem]] = {cid: [] for cid in chunk_ids}
    seen_callers: dict[str, set] = {cid: set() for cid in chunk_ids}
    seen_callees: dict[str, set] = {cid: set() for cid in chunk_ids}

    for row in rows:
        center = row.get("center_chunk")
        if not center:
            continue

        if row.get("caller_chunk") and row["caller_chunk"] not in seen_callers.get(center, set()):
            seen_callers[center].add(row["caller_chunk"])
            callers_map[center].append(CallChainItem(
                symbol_id   = row["caller_chunk"],
                method_name = row.get("caller_name") or "",
                class_name  = row.get("caller_class"),
                file_path   = row.get("caller_file") or "",
                direction   = "caller",
                depth       = 1,
            ))

        if row.get("callee_chunk") and row["callee_chunk"] not in seen_callees.get(center, set()):
            seen_callees[center].add(row["callee_chunk"])
            callees_map[center].append(CallChainItem(
                symbol_id   = row["callee_chunk"],
                method_name = row.get("callee_name") or "",
                class_name  = row.get("callee_class"),
                file_path   = row.get("callee_file") or "",
                direction   = "callee",
                depth       = 1,
            ))

    return callers_map, callees_map
