from __future__ import annotations

"""
Search Service v3
-----------------
五层检索：
  Layer 1 - 查询理解（语言检测 + 意图分类 + 标识符提取 + HyDE 假想代码）
  Layer 2 - 双通道检索（bge-m3 Dense + bge-m3 Sparse，都走 Qdrant）+ RRF 融合
  Layer 3 - Call Graph 增强（1-hop 邻居 + PageRank 加权）
  Layer 4 - bge-reranker-v2-m3 交叉编码器重排
  Layer 5 - 综合打分 + symbol 级去重

变更（v2 → v3）：
  - BM25 退役：sparse 改用 bge-m3 lexical_weights，多语言（尤其日/中）显著提升
  - 加 HyDE：自然语言 query → LLM 生成假想代码 → 用假想代码 embed 做 dense 检索
  - 加 cross-encoder reranker：top-50 候选 pair-wise 重排
"""

import asyncio
import logging
import math
import re
import time
from typing import Optional

import aiosqlite
from qdrant_client.http.models import (
    Filter, FieldCondition, MatchValue, SparseVector,
)

from app.core.config import settings
from app.core.embedder import encode_query, embed_query, sparse_to_indices_values
from app.core.qdrant_client import (
    get_qdrant, collection_name,
    DENSE_VECTOR_NAME, SPARSE_VECTOR_NAME,
)
from app.core.neo4j_client import run_query
from app.core import reranker as reranker_mod
from app.models.search import (
    SearchRequest, SearchResponse, SearchResultItem, CallChainItem
)

logger = logging.getLogger(__name__)

# Rerank 权重（v3：cross-encoder reranker 为主信号；不可用时回退）
W_RERANKER = 0.60
W_RRF      = 0.20
W_GRAPH    = 0.10
W_SYMBOL   = 0.08
W_STRUCT   = 0.02

W_RRF_FALLBACK    = 0.55
W_GRAPH_FALLBACK  = 0.20
W_SYMBOL_FALLBACK = 0.20
W_STRUCT_FALLBACK = 0.05

RRF_K = 60   # RRF 平滑常数


# ── Layer 1：查询理解 ──────────────────────────────────────────────────────────

_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
# 标识符门槛：2 字符。允许 ASCII 标识符以及 CJK 字符（日文假名/汉字、中文）
# 这样日语 query "ユーザー認証" / 中文 query "用户认证" 也能被提取为搜索 token
_IDENT_RE = re.compile(
    "["
    "a-zA-Z_"
    "぀-ゟ"    # 平假名
    "゠-ヿ"    # 片假名
    "一-鿿"    # CJK 统一汉字（中日共用）
    "]"
    "["
    "a-zA-Z0-9_"
    "぀-ゟ"
    "゠-ヿ"
    "一-鿿"
    "]+"
)

# CJK 字符判定，用于决定是否走 CJK 友好的拆分策略
_CJK_RE = re.compile("[぀-ゟ゠-ヿ一-鿿]")


def _extract_identifiers(raw: str) -> list[str]:
    """从查询文本中提取代码 / 自然语言关键词（含 CJK）"""
    return _IDENT_RE.findall(raw)


def _split_identifier(ident: str) -> list[str]:
    """
    驼峰/下划线拆分。CJK 词不拆分（拆了反而破坏语义），原样保留。
    """
    if _CJK_RE.search(ident):
        return [ident]
    text = _CAMEL_RE.sub(" ", ident)
    text = re.sub(r"[_]", " ", text)
    return [t.lower() for t in text.split() if len(t) >= 2]


# ── 语言检测 ──────────────────────────────────────────────────────────────────
# bge-m3 dense 多语言对齐很强，不需要 instruction prefix。
# 这里检测语言只用来：
#   1. 给 LLM expansion / HyDE 提示 source-language
#   2. 决定是否触发 expansion / HyDE（英文 query 走原文）

