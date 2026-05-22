from __future__ import annotations

"""
Parser Service v2
-----------------
输出两类数据：
  1. chunks     - 结构化文档（用于 embed + BM25）
  2. call_refs  - 调用关系（用于构建 Call Graph）

Chunk 结构化文档格式：
  [FILE] com/example/payment/OrderService.java
  [CLASS] OrderService
  [METHOD] processPayment
  [SIGNATURE] public void processPayment(Order order, PaymentMethod method)
  [ANNOTATIONS] @Transactional
  [CALLS] PaymentGateway.charge, RetryHandler.retry
  [JAVADOC] 处理订单支付，校验金额后调用网关
  <原始代码体>

信噪比从裸代码的 ~30% 提升到 ~80%，无需 LLM。
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


# ── tree-sitter ────────────────────────────────────────────────────────────────

@lru_cache(maxsize=None)
def _get_parser(language: str) -> Optional[Any]:
    """仅支持 Java 和 JS/TS（XML 走 sliding-window fallback，无需 parser）"""
    try:
        import tree_sitter_java
        import tree_sitter_javascript, tree_sitter_typescript
        from tree_sitter import Language, Parser
        lang_map = {
            "java":       tree_sitter_java.language(),
            "javascript": tree_sitter_javascript.language(),
            "typescript": tree_sitter_typescript.language_typescript(),
        }
        if language not in lang_map:
            return None
        return Parser(Language(lang_map[language]))
    except Exception as e:
        logger.debug("tree-sitter not available for %s: %s", language, e)
        return None


NODE_TYPES: dict[str, list[str]] = {
    "java":       ["method_declaration", "constructor_declaration", "class_declaration"],
    "javascript": ["function_declaration", "arrow_function", "method_definition", "class_declaration"],
    "typescript": ["function_declaration", "arrow_function", "method_definition", "class_declaration"],
}

# ── 正则 fallback ──────────────────────────────────────────────────────────────

_JAVA_METHOD_RE = re.compile(
    r'^([ \t]*)(?:(?:public|private|protected|static|final|synchronized|abstract'
    r'|default|override|async|native|strictfp)[ \t]+)+'
    r'(?:(?:<[^>]+>[ \t]+)?[\w\[\]<>,.?]+[ \t]+)'
    r'(?!if\b|for\b|while\b|switch\b|catch\b|else\b|try\b|new\b)'
    r'([a-zA-Z_]\w*)[ \t]*\([^)]{0,200}\)'
    r'(?:[ \t]+throws[ \t]+[\w,\s]+)?[ \t]*\{',
    re.MULTILINE,
)

_PYTHON_DEF_RE = re.compile(
    r'^([ \t]*)(async[ \t]+)?def[ \t]+([a-zA-Z_]\w*)[ \t]*\(',
    re.MULTILINE,
)

_JS_FUNC_RE = re.compile(
    r'^([ \t]*)(?:export[ \t]+(?:default[ \t]+)?)?(?:async[ \t]+)?'
    r'(?:'
    r'function[ \t]+([a-zA-Z_]\w*)[ \t]*\([^)]{0,200}\)[ \t]*\{'
    r'|(?:const|let|var)[ \t]+([a-zA-Z_]\w*)[ \t]*=[ \t]*(?:async[ \t]+)?'
    r'(?:\([^)]{0,200}\)|[a-zA-Z_]\w*)[ \t]*=>[ \t]*\{'
    r')',
    re.MULTILINE,
)

# ── 调用提取（tree-sitter method_invocation）─────────────────────────────────

def _extract_calls_from_tree(tree, src: bytes, language: str) -> list[str]:
    """从 AST 提取方法调用，返回 'ClassName.methodName' 或 'methodName' 列表"""
    calls: list[str] = []

    if language == "java":
        _walk_java_calls(tree.root_node, src, calls)
    elif language in ("javascript", "typescript"):
        _walk_js_calls(tree.root_node, src, calls)

    return list(dict.fromkeys(calls))  # 去重保序


def _walk_java_calls(node, src: bytes, out: list[str]) -> None:
    if node.type == "method_invocation":
        # object.method() 或 method()
        children = list(node.children)
        names = [c for c in children if c.type == "identifier"]
        if len(names) >= 2:
            obj    = src[names[0].start_byte:names[0].end_byte].decode("utf-8", "replace")
            method = src[names[1].start_byte:names[1].end_byte].decode("utf-8", "replace")
            out.append(f"{obj}.{method}")
        elif len(names) == 1:
            out.append(src[names[0].start_byte:names[0].end_byte].decode("utf-8", "replace"))
    for child in node.children:
        _walk_java_calls(child, src, out)


def _walk_js_calls(node, src: bytes, out: list[str]) -> None:
    """提取 JS/TS 调用：member_expression（obj.method）和直接 identifier 调用"""
    if node.type == "call_expression":
        func = node.child_by_field_name("function")
        if func:
            if func.type == "member_expression":
                obj  = func.child_by_field_name("object")
                prop = func.child_by_field_name("property")
                if obj and prop:
                    obj_text  = src[obj.start_byte:obj.end_byte].decode("utf-8", "replace")
                    prop_text = src[prop.start_byte:prop.end_byte].decode("utf-8", "replace")
                    obj_last = obj_text.split(".")[-1]
                    out.append(f"{obj_last}.{prop_text}")
            elif func.type == "identifier":
                out.append(src[func.start_byte:func.end_byte].decode("utf-8", "replace"))
    for child in node.children:
        _walk_js_calls(child, src, out)


# ── 注解提取 ──────────────────────────────────────────────────────────────────

_ANNOTATION_RE = re.compile(r"@[\w]+(?:\([^)]*\))?")


def _extract_annotations(text: str) -> list[str]:
    lines = text.splitlines()[:10]  # 只看方法头部
    anns = []
    for line in lines:
        anns.extend(_ANNOTATION_RE.findall(line))
    return list(dict.fromkeys(anns))[:5]


# ── Javadoc / 注释提取 ────────────────────────────────────────────────────────

def _extract_leading_comment(text: str) -> str:
    """提取方法上方的 /** ... */ 或 // 注释"""
    lines = text.splitlines()
    comment_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("/**") or stripped.startswith("/*") \
                or stripped.startswith("*") or stripped.startswith("//"):
            # 去掉 */ * // 前缀，只保留文字
            clean = re.sub(r"^[/*\s]+", "", stripped).strip()
            if clean:
                comment_lines.append(clean)
        elif stripped.startswith("@") or stripped == "":
            continue
        else:
            break
    return " ".join(comment_lines[:3])  # 最多 3 行


