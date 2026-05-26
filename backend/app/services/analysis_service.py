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


# ── Token 估算与截断 ────────────────────────────────────────────────────────────

def _estimate_tokens(text: str) -> int:
    """保守估算：每 3 个字符约 1 个 token（兼容 CJK + ASCII 混合场景）。"""
    return len(text) // 3


def _truncate_code(code: str, max_tokens: int = 20000) -> tuple[str, bool]:
    """
    按行边界截断代码，确保不超过 max_tokens 估算值。
    返回 (截断后代码, 是否发生截断)。
    """
    if _estimate_tokens(code) <= max_tokens:
        return code, False
    char_budget = max_tokens * 3
    lines = code.split('\n')
    result: list[str] = []
    used = 0
    for line in lines:
        used += len(line) + 1  # +1 for newline
        if used > char_budget:
            break
        result.append(line)
    return '\n'.join(result), True

logger = logging.getLogger(__name__)


# ── 调用链 Context 构建 ────────────────────────────────────────────────────────

# 调用链段落的多语言文案
_CALL_CONTEXT_LABELS = {
    "zh": {
        "title":   "\n### 调用链上下文",
        "callers": "**调用者（谁调用了此方法）：**",
        "callees": "**被调用者（此方法调用了谁）：**",
    },
    "ja": {
        "title":   "\n### 呼び出し関係",
        "callers": "**呼び出し元（このメソッドを呼ぶ）：**",
        "callees": "**呼び出し先（このメソッドが呼ぶ）：**",
    },
    "en": {
        "title":   "\n### Call chain context",
        "callers": "**Callers (who calls this method):**",
        "callees": "**Callees (what this method calls):**",
    },
}


async def _build_call_context(symbol_id: str, repo_id: str, locale: str = "en") -> str:
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
    labels = _CALL_CONTEXT_LABELS.get(locale, _CALL_CONTEXT_LABELS["en"])
    lines = [labels["title"]]

    callers = [c for c in (row.get("callers") or []) if c.get("name")]
    callees = [c for c in (row.get("callees") or []) if c.get("name")]

    if callers:
        lines.append(labels["callers"])
        for c in callers:
            lines.append(f"- `{c.get('class','')}.{c['name']}` — {c.get('file','')}")

    if callees:
        lines.append(labels["callees"])
        for c in callees:
            sig = c.get("sig") or c.get("name")
            lines.append(f"- `{c.get('class','')}.{c['name']}` → {sig[:80] if sig else ''}")

    return "\n".join(lines)


# ── Prompt 模板 ───────────────────────────────────────────────────────────────

