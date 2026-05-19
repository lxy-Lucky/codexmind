from __future__ import annotations

"""
Parser Service
--------------
切分策略优先级：
  1. tree-sitter（已安装时）：精确 AST 级别方法/类切分
  2. 正则 Parser（fallback）：识别方法签名边界，按方法粒度切分
  3. Sliding Window（最终兜底）：仅在正则也失效时使用
"""

import logging
import re
from functools import lru_cache
from typing import Any, Optional

import tiktoken

from app.core.config import settings

logger = logging.getLogger(__name__)

_enc = tiktoken.get_encoding("cl100k_base")


def _count_tokens(text: str) -> int:
    return len(_enc.encode(text, disallowed_special=()))


# ── tree-sitter ───────────────────────────────────────────────────────────────

@lru_cache(maxsize=None)
def _get_parser(language: str) -> Optional[Any]:
    try:
        import tree_sitter_java, tree_sitter_python
        import tree_sitter_javascript, tree_sitter_typescript, tree_sitter_go
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
        parser = Parser(Language(lang_map[language]))
        return parser
    except Exception as e:
        logger.debug("tree-sitter not available for %s: %s", language, e)
        return None


NODE_TYPES: dict[str, list[str]] = {
    "java":       ["method_declaration", "constructor_declaration", "class_declaration"],
    "python":     ["function_definition", "async_function_definition", "class_definition"],
    "javascript": ["function_declaration", "arrow_function", "method_definition", "class_declaration"],
    "typescript": ["function_declaration", "arrow_function", "method_definition", "class_declaration"],
    "go":         ["function_declaration", "method_declaration"],
    "kotlin":     ["function_declaration", "class_declaration"],
    "rust":       ["function_item", "impl_item"],
}


# ── 正则 Parser ───────────────────────────────────────────────────────────────

# Java/Kotlin/C#/Scala：必须有访问修饰符，排除控制流关键字
_JAVA_METHOD_RE = re.compile(
    r'^([ \t]*)'
    r'(?:(?:public|private|protected|static|final|synchronized|abstract|default|override'
    r'|async|native|strictfp|transient|volatile)\s+)+'      # 至少一个修饰符
    r'(?:(?:<[^>]+>\s+)?[\w\[\]<>,.?]+\s+)'                 # 返回类型
    r'(?!if\b|for\b|while\b|switch\b|catch\b|else\b|try\b|new\b)'  # 排除控制流
    r'([a-zA-Z_]\w*)'                                        # 方法名
    r'\s*\([^)]{0,200}\)'                                    # 参数（限长防灾难性回溯）
    r'(?:\s+throws\s+[\w,\s]+)?'
    r'\s*\{',
    re.MULTILINE,
)

# Python
_PYTHON_DEF_RE = re.compile(
    r'^([ \t]*)(async\s+)?def\s+([a-zA-Z_]\w*)\s*\(',
    re.MULTILINE,
)

# JS/TS：函数声明 + 箭头函数赋值
_JS_FUNC_RE = re.compile(
    r'^([ \t]*)(?:export\s+(?:default\s+)?)?(?:async\s+)?'
    r'(?:'
    r'function\s+([a-zA-Z_]\w*)\s*\([^)]{0,200}\)\s*\{'   # function foo() {
    r'|(?:const|let|var)\s+([a-zA-Z_]\w*)\s*=\s*(?:async\s+)?'
    r'(?:\([^)]{0,200}\)|[a-zA-Z_]\w*)\s*=>\s*\{'           # const foo = () => {
    r')',
    re.MULTILINE,
)


def _regex_parse_chunks(source: str, language: str, file_path: str) -> list[dict]:
    lines = source.splitlines()
    n = len(lines)

    if language in ("java", "kotlin", "csharp", "scala", "cpp", "c", "rust"):
        pattern = _JAVA_METHOD_RE
        sym_group = 2
    elif language == "python":
        pattern = _PYTHON_DEF_RE
        sym_group = 3
    elif language in ("javascript", "typescript"):
        pattern = _JS_FUNC_RE
        sym_group = 2
    else:
        return []

    method_starts: list[tuple[int, str]] = []
    for m in pattern.finditer(source):
        line_idx = source[:m.start()].count('\n')
        groups = m.groups()
        symbol = groups[sym_group - 1] if len(groups) >= sym_group else ""
        if symbol is None:
            # 尝试下一个 group
            symbol = next((g for g in groups[1:] if g and g.strip() and
                          not g.strip().startswith('async')), "")
        method_starts.append((line_idx, symbol or ""))

    if not method_starts:
        return []

    chunks: list[dict] = []
    for i, (start_idx, symbol) in enumerate(method_starts):
        # 确定终止行
        if i + 1 < len(method_starts):
            raw_end = method_starts[i + 1][0]
            end_idx = raw_end - 1
            while end_idx > start_idx and not lines[end_idx].strip():
                end_idx -= 1
        else:
            end_idx = n - 1

        # 花括号语言：精确找方法体结束
        if language in ("java", "kotlin", "csharp", "scala", "cpp", "c", "rust",
                        "javascript", "typescript"):
            end_idx = _find_brace_end(lines, start_idx, end_idx)

        text = "\n".join(lines[start_idx: end_idx + 1])

        if not _chunk_has_body(text, language):
            continue

        sub = _maybe_split(text, start_idx + 1, end_idx + 1, "method", symbol)
        chunks.extend(sub)

    return chunks