# ── 结构化文档构建 ─────────────────────────────────────────────────────────────

def _build_structured_text(
    raw_code: str,
    file_path: str,
    class_name: str,
    method_name: str,
    signature: str,
    annotations: list[str],
    calls: list[str],
    comment: str,
) -> str:
    """
    将结构化元数据拼在代码前面，一起送入 embed。
    不调用 LLM，纯静态分析。
    """
    parts = []
    parts.append(f"[FILE] {file_path}")
    if class_name:
        parts.append(f"[CLASS] {class_name}")
    if method_name:
        parts.append(f"[METHOD] {method_name}")
    if signature:
        parts.append(f"[SIGNATURE] {signature}")
    if annotations:
        parts.append(f"[ANNOTATIONS] {' '.join(annotations)}")
    if calls:
        parts.append(f"[CALLS] {', '.join(calls[:8])}")  # 最多 8 个调用
    if comment:
        parts.append(f"[COMMENT] {comment}")
    parts.append("")  # 空行分隔
    parts.append(raw_code)
    return "\n".join(parts)


# ── 主接口 ────────────────────────────────────────────────────────────────────

def parse_chunks(source: str, language: str, file_path: str) -> list[dict]:
    """
    返回 chunk 列表，每个 chunk 包含：
      text        - 结构化文档（用于 embed）
      raw_code    - 原始代码（存 snippet 用）
      line_start / line_end
      chunk_type  - method | class | block
      symbol      - method_name
      class_name
      signature
      calls       - 调用的方法名列表（用于构建 Call Graph）
    """
    parser = _get_parser(language)
    if parser is not None:
        try:
            tree = parser.parse(source.encode("utf-8"))
            chunks = _extract_from_tree(tree, source, language, file_path)
            if chunks:
                return chunks
        except Exception as e:
            logger.warning("tree-sitter parse error [%s] %s: %s", language, file_path, e)

    # fallback: 正则
    regex_chunks = _regex_parse_chunks(source, language, file_path)
    if regex_chunks:
        return regex_chunks

    # 最终兜底: sliding window（无结构化元数据）
    return _sliding_window_chunks(source, file_path)


