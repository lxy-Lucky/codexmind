"""轻量 i18n：支持 zh / ja / en。

用法：
    from app.core.i18n import t, get_locale

    @router.get("/foo")
    async def foo(locale: str = Depends(get_locale)):
        return {"msg": t("common.ok", locale)}
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from fastapi import Header, Query

Locale = Literal["zh", "ja", "en"]
SUPPORTED: tuple[Locale, ...] = ("zh", "ja", "en")
DEFAULT: Locale = "en"

_LOCALES_DIR = Path(__file__).parent / "locales"
_MESSAGES: dict[str, dict] = {}


def _load() -> None:
    for lng in SUPPORTED:
        path = _LOCALES_DIR / f"{lng}.json"
        with path.open("r", encoding="utf-8") as f:
            _MESSAGES[lng] = json.load(f)


_load()


def _parse_accept_language(header: str | None) -> Locale | None:
    if not header:
        return None
    # "zh-CN,zh;q=0.9,en;q=0.8" -> 取首个匹配
    for part in header.split(","):
        tag = part.split(";")[0].strip().lower()
        if tag.startswith("zh"):
            return "zh"
        if tag.startswith("ja"):
            return "ja"
        if tag.startswith("en"):
            return "en"
    return None


async def get_locale(
    lang: str | None = Query(default=None, description="zh|ja|en"),
    x_lang: str | None = Header(default=None, alias="X-Lang"),
    accept_language: str | None = Header(default=None, alias="Accept-Language"),
) -> Locale:
    """FastAPI 依赖：?lang= > X-Lang > Accept-Language > default."""
    for cand in (lang, x_lang):
        if cand and cand.lower() in SUPPORTED:
            return cand.lower()  # type: ignore[return-value]
    return _parse_accept_language(accept_language) or DEFAULT


def t(key: str, locale: Locale = DEFAULT, **vars: object) -> str:
    """按点号路径取文案，缺失则回退英文，再缺失则返回 key 本身。"""
    for lng in (locale, DEFAULT):
        node: object = _MESSAGES.get(lng, {})
        for seg in key.split("."):
            if isinstance(node, dict) and seg in node:
                node = node[seg]
            else:
                node = None
                break
        if isinstance(node, str):
            try:
                return node.format(**vars) if vars else node
            except (KeyError, IndexError):
                return node
    return key