def _detect_query_lang(query: str) -> str:
    """
    返回 'ja' / 'zh' / 'en' / 'mixed'。
    关键：日语漢字与中文汉字 Unicode 重叠，靠"是否有平假名/片假名"区分日语。
    """
    if not query:
        return "en"
    hira  = sum(1 for c in query if "぀" <= c <= "ゟ")
    kata  = sum(1 for c in query if "゠" <= c <= "ヿ")
    kanji = sum(1 for c in query if "一" <= c <= "鿿")
    ascii_letters = sum(1 for c in query if c.isascii() and c.isalpha())
    n = len(query)

    # 任何假名 → 日语
    if (hira + kata) > 0:
        return "ja"
    # 无假名但 kanji 占比 > 40% → 中文
    if n > 0 and kanji / n > 0.4:
        return "zh"
    if n > 0 and ascii_letters / n > 0.5:
        return "en"
    return "mixed"


def _needs_llm_expansion(lang: str) -> bool:
    """非英文 query 都尝试 expansion → 英文关键词，拓宽 sparse 召回"""
    return lang in ("zh", "ja", "mixed")


_LANG_NAME = {"ja": "Japanese", "zh": "Chinese", "en": "English", "mixed": "mixed-language"}


async def _expand_query_with_llm(query: str, src_lang: str) -> str:
    """Ollama 翻译：非英文 query → 英文代码关键词。失败时静默降级。"""
    try:
        import httpx
        src_name = _LANG_NAME.get(src_lang, "non-English")
        prompt = (
            f"Translate this {src_name} code search query to English code keywords only. "
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


# ── HyDE：Hypothetical Document Embedding ────────────────────────────────────
# 让 LLM 根据 query 生成"假想的实现代码"，用这段假想代码做 dense embedding。
# 跨"自然语言 → 真实代码"的语义鸿沟特别有效。

async def _hyde_generate(query: str, src_lang: str) -> str:
    """生成假想代码片段。失败/空串时由调用方降级回原 query embed。"""
    try:
        import httpx
        src_name = _LANG_NAME.get(src_lang, "the source")
        prompt = (
            f"Given the following code search query (in {src_name}), write a short "
            f"hypothetical code snippet (at most 15 lines, choose Java/Python/JS as "
            f"appropriate) that would likely match the answer. Output ONLY code, "
            f"no markdown fences, no comments, no explanation.\n\n"
            f"Query: {query}\n\nCode:"
        )
        async with httpx.AsyncClient(timeout=settings.HYDE_TIMEOUT) as client:
            resp = await client.post(
                f"{settings.OLLAMA_HOST}/api/generate",
                json={
                    "model":  settings.OLLAMA_MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"num_predict": 200, "temperature": 0.2},
                },
            )
            if resp.status_code == 200:
                code = resp.json().get("response", "").strip()
                # 去掉 markdown fence
                code = re.sub(r"^```\w*\s*|\s*```$", "", code, flags=re.MULTILINE).strip()
                if len(code) < 10:
                    return ""
                return code
    except Exception as e:
        logger.debug("HyDE skipped: %s", e)
    return ""


class QueryIntent:
    def __init__(self, raw: str):
        self.raw = raw
        self.lang = _detect_query_lang(raw)
        self.identifiers = _extract_identifiers(raw)
        self.expanded_tokens: list[str] = []
        self.llm_expansion: str = ""   # 由 semantic_search 异步填充
        self.hyde_code: str = ""       # 假想代码

        # 驼峰/下划线标识符拆分；CJK 不拆分
        for ident in self.identifiers:
            self.expanded_tokens.extend(_split_identifier(ident))

        # 意图分类
        low = raw.lower()
        if any(k in low for k in ["调用", "谁调用", "被调用", "呼び出", "caller", "who calls"]):
            self.intent = "call_chain"
        elif any(k in low for k in ["影响", "依赖", "影響", "依存", "impact", "affect", "depend"]):
            self.intent = "impact"
        elif self.identifiers and len(raw.split()) <= 4 and not _CJK_RE.search(raw):
            # ASCII identifier-only 且短 → symbol_lookup
            self.intent = "symbol_lookup"
        else:
            self.intent = "semantic"

    def embed_query_text(self) -> str:
        """dense 检索用文本。bge-m3 不需要 instruction prefix。"""
        return self.raw