# ── tree-sitter 提取 ───────────────────────────────────────────────────────────

def _extract_from_tree(tree, source: str, language: str, file_path: str) -> list[dict]:
    # tree-sitter 所有偏移量是字节偏移，必须在 bytes 上切片
    # 行号用 source.splitlines()（字符级），代码体也用行号切，两者互不干扰
    source_bytes = source.encode("utf-8")
    lines = source.splitlines()
    target_types = NODE_TYPES.get(language, [])
    chunks: list[dict] = []

    def walk(node, parent_class: str = ""):
        if "class" in node.type and node.type in target_types:
            # 类名是 identifier 类型的直接子节点，用字节切片保证多字节正确
            for child in node.children:
                if child.type == "identifier":
                    parent_class = source_bytes[child.start_byte:child.end_byte].decode("utf-8", "replace")
                    break
            for child in node.children:
                walk(child, parent_class)
            return

        if node.type in target_types and "class" not in node.type:
            line_start = node.start_point[0] + 1
            line_end   = node.end_point[0]   + 1
            raw_code   = "\n".join(lines[line_start - 1: line_end])
            symbol     = _extract_symbol(node, source_bytes)

            # 跳过无业务价值的简单 getter / setter
            if _is_trivial_accessor(symbol, raw_code):
                return

            signature  = _extract_signature(node, source_bytes, language)
            annotations = _extract_annotations(raw_code)
            comment    = _extract_leading_comment(raw_code)

            calls: list[str] = []
            try:
                raw_bytes = raw_code.encode("utf-8")
                calls = _extract_calls_from_tree(
                    _parse_subtree(raw_bytes, language), raw_bytes, language
                )
            except Exception:
                pass

            structured = _build_structured_text(
                raw_code    = raw_code,
                file_path   = file_path,
                class_name  = parent_class,
                method_name = symbol,
                signature   = signature,
                annotations = annotations,
                calls       = calls,
                comment     = comment,
            )

            sub = _maybe_split(
                structured, raw_code, line_start, line_end,
                "method", symbol, parent_class, signature, calls
            )
            chunks.extend(sub)
            return

        for child in node.children:
            walk(child, parent_class)

    walk(tree.root_node)
    if not chunks:
        return _sliding_window_chunks(source, file_path)
    return chunks


def _parse_subtree(code: bytes, language: str):
    """解析局部代码片段，用于提取调用（接受 bytes 以匹配 tree-sitter 字节偏移）"""
    parser = _get_parser(language)
    if parser is None:
        raise RuntimeError("no parser")
    return parser.parse(code)


def _extract_symbol(node, src: bytes) -> str:
    """tree-sitter 用字节偏移，必须从 bytes 而非 str 切片，否则多字节字符导致乱码"""
    for child in node.children:
        if child.type == "identifier":
            return src[child.start_byte:child.end_byte].decode("utf-8", "replace")
    return ""


