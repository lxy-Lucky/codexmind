from __future__ import annotations

"""
Parser Service
--------------
用 tree-sitter 按函数/方法/类级别切分代码，
超长 chunk 做 sliding window 降级切分。
"""

import logging
from functools import lru_cache
from typing import Any, Optional

import tiktoken

from app.core.config import settings

logger = logging.getLogger(__name__)

# tiktoken 用于 token 计数（GPT-2 tokenizer 近似，足够用于长度控制）
_enc = tiktoken.get_encoding("cl100k_base")


def _count_tokens(text: str) -> int:
    return len(_enc.encode(text, disallowed_special=()))


# ── tree-sitter 语言加载 ──────────────────────────────────────────────────────

@lru_cache(maxsize=None)
def _get_parser(language: str) -> Optional[Any]:
    try:
        import tree_sitter_java
        import tree_sitter_python
        import tree_sitter_javascript
        import tree_sitter_typescript
        import tree_sitter_go
        from tree_sitter import Language, Parser

        lang_map = {
            "java":       tree_sitter_java.language(),
            "python":     tree_sitter_python.language(),
            "javascript": tree_sitter_javascript.language(),
            "typescript": tree_sitter_typescript.language_typescript(),
            "go":         tree_sitter_go.language(),
        }

        if language not in lang_map:
            return None

        lang = Language(lang_map[language])
        parser = Parser(lang)
        return parser
    except Exception as e:
        logger.warning("tree-sitter parser load failed for %s: %s", language, e)
        return None


# ── 各语言的"函数/方法"节点类型 ─────────────────────────────────────────────

NODE_TYPES: dict[str, list[str]] = {
    "java":       ["method_declaration", "constructor_declaration", "class_declaration"],
    "python":     ["function_definition", "async_function_definition", "class_definition"],
    "javascript": ["function_declaration", "arrow_function", "method_definition", "class_declaration"],
    "typescript": ["function_declaration", "arrow_function", "method_definition", "class_declaration"],
    "go":         ["function_declaration", "method_declaration"],
    "kotlin":     ["function_declaration", "class_declaration"],
    "rust":       ["function_item", "impl_item"],
}


# ── 主接口 ────────────────────────────────────────────────────────────────────

def parse_chunks(
    source: str,
    language: str,
    file_path: str,
) -> list[dict]:
    """
    返回 chunk 列表，每个 chunk:
    {
        text: str,
        line_start: int,    # 1-indexed
        line_end: int,
        chunk_type: str,    # method | class | block
        symbol: str,        # 函数/类名（尽力提取）
    }
    """
    parser = _get_parser(language)

    if parser is None:
        # 降级：按行滑动窗口切分
        return _sliding_window_chunks(source, file_path, chunk_type="block")

    try:
        tree = parser.parse(source.encode("utf-8"))
        return _extract_from_tree(tree, source, language, file_path)
    except Exception as e:
        logger.warning("tree-sitter parse error [%s] %s: %s", language, file_path, e)
        return _sliding_window_chunks(source, file_path, chunk_type="block")


# ── tree-sitter 提取 ──────────────────────────────────────────────────────────

def _extract_from_tree(tree, source: str, language: str, file_path: str) -> list[dict]:
    lines = source.splitlines()
    target_types = NODE_TYPES.get(language, [])
    chunks: list[dict] = []

    def walk(node):
        if node.type in target_types:
            line_start = node.start_point[0] + 1  # 转 1-indexed
            line_end   = node.end_point[0]   + 1
            text       = "\n".join(lines[line_start - 1: line_end])
            symbol     = _extract_symbol(node, source)
            chunk_type = "class" if "class" in node.type else "method"

            sub_chunks = _maybe_split(text, line_start, line_end, chunk_type, symbol)
            chunks.extend(sub_chunks)
            # 不继续递归子节点（避免方法内嵌套方法重复）
            return

        for child in node.children:
            walk(child)

    walk(tree.root_node)

    # 如果没提取到任何 chunk（纯常量文件等），降级
    if not chunks:
        return _sliding_window_chunks(source, file_path, chunk_type="block")

    return chunks


def _extract_symbol(node, source: str) -> str:
    """从节点中尝试提取函数名/类名"""
    for child in node.children:
        if child.type == "identifier":
            return source[child.start_byte:child.end_byte]
    return ""


def _maybe_split(
    text: str,
    line_start: int,
    line_end: int,
    chunk_type: str,
    symbol: str,
) -> list[dict]:
    """如果 chunk 太长，做 sliding window 切分"""
    if _count_tokens(text) <= settings.CHUNK_MAX_TOKENS:
        return [{
            "text":       text,
            "line_start": line_start,
            "line_end":   line_end,
            "chunk_type": chunk_type,
            "symbol":     symbol,
        }]
    return _sliding_window_chunks(text, "", chunk_type, base_line=line_start)


# ── Sliding Window 降级切分 ───────────────────────────────────────────────────

def _sliding_window_chunks(
    source: str,
    file_path: str,
    chunk_type: str = "block",
    base_line: int = 1,
) -> list[dict]:
    lines = source.splitlines()
    max_tokens  = settings.CHUNK_MAX_TOKENS
    overlap     = settings.CHUNK_OVERLAP_TOKENS
    chunks      = []
    start_idx   = 0

    while start_idx < len(lines):
        current_lines = []
        current_tokens = 0
        end_idx = start_idx

        while end_idx < len(lines):
            line_tokens = _count_tokens(lines[end_idx])
            if current_tokens + line_tokens > max_tokens and current_lines:
                break
            current_lines.append(lines[end_idx])
            current_tokens += line_tokens
            end_idx += 1

        if not current_lines:
            # 单行就超限，强制截断
            current_lines = [lines[start_idx]]
            end_idx = start_idx + 1

        text = "\n".join(current_lines)
        chunks.append({
            "text":       text,
            "line_start": base_line + start_idx,
            "line_end":   base_line + end_idx - 1,
            "chunk_type": chunk_type,
            "symbol":     "",
        })

        # 步进：下一窗口从 (end_idx - overlap_lines) 开始
        overlap_lines = max(1, overlap // 10)
        start_idx = max(end_idx - overlap_lines, start_idx + 1)

    return chunks