# ── Layer 2：双通道检索（Qdrant dense + Qdrant sparse）+ RRF ──────────────────

def _make_filter(language_filter: Optional[str]) -> Optional[Filter]:
    if not language_filter:
        return None
    return Filter(must=[FieldCondition(
        key="language", match=MatchValue(value=language_filter)
    )])


async def _dense_search(
    query_vec: list[float], repo_id: str, top_k: int,
    language_filter: Optional[str],
) -> tuple[list[tuple[str, float]], dict[str, dict]]:
    """
    Qdrant 具名 dense 向量检索。返回 ([(chunk_id, score), ...], {chunk_id: payload})。
    """
    client = get_qdrant()
    col = collection_name(repo_id)
    try:
        res = await client.query_points(
            collection_name=col,
            query=query_vec,
            using=DENSE_VECTOR_NAME,
            limit=top_k,
            query_filter=_make_filter(language_filter),
            with_payload=True,
        )
        scores   = [(str(h.id), round(h.score, 4)) for h in res.points]
        payloads = {str(h.id): (h.payload or {}) for h in res.points}
        return scores, payloads
    except Exception as e:
        logger.error("Dense search failed for collection %s: %s", col, e)
        return [], {}


async def _sparse_search(
    query_sparse: dict[int, float], repo_id: str, top_k: int,
    language_filter: Optional[str],
) -> tuple[list[tuple[str, float]], dict[str, dict]]:
    """
    Qdrant 具名 sparse 向量检索（bge-m3 lexical_weights）。
    替代旧 BM25，多语言能力强一大截。
    """
    if not query_sparse:
        return [], {}
    client = get_qdrant()
    col = collection_name(repo_id)
    indices, values = sparse_to_indices_values(query_sparse)
    try:
        res = await client.query_points(
            collection_name=col,
            query=SparseVector(indices=indices, values=values),
            using=SPARSE_VECTOR_NAME,
            limit=top_k,
            query_filter=_make_filter(language_filter),
            with_payload=True,
        )
        scores   = [(str(h.id), round(h.score, 4)) for h in res.points]
        payloads = {str(h.id): (h.payload or {}) for h in res.points}
        return scores, payloads
    except Exception as e:
        logger.error("Sparse search failed for collection %s: %s", col, e)
        return [], {}


def _rrf_merge(
    dense: list[tuple[str, float]],
    sparse: list[tuple[str, float]],
    extra_dense: Optional[list[tuple[str, float]]] = None,
    k: int = RRF_K,
) -> list[tuple[str, float]]:
    """
    RRF 融合。extra_dense 是 HyDE 假想代码的检索结果，作为第三路（可选）。
    返回 [(chunk_id, rrf_score), ...] 降序。
    """
    rank_sets: list[dict[str, int]] = []
    if dense:
        rank_sets.append({cid: rank for rank, (cid, _) in enumerate(dense, 1)})
    if sparse:
        rank_sets.append({cid: rank for rank, (cid, _) in enumerate(sparse, 1)})
    if extra_dense:
        rank_sets.append({cid: rank for rank, (cid, _) in enumerate(extra_dense, 1)})

    all_ids: set[str] = set()
    for rs in rank_sets:
        all_ids |= rs.keys()

    results: list[tuple[str, float]] = []
    for cid in all_ids:
        rrf = 0.0
        for rs in rank_sets:
            if cid in rs:
                rrf += 1.0 / (k + rs[cid])
        results.append((cid, round(rrf, 6)))

    results.sort(key=lambda x: x[1], reverse=True)
    return results


# ── Layer 3：Call Graph 增强 ───────────────────────────────────────────────────

