from __future__ import annotations

"""
Analysis Service v2
-------------------
升级点：所有分析模式在调用 LLM 前，先从 Neo4j 拉取调用链上下文注入 prompt。
  - summary：目标方法 + callers(3) + callees(5) 的签名
  - bug：    目标方法 + 调用链（入参来源 + 异常传播路径）
  - deps：   直接使用 Neo4j call_graph 数据，不再让 LLM 生成 Mermaid
  - custom： 系统提示注入调用链摘要，增强问答上下文
"""

import json
import logging
import time
from typing import AsyncIterator

import ollama

from app.core.config import settings
from app.core.neo4j_client import run_query
from app.models.analysis import AnalysisRequest, AnalysisDoneEvent, ChatMessage

logger = logging.getLogger(__name__)


# ── 调用链 Context 构建 ────────────────────────────────────────────────────────

async def _build_call_context(symbol_id: str, repo_id: str) -> str:
    """
    从 Neo4j 拉取 1-hop 调用链，构造自然语言摘要注入 prompt。
    无 symbol_id 时返回空字符串（不阻断分析）。
    """
    if not symbol_id:
        return ""

    rows = await run_query(
        """
        MATCH (m:Method {id: $symbol_id})
        OPTIONAL MATCH (caller:Method {repo_id: $repo_id})-[:CALLS]->(m)
        OPTIONAL MATCH (m)-[:CALLS]->(callee:Method {repo_id: $repo_id})
        RETURN
          m.name       AS self_name,
          m.class_name AS self_class,
          m.signature  AS self_sig,
          collect(DISTINCT {
            name: caller.name, class: caller.class_name,
            file: caller.file_path, sig: caller.signature
          })[0..3] AS callers,
          collect(DISTINCT {
            name: callee.name, class: callee.class_name,
            file: callee.file_path, sig: callee.signature
          })[0..5] AS callees
        """,
        {"symbol_id": symbol_id, "repo_id": repo_id},
    )

    if not rows:
        return ""

    row = rows[0]
    lines = ["\n### 调用链上下文"]

    callers = [c for c in (row.get("callers") or []) if c.get("name")]
    callees = [c for c in (row.get("callees") or []) if c.get("name")]

    if callers:
        lines.append("**调用者（谁调用了此方法）：**")
        for c in callers:
            lines.append(f"- `{c.get('class','')}.{c['name']}` — {c.get('file','')}")

    if callees:
        lines.append("**被调用者（此方法调用了谁）：**")
        for c in callees:
            sig = c.get("sig") or c.get("name")
            lines.append(f"- `{c.get('class','')}.{c['name']}` → {sig[:80] if sig else ''}")

    return "\n".join(lines)


# ── Prompt 模板 ───────────────────────────────────────────────────────────────

_PROMPTS = {
    "summary": """\
你是一位资深软件架构师，精通 Java、Python、Go 等主流语言。
请对以下代码进行语义分析，用**中文**输出，使用 Markdown 格式：

## 功能概述
（1-2 句话说明该代码段的核心职责）

## 在整体流程中的位置
（结合调用链上下文，说明此方法在业务流程中扮演的角色）

## 核心流程
（分步骤说明执行逻辑）

## 关键设计决策
（设计模式、边界条件、值得关注之处）

---
文件：`{file_path}`（第 {line_start}-{line_end} 行）

{call_context}

```{language}
{code}
```
""",

    "bug": """\
你是一位代码安全与质量审查专家。
分析以下代码（含调用链上下文），找出所有潜在问题，重点关注：
- 空指针 / 未校验的入参
- 调用者传入的危险参数
- 被调用方法的异常未处理
- 资源泄漏、事务边界、并发问题

**严格只输出一个 JSON 数组**，不要有任何其他文字或 markdown 包裹：

[
  {{
    "severity": "Critical|Warning|Suggestion",
    "line": 行号或null,
    "title": "问题标题（20字以内）",
    "desc": "详细描述",
    "suggestion": "修复建议",
    "code_ref": "有问题的代码片段（可选）"
  }}
]

---
文件：`{file_path}`（第 {line_start}-{line_end} 行）

{call_context}

```{language}
{code}
```
""",

    # deps 模式不走 LLM，直接用 graph_service 数据，此模板保留备用
    "deps": """\
分析以下代码的依赖关系，输出 Mermaid flowchart LR。
只输出 mermaid 代码块。

{call_context}

```{language}
{code}
```
""",

    "custom_system": """\
你是一位专业的代码助手，擅长分析、解释、优化各类编程语言的代码。
请根据用户的问题，结合以下代码上下文进行回答，用**中文**输出，支持 Markdown 格式。

文件：`{file_path}`（第 {line_start}-{line_end} 行）

{call_context}

```{language}
{code}
```
""",
}


