from __future__ import annotations

"""
BM25 索引模块
-------------
- 使用 rank_bm25.BM25Okapi
- 每个 repo 独立一个索引文件（pickle）
- 支持标识符感知的 tokenization（驼峰/下划线拆分）
- 查询返回 (chunk_id, score) 列表
"""

import logging
import pickle
import re
from pathlib import Path
from typing import Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

# ── Tokenizer ─────────────────────────────────────────────────────────────────

_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def _tokenize(text: str) -> list[str]:
    """
    标识符感知 tokenization：
      1. 保留原始标识符（processPayment → processpayment，用于精确查询）
      2. 驼峰拆分  processPayment → process payment
      3. 下划线拆分 get_order_id  → get order id
      4. 小写 + 去停用词（长度 < 2 的 token）
    """
    # 先提取原始标识符，保持整词（用于精确匹配）
    raw_idents = re.findall(r'[a-zA-Z_][a-zA-Z0-9_]+', text)
    # 驼峰拆分
    split_text = _CAMEL_RE.sub(" ", text)
    # 非字母数字 → 空格
    split_text = re.sub(r"[^a-zA-Z0-9]", " ", split_text)
    split_tokens = [t.lower() for t in split_text.split() if len(t) >= 2]
    # 合并：原始标识符优先（精确匹配权重高），再追加拆分词
    seen: set[str] = set()
    result: list[str] = []
    for t in [i.lower() for i in raw_idents] + split_tokens:
        if t not in seen and len(t) >= 2:
            seen.add(t)
            result.append(t)
    return result


# ── BM25Index 类 ──────────────────────────────────────────────────────────────

class BM25Index:
    """单个 repo 的 BM25 索引"""

    def __init__(self) -> None:
        self._bm25 = None          # BM25Okapi 实例
        self._chunk_ids: list[str] = []   # 与 BM25 corpus 对应的 chunk_id 列表

    def build(self, docs: list[dict]) -> None:
        """
        构建索引。
        docs: [{"chunk_id": str, "text": str}, ...]
        """
        from rank_bm25 import BM25Okapi

        self._chunk_ids = [d["chunk_id"] for d in docs]
        corpus = [_tokenize(d["text"]) for d in docs]
        self._bm25 = BM25Okapi(corpus)
        logger.info("BM25 index built: %d documents", len(docs))

    def query(self, q: str, top_k: int = 50) -> list[tuple[str, float]]:
        """
        返回 [(chunk_id, normalized_score), ...] 降序，最多 top_k 条。
        score 归一化到 [0, 1]（除以最大分）。
        """
        if self._bm25 is None:
            return []
        tokens = _tokenize(q)
        if not tokens:
            return []

        scores = self._bm25.get_scores(tokens)
        max_score = float(scores.max()) if scores.max() > 0 else 1.0

        # 只保留分数 > 0 的结果
        pairs = [
            (self._chunk_ids[i], round(float(scores[i]) / max_score, 4))
            for i in range(len(scores))
            if scores[i] > 0
        ]
        pairs.sort(key=lambda x: x[1], reverse=True)
        return pairs[:top_k]

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump({"bm25": self._bm25, "chunk_ids": self._chunk_ids}, f)
        logger.debug("BM25 index saved to %s", path)

    @classmethod
    def load(cls, path: Path) -> "BM25Index":
        with open(path, "rb") as f:
            data = pickle.load(f)
        idx = cls()
        idx._bm25 = data["bm25"]
        idx._chunk_ids = data["chunk_ids"]
        logger.debug("BM25 index loaded from %s (%d docs)", path, len(idx._chunk_ids))
        return idx


# ── 全局缓存（内存中保留已加载的索引）────────────────────────────────────────

_cache: dict[str, BM25Index] = {}


def _index_path(repo_id: str) -> Path:
    return settings.BM25_INDEX_DIR / f"{repo_id}.pkl"


def get_bm25(repo_id: str) -> Optional[BM25Index]:
    """从缓存或磁盘加载。不存在返回 None。"""
    if repo_id in _cache:
        return _cache[repo_id]
    path = _index_path(repo_id)
    if path.exists():
        idx = BM25Index.load(path)
        _cache[repo_id] = idx
        return idx
    return None


def save_bm25(repo_id: str, idx: BM25Index) -> Path:
    """保存到磁盘并更新缓存"""
    path = _index_path(repo_id)
    idx.save(path)
    _cache[repo_id] = idx
    return path


def invalidate_bm25(repo_id: str) -> None:
    """重新索引时清除旧缓存"""
    _cache.pop(repo_id, None)
    path = _index_path(repo_id)
    if path.exists():
        path.unlink()