async def _graph_enhance(
    candidate_ids: list[str],
    payloads: dict[str, dict],
    repo_id: str,
    intent: QueryIntent,
) -> dict[str, float]:
    """
    对候选 chunk_id 列表，通过 payload.symbol_id 查 Neo4j 拿图分。
    返回 {chunk_id: graph_score in [0,1]}。
    """
    if not candidate_ids:
        return {}

    symbol_ids = {
        sid for cid in candidate_ids
        if (sid := (payloads.get(cid, {}) or {}).get("symbol_id"))
    }
    if not symbol_ids:
        return {}

    rows = await run_query(
        """
        MATCH (m:Method {repo_id: $repo_id})
        WHERE m.id IN $symbol_ids
        RETURN m.id        AS symbol_id,
               m.name      AS method_name,
               coalesce(m.pagerank, 0.0) AS pagerank,
               size([(m)<-[:CALLS]-() | 1]) AS in_degree
        """,
        {"repo_id": repo_id, "symbol_ids": list(symbol_ids)},
    )

    # log 归一化：PageRank 原始分布是幂律
    max_log_pr = max((math.log1p(r["pagerank"]) for r in rows), default=0.0) or 1.0

    symbol_graph: dict[str, float] = {}
    for row in rows:
        sid       = row["symbol_id"]
        pr_norm   = math.log1p(row["pagerank"]) / max_log_pr
        in_degree = min(row["in_degree"] / 10.0, 1.0)

        # symbol 精确命中
        symbol_hit = 0.0
        name_lower = (row["method_name"] or "").lower()
        for ident in intent.identifiers:
            il = ident.lower()
            if il == name_lower:
                symbol_hit = 1.0
                break
            if il in name_lower or name_lower in il:
                symbol_hit = max(symbol_hit, 0.7)
        if symbol_hit < 0.7:
            for token in intent.expanded_tokens:
                if token in name_lower:
                    symbol_hit = max(symbol_hit, 0.4)
                    break

        symbol_graph[sid] = round(
            pr_norm    * 0.35 +
            in_degree  * 0.25 +
            symbol_hit * 0.40,
            4,
        )

    graph_scores: dict[str, float] = {}
    for cid in candidate_ids:
        sid = (payloads.get(cid, {}) or {}).get("symbol_id")
        if sid and sid in symbol_graph:
            graph_scores[cid] = symbol_graph[sid]
    return graph_scores


# ── Layer 4：Cross-encoder Rerank ──────────────────────────────────────────────

async def _cross_rerank(
    query: str, candidate_ids: list[str], payloads: dict[str, dict],
) -> dict[str, float]:
    """
    用 bge-reranker-v2-m3 对 top-N 做 pair-wise 重排。
    返回 {chunk_id: rerank_score in [0,1]}。reranker 不可用时返回空 dict。
    """
    if not reranker_mod.is_available() or not candidate_ids:
        return {}

    docs: list[str] = []
    valid_ids: list[str] = []
    for cid in candidate_ids:
        p = payloads.get(cid, {})
        # 用 raw_code（payload.text 已经是 raw_code 了，参见 indexer Pass3）
        text = p.get("text") or ""
        if not text:
            continue
        # 拼上少量元数据帮 reranker 定位
        head = []
        if p.get("file_path"):
            head.append(p["file_path"])
        if p.get("class_name"):
            head.append(p["class_name"])
        if p.get("symbol"):
            head.append(p["symbol"])
        prefix = " / ".join(head)
        doc = f"{prefix}\n{text[:2000]}" if prefix else text[:2000]
        docs.append(doc)
        valid_ids.append(cid)

    if not docs:
        return {}

    scores = await reranker_mod.rerank(query, docs)
    return {cid: float(sc) for cid, sc in zip(valid_ids, scores)}


# ── Layer 5：综合打分 ─────────────────────────────────────────────────────────