def _find_brace_end(lines: list[str], start: int, max_end: int) -> int:
    """从 start 行向下配对花括号，返回方法体结束行（0-indexed）"""
    depth = 0
    found_open = False
    in_string_double = False
    in_string_single = False

    for i in range(start, min(max_end + 1, len(lines))):
        j = 0
        line = lines[i]
        while j < len(line):
            ch = line[j]
            # 跳过字符串内容
            if not in_string_single and ch == '"' and (j == 0 or line[j-1] != '\\'):
                in_string_double = not in_string_double
            elif not in_string_double and ch == "'" and (j == 0 or line[j-1] != '\\'):
                in_string_single = not in_string_single
            elif not in_string_double and not in_string_single:
                if ch == '{':
                    depth += 1
                    found_open = True
                elif ch == '}':
                    depth -= 1
                    if found_open and depth == 0:
                        return i
            j += 1

    return max_end


def _chunk_has_body(text: str, language: str) -> bool:
    stripped = text.strip()
    lines = [l.strip() for l in stripped.splitlines() if l.strip()]
    if not lines:
        return False
    if language == "python":
        return len(lines) >= 2
    if '{' not in stripped or '}' not in stripped:
        return False
    non_trivial = [l for l in lines
                   if not l.startswith('*') and not l.startswith('//')
                   and not l.startswith('/*') and not l.startswith('@')
                   and l not in ('{', '}', '')]
    return len(non_trivial) >= 2


# ── 主接口 ────────────────────────────────────────────────────────────────────

def parse_chunks(source: str, language: str, file_path: str) -> list[dict]:
    """
    优先级：tree-sitter → 正则 → sliding window
    """
    # 1. tree-sitter
    parser = _get_parser(language)
    if parser is not None:
        try:
            tree = parser.parse(source.encode("utf-8"))
            chunks = _extract_from_tree(tree, source, language, file_path)
            if chunks:
                return chunks
        except Exception as e:
            logger.warning("tree-sitter parse error [%s] %s: %s", language, file_path, e)

    # 2. 正则
    regex_chunks = _regex_parse_chunks(source, language, file_path)
    if regex_chunks:
        return regex_chunks

    # 3. Sliding window
    return _sliding_window_chunks(source, file_path, chunk_type="block")


# ── tree-sitter 提取 ──────────────────────────────────────────────────────────

def _extract_from_tree(tree, source: str, language: str, file_path: str) -> list[dict]:
    lines = source.splitlines()
    target_types = NODE_TYPES.get(language, [])
    chunks: list[dict] = []

    def walk(node):
        if node.type in target_types:
            line_start = node.start_point[0] + 1
            line_end   = node.end_point[0]   + 1
            text       = "\n".join(lines[line_start - 1: line_end])
            symbol     = _extract_symbol(node, source)
            chunk_type = "class" if "class" in node.type else "method"
            chunks.extend(_maybe_split(text, line_start, line_end, chunk_type, symbol))
            return
        for child in node.children:
            walk(child)

    walk(tree.root_node)
    if not chunks:
        return _sliding_window_chunks(source, file_path, chunk_type="block")
    return chunks


def _extract_symbol(node, source: str) -> str:
    for child in node.children:
        if child.type == "identifier":
            return source[child.start_byte:child.end_byte]
    return ""


def _maybe_split(text: str, line_start: int, line_end: int,
                 chunk_type: str, symbol: str) -> list[dict]:
    if _count_tokens(text) <= settings.CHUNK_MAX_TOKENS:
        return [{"text": text, "line_start": line_start, "line_end": line_end,
                 "chunk_type": chunk_type, "symbol": symbol}]
    return _sliding_window_chunks(text, "", chunk_type, base_line=line_start)


# ── Sliding Window ────────────────────────────────────────────────────────────

def _sliding_window_chunks(source: str, file_path: str,
                            chunk_type: str = "block", base_line: int = 1) -> list[dict]:
    lines = source.splitlines()
    max_tokens = settings.CHUNK_MAX_TOKENS
    overlap    = settings.CHUNK_OVERLAP_TOKENS
    chunks     = []
    start_idx  = 0

    while start_idx < len(lines):
        current_lines: list[str] = []
        current_tokens = 0
        end_idx = start_idx

        while end_idx < len(lines):
            lt = _count_tokens(lines[end_idx])
            if current_tokens + lt > max_tokens and current_lines:
                break
            current_lines.append(lines[end_idx])
            current_tokens += lt
            end_idx += 1

        if not current_lines:
            current_lines = [lines[start_idx]]
            end_idx = start_idx + 1

        text = "\n".join(current_lines)
        chunks.append({"text": text, "line_start": base_line + start_idx,
                       "line_end": base_line + end_idx - 1,
                       "chunk_type": chunk_type, "symbol": ""})

        overlap_lines = max(1, overlap // 10)
        start_idx = max(end_idx - overlap_lines, start_idx + 1)

    return chunks
