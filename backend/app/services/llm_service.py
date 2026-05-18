from __future__ import annotations

import json
import logging
import time
from typing import AsyncIterator

import ollama

from app.core.config import settings
from app.models.analysis import AnalysisRequest, AnalysisDoneEvent

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
}


def _build_prompt(req: AnalysisRequest) -> str:
    template = _PROMPTS.get(req.mode, _PROMPTS["summary"])
    # 推断语言（简单从文件扩展名）
    ext = req.file_path.rsplit(".", 1)[-1] if "." in req.file_path else "text"
    ext_lang_map = {
        "java": "java", "py": "python", "ts": "typescript",
        "js": "javascript", "go": "go", "kt": "kotlin",
        "rs": "rust", "cpp": "cpp", "cs": "csharp",
    }
    language = ext_lang_map.get(ext, ext)
    return template.format(
        file_path  = req.file_path,
        line_start = req.line_start,
        line_end   = req.line_end,
        language   = language,
        code       = req.code,
    )


# ── SSE 生成器 ────────────────────────────────────────────────────────────────

async def stream_analysis(req: AnalysisRequest) -> AsyncIterator[str]:
    """
    yield SSE 格式的字符串：
      data: {"text": "..."}\\n\\n
      ...
      data: {"done": true, "confidence": 0.9, "latency_ms": 1200}\\n\\n
    """
    prompt = _build_prompt(req)
    t0 = time.monotonic()
    full_text = []

    try:
        client = ollama.AsyncClient(host=settings.OLLAMA_HOST)
        stream = await client.chat(
            model   = settings.OLLAMA_MODEL,
            messages= [{"role": "user", "content": prompt}],
            stream  = True,
            options = {"num_ctx": 8192},
        )

        async for chunk in stream:
            token = chunk["message"]["content"]
            if token:
                full_text.append(token)
                payload = json.dumps({"text": token}, ensure_ascii=False)
                yield f"data: {payload}\n\n"

    except Exception as e:
        logger.exception("LLM stream error: %s", e)
        err = json.dumps({"error": str(e)}, ensure_ascii=False)
        yield f"data: {err}\n\n"
        return

    latency_ms = int((time.monotonic() - t0) * 1000)
    confidence = _estimate_confidence(req.mode, "".join(full_text))

    done_event = AnalysisDoneEvent(
        confidence = confidence,
        latency_ms = latency_ms,
        mode       = req.mode,
    )
    done_payload = json.dumps(
        {"done": True, **done_event.model_dump()},
        ensure_ascii=False,
    )
    yield f"data: {done_payload}\n\n"
    logger.info("Analysis done [%s] mode=%s latency=%dms", req.repo_id, req.mode, latency_ms)


def _estimate_confidence(mode: str, output: str) -> float:
    """
    简单的置信度启发：
    - bug 模式：输出能解析为有效 JSON 数组 → 高
    - 其他：按输出长度打分，有代码引用 → 加分
    """
    if mode == "bug":
        try:
            parsed = json.loads(output.strip())
            if isinstance(parsed, list) and len(parsed) > 0:
                return 0.95
            return 0.75
        except Exception:
            return 0.50

    length = len(output)
    score = min(0.95, 0.60 + length / 3000)
    if "```" in output:
        score = min(0.97, score + 0.05)
    return round(score, 2)