def _final_rerank(
    candidates: list[tuple[str, float]],
    graph_scores: dict[str, float],
    rerank_scores: dict[str, float],
    payloads: dict[str, dict],
    intent: QueryIntent,
) -> list[tuple[str, float]]:
    """
    综合打分。
    - 有 reranker 时：reranker 主权重；RRF/graph/symbol/struct 作辅助
    - 无 reranker 时：RRF 主权重（v2 行为）
    """
    rrf_max = max((c[1] for c in candidates), default=0.0) or 1.0
    use_reranker = bool(rerank_scores)

    if use_reranker:
        w_rrf, w_graph, w_symbol, w_struct = W_RRF, W_GRAPH, W_SYMBOL, W_STRUCT
    else:
        w_rrf, w_graph, w_symbol, w_struct = (
            W_RRF_FALLBACK, W_GRAPH_FALLBACK, W_SYMBOL_FALLBACK, W_STRUCT_FALLBACK
        )

    scored = []
    for chunk_id, rrf_score in candidates:
        payload     = payloads.get(chunk_id, {})
        graph_score = graph_scores.get(chunk_id, 0.0)
        rerank_s    = rerank_scores.get(chunk_id, 0.0) if use_reranker else 0.0
        rrf_norm    = rrf_score / rrf_max

        # symbol 精确匹配（取 max）
        symbol_score = 0.0
        sym = (payload.get("symbol") or "").lower()
        cls = (payload.get("class_name") or "").lower()
        for ident in intent.identifiers:
            il = ident.lower()
            if il == sym or il == cls:
                symbol_score = 1.0
                break
            if sym and (il in sym or sym in il):
                symbol_score = max(symbol_score, 0.7)
            if cls and (il in cls or cls in il):
                symbol_score = max(symbol_score, 0.6)

        # struct 匹配（token 重叠率）
        text = (payload.get("text") or "").lower()
        struct_score = 0.0
        if intent.expanded_tokens:
            hits = sum(1 for t in intent.expanded_tokens if t.lower() in text)
            struct_score = min(hits / len(intent.expanded_tokens), 1.0)

        final = (
            (rerank_s    * W_RERANKER if use_reranker else 0.0) +
            rrf_norm     * w_rrf  +
            graph_score  * w_graph +
            symbol_score * w_symbol +
            struct_score * w_struct
        )
        scored.append((chunk_id, round(final, 4)))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


# ── 主接口 ────────────────────────────────────────────────────────────────────

