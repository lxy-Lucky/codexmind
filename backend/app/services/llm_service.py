from __future__ import annotations

import json
import logging
import time
from typing import AsyncIterator

import ollama

from app.core.config import settings
from app.models.analysis import AnalysisRequest, AnalysisDoneEvent, ChatMessage

logger = logging.getLogger(__name__)

# ── Prompt 模板 ───────────────────────────────────────────────────────────────

_PROMPTS = {
    "summary": """\
你是一位资深软件架构师，精通 Java、Python、Go 等主流语言。
请对以下代码进行语义分析，用**中文**输出，使用 Markdown 格式：

## 功能概述
（1-2 句话说明该代码段的核心职责）

## 核心流程
（分步骤说明执行逻辑，每步 1-2 句）

## 关键设计决策
（说明值得关注的设计模式、技术选型或边界条件）

---
文件：`{file_path}`（第 {line_start}-{line_end} 行）

```{language}
{code}
```
""",

    "bug": """\
你是一位代码安全与质量审查专家。
分析以下代码，找出所有潜在问题。

**严格只输出一个 JSON 数组**，不要有任何其他文字、注释或 markdown 代码块包裹：

[
  {{
    "severity": "Critical|Warning|Suggestion",
    "line": 行号（整数，若无法确定填 null）,
    "title": "问题标题（20字以内）",
    "desc": "详细描述，说明为什么是问题",
    "suggestion": "具体修复建议",
    "code_ref": "有问题的代码片段（可选）"
  }}
]

---
文件：`{file_path}`（第 {line_start}-{line_end} 行）

```{language}
{code}
```
""",

    "deps": """\
你是一位架构分析专家。
分析以下代码的调用链和依赖关系，输出 **Mermaid flowchart LR** 格式。

要求：
- 包含方法调用关系（A --> B）
- 外部依赖用不同形状标注（DB 用圆柱 [( )]，HTTP 用菱形 {{ }}，Redis 用六边形 {{ }}）
- 只输出 mermaid 代码块，不要其他文字

---
文件：`{file_path}`

```{language}
{code}
```
""",

    # custom 模式的系统提示：注入代码上下文，用户问题单独在 messages 里
    "custom_system": """\
你是一位专业的代码助手，擅长分析、解释、优化各类编程语言的代码。
请根据用户的问题，结合以下代码上下文进行回答，用**中文**输出，支持 Markdown 格式。

代码上下文：
文件：`{file_path}`（第 {line_start}-{line_end} 行）

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


def _build_prompt(req: AnalysisRequest) -> str:
    """summary / bug / deps 模式：单轮 prompt"""
    template = _PROMPTS.get(req.mode, _PROMPTS["summary"])
    language = _ext_to_lang(req.file_path)
    return template.format(
        file_path  = req.file_path,
        line_start = req.line_start,
        line_end   = req.line_end,
        language   = language,
        code       = req.code,
    )


def _build_chat_messages(
    req: AnalysisRequest,
    history: list[ChatMessage],
) -> list[dict]:
    """
    custom 模式：构造多轮对话 messages。
    结构：
      system: 代码上下文
      [历史 user/assistant 消息...]
      user: 本次用户问题
    """
    language = _ext_to_lang(req.file_path)
    system_content = _PROMPTS["custom_system"].format(
        file_path  = req.file_path,
        line_start = req.line_start,
        line_end   = req.line_end,
        language   = language,
        code       = req.code,
    )

    messages: list[dict] = [{"role": "system", "content": system_content}]

    # 历史记录（最多保留最近 10 轮，避免 context 超限）
    for msg in history[-20:]:
        messages.append({"role": msg.role, "content": msg.content})

    # 本次用户问题
    messages.append({"role": "user", "content": req.custom_prompt or ""})

    return messages


# ── SSE 生成器 ────────────────────────────────────────────────────────────────

async def stream_analysis(
    req: AnalysisRequest,
    history: list[ChatMessage] | None = None,
) -> AsyncIterator[str]:
    """
    yield SSE：
      data: {"text": "..."}\n\n
      data: {"done": true, "confidence": 0.9, "latency_ms": 1200}\n\n
    """
    t0 = time.monotonic()
    full_text: list[str] = []

    # 构造 messages
    if req.mode == "custom":
        messages = _build_chat_messages(req, history or [])
    else:
        messages = [{"role": "user", "content": _build_prompt(req)}]

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