def _extract_signature(node, src: bytes, language: str) -> str:
    """提取方法签名（不含方法体），从字节流切片避免多字节偏移错误"""
    try:
        text = src[node.start_byte:]
        brace_pos = text.find(b"{")
        if brace_pos == -1:
            return text[:200].decode("utf-8", "replace").strip()
        return text[:brace_pos].decode("utf-8", "replace").strip()[:200]
    except Exception:
        return ""


# ── 正则 fallback ──────────────────────────────────────────────────────────────

def _regex_parse_chunks(source: str, language: str, file_path: str) -> list[dict]:
    lines = source.splitlines()
    n = len(lines)

    if language == "java":
        pattern, sym_group = _JAVA_METHOD_RE, 2
    elif language in ("javascript", "typescript"):
        pattern, sym_group = _JS_FUNC_RE, 2
    else:
        return []

    method_starts: list[tuple[int, str]] = []
    for m in pattern.finditer(source):
        line_idx = source[:m.start()].count("\n")
        groups = m.groups()
        symbol = groups[sym_group - 1] if len(groups) >= sym_group else ""
        if symbol is None:
            symbol = next((g for g in groups[1:] if g and g.strip()
                           and not g.strip().startswith("async")), "")
        method_starts.append((line_idx, symbol or ""))

    if not method_starts:
        return []

    chunks: list[dict] = []
    for i, (start_idx, symbol) in enumerate(method_starts):
        if i + 1 < len(method_starts):
            raw_end = method_starts[i + 1][0]
            end_idx = raw_end - 1
            while end_idx > start_idx and not lines[end_idx].strip():
                end_idx -= 1
        else:
            end_idx = n - 1

        if language in ("java", "kotlin", "csharp", "scala", "javascript", "typescript"):
            end_idx = _find_brace_end(lines, start_idx, end_idx)

        raw_code = "\n".join(lines[start_idx: end_idx + 1])
        if not _chunk_has_body(raw_code, language):
            continue
        if _is_trivial_accessor(symbol, raw_code):
            continue

        annotations = _extract_annotations(raw_code)
        comment     = _extract_leading_comment(raw_code)
        structured  = _build_structured_text(
            raw_code    = raw_code,
            file_path   = file_path,
            class_name  = "",
            method_name = symbol,
            signature   = raw_code.splitlines()[0][:200] if raw_code else "",
            annotations = annotations,
            calls       = [],
            comment     = comment,
        )

        sub = _maybe_split(
            structured, raw_code, start_idx + 1, end_idx + 1,
            "method", symbol, "", "", []
        )
        chunks.extend(sub)

    return chunks


def _find_brace_end(lines: list[str], start: int, max_end: int) -> int:
    depth = 0
    found_open = False
    in_str_d = in_str_s = False
    for i in range(start, min(max_end + 1, len(lines))):
        j = 0
        line = lines[i]
        while j < len(line):
            ch = line[j]
            if not in_str_s and ch == '"' and (j == 0 or line[j-1] != "\\"):
                in_str_d = not in_str_d
            elif not in_str_d and ch == "'" and (j == 0 or line[j-1] != "\\"):
                in_str_s = not in_str_s
            elif not in_str_d and not in_str_s:
                if ch == "{":
                    depth += 1
                    found_open = True
                elif ch == "}":
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
    if "{" not in stripped or "}" not in stripped:
        return False
    non_trivial = [l for l in lines
                   if not l.startswith("*") and not l.startswith("//")
                   and not l.startswith("/*") and not l.startswith("@")
                   and l not in ("{", "}", "")]
    return len(non_trivial) >= 2


# ── Getter / Setter 过滤 ───────────────────────────────────────────────────────

_ACCESSOR_PREFIX_RE = re.compile(r'^(?:get|set|is|has)[A-Z]')