async def semantic_search(req: SearchRequest, db: aiosqlite.Connection) -> SearchResponse:
    t0 = time.monotonic()

    intent = QueryIntent(req.query)
    fetch_k = max(req.top_k * 10, 100)
    logger.info("Search [%s] '%s' lang=%s intent=%s",
                req.repo_id, req.query, intent.lang, intent.intent)

    # ── collection 存在性检查 ────────────────────────────────────────────────
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

    # ── Layer 1：异步触发 LLM expansion / HyDE（不阻塞主路径）──────────────────
    llm_expansion_task = None
    if _needs_llm_expansion(intent.lang):
        llm_expansion_task = asyncio.create_task(
            _expand_query_with_llm(req.query, intent.lang)
        )

    hyde_task = None
    # 仅 semantic / impact 意图触发 HyDE；symbol_lookup / call_chain 走精确路径
    if settings.USE_HYDE and intent.intent in ("semantic", "impact"):
        hyde_task = asyncio.create_task(_hyde_generate(req.query, intent.lang))

    # ── Layer 2：query 同时算 dense + sparse（bge-m3 一次前向），并行触发检索 ─
    q_dense, q_sparse = await encode_query(intent.embed_query_text())

    dense_task  = asyncio.create_task(
        _dense_search(q_dense, req.repo_id, fetch_k, req.language_filter)
    )
    sparse_task = asyncio.create_task(
        _sparse_search(q_sparse, req.repo_id, fetch_k, req.language_filter)
    )

    (dense_results, dense_payloads), (sparse_results, sparse_payloads) = await asyncio.gather(
        dense_task, sparse_task
    )

    # HyDE：用假想代码 embed 再跑一路 dense
    extra_dense: list[tuple[str, float]] = []
    extra_payloads: dict[str, dict] = {}
    if hyde_task is not None:
        try:
            hyde_code = await asyncio.wait_for(hyde_task, timeout=settings.HYDE_TIMEOUT)
        except asyncio.TimeoutError:
            hyde_code = ""
        if hyde_code:
            intent.hyde_code = hyde_code
            try:
                hyde_vec = await embed_query(hyde_code)
                extra_dense, extra_payloads = await _dense_search(
                    hyde_vec, req.repo_id, fetch_k, req.language_filter
                )
                logger.info("HyDE active: %d extra candidates", len(extra_dense))
            except Exception as e:
                logger.debug("HyDE embed/search failed: %s", e)

    # LLM expansion 完成后，可以将其 token 注入 intent（用于 symbol/struct 评分）
    if llm_expansion_task is not None:
        try:
            intent.llm_expansion = await asyncio.wait_for(llm_expansion_task, timeout=5.0)
            if intent.llm_expansion:
                # 把展开词追加到 expanded_tokens，参与 struct 评分
                intent.expanded_tokens.extend(
                    t.lower() for t in intent.llm_expansion.split() if len(t) >= 2
                )
                logger.info("LLM expansion: %r → %r", req.query, intent.llm_expansion)
        except asyncio.TimeoutError:
            intent.llm_expansion = ""

    # ── RRF 三路融合 ────────────────────────────────────────────────────────
    candidates = _rrf_merge(dense_results, sparse_results, extra_dense or None)
    if not candidates:
        latency_ms = int((time.monotonic() - t0) * 1000)
        return SearchResponse(results=[], total=0, latency_ms=latency_ms,
                              query=req.query, intent=intent.intent)

    # 合并 payload（dense 优先；sparse-only / hyde-only 命中补全）
    payloads: dict[str, dict] = {**extra_payloads, **sparse_payloads, **dense_payloads}
    candidate_ids = [c[0] for c in candidates]
    missing = [cid for cid in candidate_ids if cid not in payloads]
    if missing:
        more = await _fetch_payloads(req.repo_id, missing)
        payloads.update(more)

    # ── Layer 3：Graph 增强 ────────────────────────────────────────────────
    graph_scores = await _graph_enhance(candidate_ids, payloads, req.repo_id, intent)

    # ── Layer 4：Cross-encoder Rerank（对前 RERANKER_TOP_K 做） ─────────────
    rerank_pool_ids = candidate_ids[: settings.RERANKER_TOP_K]
    rerank_scores = await _cross_rerank(req.query, rerank_pool_ids, payloads)

    # ── Layer 5：综合打分 ─────────────────────────────────────────────────
    ranked = _final_rerank(candidates, graph_scores, rerank_scores, payloads, intent)

    # 去重 + 截断
    deduped = _deduplicate(ranked, payloads)
    final_ids = [cid for cid, _ in deduped[:req.top_k]]

    # 调用链 + 结果构建
    results = await _build_results(final_ids, ranked, payloads, req.repo_id, db)

    latency_ms = int((time.monotonic() - t0) * 1000)
    logger.info("Search [%s] '%s' → %d results in %dms (reranker=%s, hyde=%s)",
                req.repo_id, req.query, len(results), latency_ms,
                bool(rerank_scores), bool(extra_dense))

    # 查询历史
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
    """
    去重两层：
      1. symbol_id 级：同方法的多个切窗 chunk 只留最高分
      2. 行号级：跨 symbol 但行号重叠 >40% 的也合并
    """
    seen_symbols: set[str] = set()
    by_symbol: list[tuple[str, float]] = []
    for chunk_id, score in ranked:
        sid = (payloads.get(chunk_id, {}) or {}).get("symbol_id", "")
        if sid:
            if sid in seen_symbols:
                continue
            seen_symbols.add(sid)
        by_symbol.append((chunk_id, score))

    kept: list[tuple[str, float, int, int, str]] = []
    for chunk_id, score in by_symbol:
        p = payloads.get(chunk_id, {})
        fp = p.get("file_path", "")
        ls = p.get("line_start", 0)
        le = p.get("line_end", 0)
        overlap = False
        for _, _, kls, kle, kfp in kept:
            if kfp != fp:
                continue
            ov_start = max(kls, ls)
            ov_end   = min(kle, le)
            if ov_end >= ov_start:
                shorter = max(min(le - ls + 1, kle - kls + 1), 1)
                if (ov_end - ov_start + 1) / shorter > 0.4:
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
    results: list[SearchResultItem] = []

    symbol_ids = [
        sid for cid in final_ids
        if (sid := (payloads.get(cid, {}) or {}).get("symbol_id", ""))
    ]
    callers_map, callees_map = await _fetch_call_chains(symbol_ids, repo_id)

    for chunk_id in final_ids:
        p = payloads.get(chunk_id)
        if not p:
            continue
        symbol_id = p.get("symbol_id", "")
        results.append(SearchResultItem(
            file_path  = p.get("file_path", ""),
            line_start = p.get("line_start", 0),
            line_end   = p.get("line_end", 0),
            snippet    = p.get("text", "")[:3000],
            score      = score_map.get(chunk_id, 0.0),
            language   = p.get("language", ""),
            chunk_type = p.get("chunk_type", "method"),
            symbol_id  = symbol_id,
            method_name = p.get("symbol", ""),
            class_name  = p.get("class_name", ""),
            callers    = callers_map.get(symbol_id, []),
            callees    = callees_map.get(symbol_id, []),
        ))
    return results


