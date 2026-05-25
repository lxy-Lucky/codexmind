from __future__ import annotations

"""
MyBatis Mapper XML 解析器
------------------------
从 *Mapper.xml 中抽取：
  - <mapper namespace="com.example.UserMapper">
  - <select|insert|update|delete id="selectById" ...>
  - <sql id="..."> 片段（resultMap / 通用片段）
  - <include refid="...">  → 调用关系

输出与 Java parser_service 同构的 chunk 结构，方便 indexer 复用同一条
"Pass1 写 Method 节点 + Pass2 写 CALLS 边" 的管线。

为每条 SQL 建模成一个 Method 节点：
  class_name  = mapper namespace 简名（如 UserMapper）
  method_name = SQL id（如 selectById）
  chunk_type  = "sql"
  signature   = "<select id='selectById'> [resultType=User]"
  calls       = [refid1, refid2, ...]   ← 用于 <include refid> 连边

外部连接关系（Java interface → XML SQL）由 indexer 在 Pass1.5 处理。
"""

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)


# ── 正则定义 ──────────────────────────────────────────────────────────────────
# 用正则解析 XML 而非 ElementTree 的原因：
#   1. Mapper XML 经常含 OGNL 表达式（${} / #{}），不是严格 XML
#   2. 我们只关心 5 类标签 + 它们的 id/namespace 属性，不需要完整 DOM
#   3. 行号定位简单（直接数 \n）

_NAMESPACE_RE = re.compile(
    r'<\s*mapper\b[^>]*\bnamespace\s*=\s*"([^"]+)"',
    re.IGNORECASE,
)

# 匹配 <select|insert|update|delete|sql ... id="xxx" ...>，开始标签
_STMT_OPEN_RE = re.compile(
    r'<\s*(select|insert|update|delete|sql)\b'
    r'(?P<attrs>[^>]*)>',
    re.IGNORECASE,
)

_ID_ATTR_RE = re.compile(r'\bid\s*=\s*"([^"]+)"', re.IGNORECASE)
_RESULT_TYPE_RE = re.compile(r'\b(resultType|resultMap)\s*=\s*"([^"]+)"', re.IGNORECASE)
_PARAM_TYPE_RE  = re.compile(r'\bparameterType\s*=\s*"([^"]+)"', re.IGNORECASE)

# <include refid="..."> 表示对其他 SQL 片段的引用，用于建 CALLS 边
_INCLUDE_RE = re.compile(r'<\s*include\b[^>]*\brefid\s*=\s*"([^"]+)"', re.IGNORECASE)


def _short_name(fq_name: str) -> str:
    """com.example.UserMapper → UserMapper"""
    return fq_name.rsplit(".", 1)[-1] if "." in fq_name else fq_name


def _line_at(text: str, offset: int) -> int:
    """字符 offset → 1-indexed 行号"""
    return text.count("\n", 0, offset) + 1


def _find_close_tag(text: str, tag: str, start: int) -> int:
    """
    从 start 位置开始找 </tag>，返回 close-tag 结束位置（exclusive）。
    支持嵌套同名标签（虽然 mapper 里不常见，<sql> 内可包另一 <sql>）。
    找不到返回 len(text)。
    """
    depth = 1
    open_re  = re.compile(rf'<\s*{tag}\b[^>]*>',  re.IGNORECASE)
    close_re = re.compile(rf'<\s*/\s*{tag}\s*>',  re.IGNORECASE)
    pos = start
    while depth > 0:
        m_open  = open_re.search(text, pos)
        m_close = close_re.search(text, pos)
        if not m_close:
            return len(text)
        if m_open and m_open.start() < m_close.start():
            depth += 1
            pos = m_open.end()
        else:
            depth -= 1
            pos = m_close.end()
    return pos


# ── 主接口 ────────────────────────────────────────────────────────────────────

def parse_mapper_xml(source: str, file_path: str) -> list[dict]:
    """
    返回 chunk 列表，与 Java parser_service.parse_chunks 输出结构兼容。
    非 mapper 文件（无 <mapper namespace>）返回 []，由 indexer 兜底走 sliding-window。
    """
    ns_match = _NAMESPACE_RE.search(source)
    if not ns_match:
        return []   # 不是 MyBatis mapper，走通用 xml 索引

    namespace = ns_match.group(1).strip()
    class_short = _short_name(namespace)

    chunks: list[dict] = []
    for m in _STMT_OPEN_RE.finditer(source):
        tag   = m.group(1).lower()
        attrs = m.group("attrs")
        id_m  = _ID_ATTR_RE.search(attrs)
        if not id_m:
            continue   # 无 id 的 statement 不可寻址，跳过
        sql_id = id_m.group(1).strip()

        # 找闭合标签，截出整段 SQL
        close_end = _find_close_tag(source, tag, m.end())
        raw_code  = source[m.start(): close_end]

        line_start = _line_at(source, m.start())
        line_end   = _line_at(source, close_end - 1)

        # signature: <tag id='...'> [resultType=...]
        sig_parts = [f"<{tag} id='{sql_id}'>"]
        rt = _RESULT_TYPE_RE.search(attrs)
        if rt:
            sig_parts.append(f"{rt.group(1)}={rt.group(2)}")
        pt = _PARAM_TYPE_RE.search(attrs)
        if pt:
            sig_parts.append(f"parameterType={pt.group(1)}")
        signature = " ".join(sig_parts)[:300]

        # calls = <include refid> 列表（XML 内部的 SQL 片段调用）
        calls = list(dict.fromkeys(_INCLUDE_RE.findall(raw_code)))

        # 结构化文本：[FILE]/[CLASS]/[METHOD]/[SIGNATURE] + SQL 原文
        structured_lines = [
            f"[FILE] {file_path}",
            f"[CLASS] {class_short}",
            f"[METHOD] {sql_id}",
            f"[SIGNATURE] {signature}",
            f"[NAMESPACE] {namespace}",
        ]
        if calls:
            structured_lines.append(f"[CALLS] {', '.join(calls)}")
        structured_lines.append("")   # 空行分隔
        structured_lines.append(raw_code)
        structured = "\n".join(structured_lines)

        chunks.append({
            "text":              structured,
            "raw_code":          raw_code[:3000],
            "line_start":        line_start,
            "line_end":          line_end,
            "method_line_start": line_start,
            "method_line_end":   line_end,
            "chunk_type":        "sql",
            "symbol":            sql_id,
            "class_name":        class_short,
            "signature":         signature,
            "calls":             calls,
            "is_primary":        True,
            # 给 indexer 用：建 IMPLEMENTS 边时需要 namespace 完整名定位 Java interface
            "_mapper_namespace": namespace,
            "_mapper_tag":       tag,
        })

    logger.info("Mapper XML parsed: %s namespace=%s, %d statements",
                file_path, namespace, len(chunks))
    return chunks