# 多语言 prompt 模板。LLM 会按 locale 用对应语言回复。
_PROMPTS_BY_LOCALE: dict[str, dict[str, str]] = {
    "zh": {
        "summary": """\
你是一位资深软件架构师，精通 Java、Python、Go 等主流语言。
请对以下代码进行语义分析，**必须用中文回答**，使用 Markdown 格式：

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
你是一位代码安全与质量审查专家。**所有自然语言字段必须用中文。**
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
        "deps": """\
你是一位架构分析专家。分析以下代码的调用链和依赖关系，输出 **Mermaid flowchart LR** 格式。

要求：
- 包含方法调用关系（A --> B）
- 外部依赖用不同形状标注（DB 用圆柱 [( )]，HTTP 用菱形 {{ }}）
- 只输出 mermaid 代码块，不要其他文字

{call_context}

```{language}
{code}
```
""",
        "custom_system": """\
你是一位专业的代码助手，擅长分析、解释、优化各类编程语言的代码。
请根据用户的问题，结合以下代码上下文进行回答，**必须用中文回答**，支持 Markdown 格式。

文件：`{file_path}`（第 {line_start}-{line_end} 行）

{call_context}

```{language}
{code}
```
""",
        "docs": """\
你是一位技术文档工程师。为以下代码生成结构清晰的开发文档，**必须用中文**，直接输出 Markdown：

## 功能概述
（1-3 句话描述核心职责）

## 方法说明

| 方法 | 功能 | 参数 | 返回值 |
|------|------|------|--------|
（逐行列出所有方法）

## 关键依赖
（调用的外部服务、数据库组件、第三方库等）

## 注意事项
（边界条件、常见问题、使用限制）

---
文件：`{file_path}`（第 {line_start}-{line_end} 行）

{call_context}

```{language}
{code}
```
""",
    },

    "ja": {
        "summary": """\
あなたはベテランのソフトウェアアーキテクトで、Java、Python、Go などの主要言語に精通しています。
以下のコードをセマンティック分析し、**必ず日本語で回答**してください。Markdown 形式で出力します。

## 機能概要
（このコードセグメントの中核となる責任を 1〜2 文で）

## 全体フロー内での位置付け
（呼び出し関係を踏まえ、このメソッドがビジネスフロー内で果たす役割）

## 主要処理フロー
（実行ロジックをステップごとに説明）

## 重要な設計判断
（デザインパターン、境界条件、注目すべき点）

---
ファイル：`{file_path}`（{line_start}-{line_end} 行）

{call_context}

```{language}
{code}
```
""",
        "bug": """\
あなたはコードのセキュリティと品質のレビュー専門家です。**自然言語フィールドは必ず日本語で記述してください。**
以下のコード（呼び出し関係を含む）を分析し、潜在的な問題をすべて検出します。特に注意：
- NULL 参照 / 未検証の引数
- 呼び出し元から渡される危険なパラメータ
- 被呼び出しメソッドの未処理例外
- リソースリーク、トランザクション境界、並行性の問題

**JSON 配列のみを厳密に出力**してください。他のテキストや markdown 囲みは禁止：

[
  {{
    "severity": "Critical|Warning|Suggestion",
    "line": 行番号 または null,
    "title": "問題タイトル（20 文字以内）",
    "desc": "詳細説明",
    "suggestion": "修正の提案",
    "code_ref": "問題のコード片（任意）"
  }}
]

---
ファイル：`{file_path}`（{line_start}-{line_end} 行）

{call_context}

```{language}
{code}
```
""",
        "deps": """\
あなたはアーキテクチャ分析の専門家です。以下のコードの呼び出し関係と依存関係を分析し、**Mermaid flowchart LR** 形式で出力してください。

要件：
- メソッド呼び出し関係を含める（A --> B）
- 外部依存はシェイプで区別（DB はシリンダー [( )]、HTTP はひし形 {{ }}）
- mermaid コードブロックのみを出力し、他のテキストは出力しない

{call_context}

```{language}
{code}
```
""",
        "custom_system": """\
あなたはプロのコードアシスタントで、さまざまなプログラミング言語のコードを分析・解説・最適化することが得意です。
ユーザーの質問に対し、以下のコードコンテキストを踏まえて回答してください。**必ず日本語で回答**し、Markdown 形式に対応します。

ファイル：`{file_path}`（{line_start}-{line_end} 行）

{call_context}

```{language}
{code}
```
""",
        "docs": """\
あなたは技術文書エンジニアです。以下のコードについて構造化された開発ドキュメントを生成してください。**必ず日本語で**、Markdown 形式で直接出力してください：

## 機能概要
（中核となる責任を 1-3 文で）

## メソッド説明

| メソッド | 機能 | パラメータ | 戻り値 |
|----------|------|------------|--------|
（全メソッドを列挙）

## 主要な依存関係
（外部サービス、DB コンポーネント、サードパーティ）

## 注意事項
（境界条件、よくある問題、使用制限）

---
ファイル：`{file_path}`（{line_start}-{line_end} 行）

{call_context}

```{language}
{code}
```
""",
    },

    "en": {
        "summary": """\
You are a senior software architect proficient in Java, Python, Go, and other mainstream languages.
Perform a semantic analysis of the code below. **Always respond in English** using Markdown:

## Overview
(1-2 sentences describing the core responsibility of this snippet)

## Position in the overall flow
(Using the call-chain context, describe this method's role in the business flow)

## Core logic
(Walk through the execution step-by-step)

## Key design decisions
(Design patterns, edge cases, things worth noting)

---
File: `{file_path}` (lines {line_start}-{line_end})

{call_context}

```{language}
{code}
```
""",
        "bug": """\
You are a code security and quality reviewer. **All natural-language fields must be in English.**
Analyze the following code (with call-chain context) and find every potential issue. Focus on:
- Null references / unvalidated inputs
- Dangerous parameters from callers
- Unhandled exceptions in callees
- Resource leaks, transaction boundaries, concurrency

**Output strictly a single JSON array** — no extra text, no markdown fence:

[
  {{
    "severity": "Critical|Warning|Suggestion",
    "line": line_number or null,
    "title": "Short title (within 20 chars)",
    "desc": "Detailed description",
    "suggestion": "How to fix",
    "code_ref": "Problematic snippet (optional)"
  }}
]

---
File: `{file_path}` (lines {line_start}-{line_end})

{call_context}

```{language}
{code}
```
""",
        "deps": """\
You are an architecture analyst. Analyze the call chain and dependencies of the code below and output a **Mermaid flowchart LR**.

Requirements:
- Include method-to-method calls (A --> B)
- Distinguish external dependencies by shape (DB as cylinder [( )], HTTP as rhombus {{ }})
- Output only the mermaid code block, nothing else

{call_context}

```{language}
{code}
```
""",
        "custom_system": """\
You are a professional code assistant skilled at analyzing, explaining, and optimizing code in any language.
Answer the user's question using the code context below. **Always respond in English** and support Markdown.

File: `{file_path}` (lines {line_start}-{line_end})

{call_context}

```{language}
{code}
```
""",
        "docs": """\
You are a technical documentation engineer. Generate structured developer documentation for the code below. **Always respond in English**, output Markdown directly:

## Overview
(1-3 sentences describing the core responsibility)

## Method Reference

| Method | Purpose | Parameters | Return |
|--------|---------|------------|--------|
(List every method)

## Key Dependencies
(External services, DB components, third-party libraries)

## Notes
(Edge cases, common pitfalls, usage constraints)

---
File: `{file_path}` (lines {line_start}-{line_end})

{call_context}

```{language}
{code}
```
""",
    },
}


def _get_prompts(locale: str) -> dict[str, str]:
    return _PROMPTS_BY_LOCALE.get(locale, _PROMPTS_BY_LOCALE["en"])


def _ext_to_lang(file_path: str) -> str:
    ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else "text"
    return {
        "java": "java", "py": "python", "ts": "typescript",
        "js": "javascript", "go": "go", "kt": "kotlin",
        "rs": "rust", "cpp": "cpp", "cs": "csharp",
        "vue": "vue", "sql": "sql", "sh": "bash",
    }.get(ext, ext)


def _build_prompt(req: AnalysisRequest, call_context: str, locale: str = "en") -> str:
    prompts = _get_prompts(locale)
    template = prompts.get(req.mode, prompts["summary"])
    language = _ext_to_lang(req.file_path)

    # 截断代码：为系统 prompt + call_context + 输出预留 ~4000 token
    code, was_truncated = _truncate_code(req.code, max_tokens=24000)
    if was_truncated:
        code += "\n\n... [代码过长，已截断至此处]"

    return template.format(
        file_path    = req.file_path,
        line_start   = req.line_start,
        line_end     = req.line_end,
        language     = language,
        code         = code,
        call_context = call_context,
    )


def _build_chat_messages(
    req: AnalysisRequest,
    history: list[ChatMessage],
    call_context: str,
    locale: str = "en",
) -> list[dict]:
    prompts = _get_prompts(locale)
    language = _ext_to_lang(req.file_path)

    # 对话模式代码截断：为历史 + 回复留更多空间（8000 token 给代码上下文）
    code, _ = _truncate_code(req.code, max_tokens=8000)

    system_content = prompts["custom_system"].format(
        file_path    = req.file_path,
        line_start   = req.line_start,
        line_end     = req.line_end,
        language     = language,
        code         = code,
        call_context = call_context,
    )
    messages: list[dict] = [{"role": "system", "content": system_content}]

    # 按 token 预算裁剪历史（保留最新的，预算 8000 token）
    HISTORY_TOKEN_BUDGET = 8000
    trimmed: list[ChatMessage] = []
    used = 0
    for msg in reversed(history):
        tokens = _estimate_tokens(msg.content)
        if used + tokens > HISTORY_TOKEN_BUDGET:
            break
        trimmed.insert(0, msg)
        used += tokens

    for msg in trimmed:
        messages.append({"role": msg.role, "content": msg.content})
    messages.append({"role": "user", "content": req.custom_prompt or ""})
    return messages


# ── SSE 流式生成 ──────────────────────────────────────────────────────────────

async def stream_analysis(
    req: AnalysisRequest,
    history: list[ChatMessage] | None = None,
    locale: str = "en",
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
            req.symbol_id or "", req.repo_id, locale
        )
    except Exception as e:
        logger.warning("Call context fetch failed: %s", e)

    # 构造 messages
    if req.mode == "custom":
        messages = _build_chat_messages(req, history or [], call_context, locale)
    else:
        messages = [{"role": "user", "content": _build_prompt(req, call_context, locale)}]

    try:
        client = ollama.AsyncClient(host=settings.OLLAMA_HOST)
        stream = await client.chat(
            model    = settings.OLLAMA_MODEL,
            messages = messages,
            stream   = True,
            options  = {"num_ctx": 32768},
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


async def generate_file_docs(
    repo_id: str,
    file_path: str,
    locale: str = "en",
) -> AsyncIterator[str]:
    """
    整文件文档生成（批处理）：
      1. 从 Neo4j 按 file_path 查找所有 Class 节点
      2. 按类逐一调用 LLM，流式 yield SSE 事件
      3. 每个类开始前 yield progress 事件

    SSE 事件格式：
      {"text": "..."}                  普通文本 chunk
      {"progress": {"current": N, "total": M, "class_name": "..."}}  进度
      {"done": true, "total": N}       全部完成
      {"error": "..."}                 错误
    """
    # 路径规范化，用于多方式匹配
    norm_path  = file_path.replace("\\", "/")
    filename   = norm_path.split("/")[-1]
    file_suffix = "/" + filename  # 带前缀斜杠防止误匹配

    classes = await run_query(
        """
        MATCH (c:Class {repo_id: $repo_id})
        WHERE c.file_path = $file_path
           OR c.file_path = $norm_path
           OR c.file_path ENDS WITH $file_suffix
        RETURN c.id        AS id,
               c.name      AS name,
               c.file_path AS full_path,
               c.line_start AS line_start,
               c.line_end   AS line_end
        ORDER BY c.line_start
        """,
        {
            "repo_id":     repo_id,
            "file_path":   file_path,
            "norm_path":   norm_path,
            "file_suffix": file_suffix,
        },
    )

    if not classes:
        yield f"data: {json.dumps({'error': 'No class info found for this file. Please index the repository first.'}, ensure_ascii=False)}\n\n"
        return

    total = len(classes)

    # 读取源文件（用 Neo4j 存储的绝对路径）
    full_path = (classes[0].get("full_path") or file_path).replace("\\", "/")
    try:
        with open(full_path, "r", encoding="utf-8", errors="replace") as fh:
            all_lines = fh.readlines()
    except Exception as exc:
        logger.exception("Cannot read file %s: %s", full_path, exc)
        yield f"data: {json.dumps({'error': f'Cannot read file: {exc}'}, ensure_ascii=False)}\n\n"
        return

    # 文件头
    display_name = filename
    header_text  = f"# {display_name} 文档\n\n> 共 {total} 个类 · 自动生成\n\n---\n\n"
    yield f"data: {json.dumps({'text': header_text}, ensure_ascii=False)}\n\n"

    prompts = _get_prompts(locale)
    docs_template = prompts.get("docs", prompts["summary"])
    language = _ext_to_lang(file_path)

    for idx, cls in enumerate(classes, 1):
        class_name = cls.get("name") or f"Class_{idx}"
        line_start  = max(1, (cls.get("line_start") or 1))
        line_end    = cls.get("line_end") or len(all_lines)

        # 进度事件
        yield f"data: {json.dumps({'progress': {'current': idx, 'total': total, 'class_name': class_name}}, ensure_ascii=False)}\n\n"

        # 截取类代码（0-indexed slice）
        class_code = "".join(all_lines[line_start - 1 : line_end])
        class_code, _ = _truncate_code(class_code, max_tokens=12000)

        prompt_text = docs_template.format(
            file_path    = file_path,
            line_start   = line_start,
            line_end     = line_end,
            language     = language,
            code         = class_code,
            call_context = "",
        )

        # 类标题
        yield f"data: {json.dumps({'text': f'## `{class_name}`\\n\\n'}, ensure_ascii=False)}\n\n"

        # 流式 LLM 调用
        try:
            client = ollama.AsyncClient(host=settings.OLLAMA_HOST)
            stream = await client.chat(
                model    = settings.OLLAMA_MODEL,
                messages = [{"role": "user", "content": prompt_text}],
                stream   = True,
                options  = {"num_ctx": 16384},
            )
            async for chunk in stream:
                token = chunk["message"]["content"]
                if token:
                    yield f"data: {json.dumps({'text': token}, ensure_ascii=False)}\n\n"
        except Exception as exc:
            logger.exception("Docs LLM error for class %s: %s", class_name, exc)
            yield f"data: {json.dumps({'text': f'\\n> ⚠ 生成失败: {exc}\\n'}, ensure_ascii=False)}\n\n"

        # 类分隔符
        yield f"data: {json.dumps({'text': '\\n\\n---\\n\\n'}, ensure_ascii=False)}\n\n"

    yield f"data: {json.dumps({'done': True, 'total': total}, ensure_ascii=False)}\n\n"
    logger.info("File docs done [%s] file=%s classes=%d", repo_id, file_path, total)


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