async def _fetch_call_chains(
    symbol_ids: list[str],
    repo_id: str,
) -> tuple[dict[str, list[CallChainItem]], dict[str, list[CallChainItem]]]:
    """批量查各方法的 1-hop caller 和 callee。键为 symbol_id（Method.id）"""
    if not symbol_ids:
        return {}, {}
    rows = await run_query(
        """
        MATCH (m:Method {repo_id: $repo_id})
        WHERE m.id IN $symbol_ids
        OPTIONAL MATCH (caller:Method)-[:CALLS]->(m)
        OPTIONAL MATCH (m)-[:CALLS]->(callee:Method)
        RETURN m.id          AS center,
               caller.id     AS caller_id,
               caller.name   AS caller_name,
               caller.class_name AS caller_class,
               caller.file_path  AS caller_file,
               callee.id     AS callee_id,
               callee.name   AS callee_name,
               callee.class_name AS callee_class,
               callee.file_path  AS callee_file
        """,
        {"repo_id": repo_id, "symbol_ids": symbol_ids},
    )

    callers_map: dict[str, list[CallChainItem]] = {sid: [] for sid in symbol_ids}
    callees_map: dict[str, list[CallChainItem]] = {sid: [] for sid in symbol_ids}
    seen_callers: dict[str, set] = {sid: set() for sid in symbol_ids}
    seen_callees: dict[str, set] = {sid: set() for sid in symbol_ids}

    for row in rows:
        center = row.get("center")
        if not center:
            continue

        if row.get("caller_id") and row["caller_id"] not in seen_callers.get(center, set()):
            seen_callers[center].add(row["caller_id"])
            callers_map[center].append(CallChainItem(
                symbol_id   = row["caller_id"],
                method_name = row.get("caller_name") or "",
                class_name  = row.get("caller_class"),
                file_path   = row.get("caller_file") or "",
                direction   = "caller",
                depth       = 1,
            ))

        if row.get("callee_id") and row["callee_id"] not in seen_callees.get(center, set()):
            seen_callees[center].add(row["callee_id"])
            callees_map[center].append(CallChainItem(
                symbol_id   = row["callee_id"],
                method_name = row.get("callee_name") or "",
                class_name  = row.get("callee_class"),
                file_path   = row.get("callee_file") or "",
                direction   = "callee",
                depth       = 1,
            ))

    return callers_map, callees_map