def _ext_to_lang(file_path: str) -> str:
    ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else "text"
    return {
        "java": "java", "py": "python", "ts": "typescript",
        "js": "javascript", "go": "go", "kt": "kotlin",
        "rs": "rust", "cpp": "cpp", "cs": "csharp",
        "vue": "vue", "sql": "sql", "sh": "bash",
    }.get(ext, ext)


def _build_prompt(req: AnalysisRequest, call_context: str) -> str:
    template = _PROMPTS.get(req.mode, _PROMPTS["summary"])
    language = _ext_to_lang(req.file_path)
    return template.format(
        file_path    = req.file_path,
        line_start   = req.line_start,
        line_end     = req.line_end,
        language     = language,
        code         = req.code,
        call_context = call_context,
    )


def _build_chat_messages(
    req: AnalysisRequest,
    history: list[ChatMessage],
    call_context: str,
) -> list[dict]:
    language = _ext_to_lang(req.file_path)
    system_content = _PROMPTS["custom_system"].format(
        file_path    = req.file_path,
        line_start   = req.line_start,
        line_end     = req.line_end,
        language     = language,
        code         = req.code,
        call_context = call_context,
    )
    messages: list[dict] = [{"role": "system", "content": system_content}]
    for msg in history[-20:]:
        messages.append({"role": msg.role, "content": msg.content})
    messages.append({"role": "user", "content": req.custom_prompt or ""})
    return messages


# ── SSE 流式生成 ──────────────────────────────────────────────────────────────

async def stream_analysis(
    req: AnalysisRequest,
    history: list[ChatMessage] | None = None,
) -> AsyncIterator[str]:
    """
    yield SSE 事件：
      data: {"text": "..."}
      data: {"done": true, "confidence": 0.9, "latency_ms": 1200}
    """
    t0 = time.monotonic()
    full_text: list[str] = []

    # 拉取调用链上下文（懒加载式：查询失败不阻断分析）
    call_context = ""
    try:
        call_context = await _build_call_context(
            req.symbol_id or "", req.repo_id
        )
    except Exception as e:
        logger.warning("Call context fetch failed: %s", e)

    # 构造 messages
    if req.mode == "custom":
        messages = _build_chat_messages(req, history or [], call_context)
    else:
        messages = [{"role": "user", "content": _build_prompt(req, call_context)}]

    try:
        client = ollama.AsyncClient(host=settings.OLLAMA_HOST)
        stream = await client.chat(
            model    = settings.OLLAMA_MODEL,
            messages = messages,
            stream   = True,
            options  = {"num_ctx": 8192},
        )
        async for chunk in stream:
            token = chunk["message"]["content"]
            if token:
                full_text.append(token)
                yield f"data: {json.dumps({'text': token}, ensure_ascii=False)}\n\n"

    except Exception as e:
        logger.exception("LLM stream error: %s", e)
        yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"
        return

    latency_ms = int((time.monotonic() - t0) * 1000)
    confidence = _estimate_confidence(req.mode, "".join(full_text))
    done = AnalysisDoneEvent(confidence=confidence, latency_ms=latency_ms, mode=req.mode)
    yield f"data: {json.dumps({'done': True, **done.model_dump()}, ensure_ascii=False)}\n\n"
    logger.info("Analysis done [%s] mode=%s latency=%dms", req.repo_id, req.mode, latency_ms)


def _estimate_confidence(mode: str, output: str) -> float:
    if mode == "bug":
        try:
            parsed = json.loads(output.strip())
            return 0.95 if isinstance(parsed, list) and parsed else 0.75
        except Exception:
            return 0.50
    score = min(0.95, 0.60 + len(output) / 3000)
    if "```" in output:
        score = min(0.97, score + 0.05)
    return round(score, 2)