def _is_trivial_accessor(symbol: str, raw_code: str) -> bool:
    """
    判断是否为无业务价值的简单 getter / setter / is / has 方法，跳过以降低索引噪音。

    判定条件（同时满足）：
      1. 方法名以 get / set / is / has + 大写字母 开头
      2. 有效代码行（去掉空行、纯括号行、注释行、注解行）≤ 3 行
         典型 getter：签名行 + return 语句 = 2 行
         带单行校验的 setter：签名行 + 赋值 = 2–3 行
    """
    if not symbol or not _ACCESSOR_PREFIX_RE.match(symbol):
        return False
    effective = [
        l for l in raw_code.splitlines()
        if l.strip()
        and l.strip() not in ("{", "}")
        and not l.strip().startswith(("/", "*", "@"))
    ]
    return len(effective) <= 3


# ── maybe_split ───────────────────────────────────────────────────────────────

def _meta_header_from_structured(structured: str) -> str:
    """
    从结构化文本中提取元数据头（空行之前的 [FILE]/[CLASS]/[METHOD] 等行）。
    用于超长方法分窗时，确保每个分窗都携带语义标识，不再是"裸代码"。
    """
    lines = structured.split("\n")
    header: list[str] = []
    for line in lines:
        if not line.strip():
            break
        header.append(line)
    return "\n".join(header)


def _maybe_split(
    structured: str, raw_code: str,
    line_start: int, line_end: int,
    chunk_type: str, symbol: str,
    class_name: str, signature: str,
    calls: list[str],
) -> list[dict]:
    if _count_tokens(structured) <= settings.CHUNK_MAX_TOKENS:
        return [{
            "text":       structured,
            "raw_code":   raw_code[:800],
            "line_start": line_start,
            "line_end":   line_end,
            "chunk_type": chunk_type,
            "symbol":     symbol,
            "class_name": class_name,
            "signature":  signature,
            "calls":      calls,
        }]

    # 超长方法：提取元数据头，每个分窗都携带，避免大方法的 chunk 成为裸代码
    meta_header = _meta_header_from_structured(structured)
    header_tokens = _count_tokens(meta_header)
    # 代码窗口大小 = 总限制 - 头部占用 - 2（空行分隔符）
    code_window_tokens = max(settings.CHUNK_MAX_TOKENS - header_tokens - 2, 100)

    windows = _sliding_window_chunks(
        raw_code, "", chunk_type, base_line=line_start,
        symbol=symbol, class_name=class_name, signature=signature, calls=calls,
        max_tokens=code_window_tokens,
    )
    for w in windows:
        w["text"] = meta_header + "\n\n" + w["text"]
    return windows


# ── Sliding Window ─────────────────────────────────────────────────────────────

def _sliding_window_chunks(
    source: str, file_path: str,
    chunk_type: str = "block",
    base_line: int = 1,
    symbol: str = "",
    class_name: str = "",
    signature: str = "",
    calls: list[str] | None = None,
    max_tokens: int | None = None,   # 由 _maybe_split 传入调整后的窗口大小
) -> list[dict]:
    lines = source.splitlines()
    _max   = max_tokens if max_tokens is not None else settings.CHUNK_MAX_TOKENS
    overlap = settings.CHUNK_OVERLAP_TOKENS
    chunks  = []
    start_idx = 0

    while start_idx < len(lines):
        current_lines: list[str] = []
        current_tokens = 0
        end_idx = start_idx

        while end_idx < len(lines):
            lt = _count_tokens(lines[end_idx])
            if current_tokens + lt > _max and current_lines:
                break
            current_lines.append(lines[end_idx])
            current_tokens += lt
            end_idx += 1

        if not current_lines:
            current_lines = [lines[start_idx]]
            end_idx = start_idx + 1

        text = "\n".join(current_lines)
        chunks.append({
            "text":       text,
            "raw_code":   text[:800],
            "line_start": base_line + start_idx,
            "line_end":   base_line + end_idx - 1,
            "chunk_type": chunk_type,
            "symbol":     symbol,
            "class_name": class_name,
            "signature":  signature,
            "calls":      calls or [],
        })

        overlap_lines = max(1, overlap // 10)
        start_idx = max(end_idx - overlap_lines, start_idx + 1)

    return chunks
