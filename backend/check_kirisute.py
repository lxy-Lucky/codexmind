"""
验证两件事：
1. Qdrant 里有几个 kirisute？来自哪些文件？
2. 重建每个 kirisute 的结构化文本（实际送去 embed 的内容）
"""
import asyncio
import sys
from pathlib import Path

# 把 app 加入 path
sys.path.insert(0, str(Path(__file__).parent))

from qdrant_client import AsyncQdrantClient
from qdrant_client.http.models import Filter, FieldCondition, MatchValue


async def check():
    client = AsyncQdrantClient(host="localhost", port=6333)
    cols = await client.get_collections()

    for col in cols.collections:
        result, _ = await client.scroll(
            collection_name=col.name,
            scroll_filter=Filter(must=[
                FieldCondition(key="symbol", match=MatchValue(value="kirisute"))
            ]),
            limit=20,
            with_payload=True,
            with_vectors=False,
        )
        print(f"\n=== {col.name}：找到 {len(result)} 个 kirisute ===")
        for p in result:
            pl = p.payload or {}
            print(f"\n  file : {pl.get('file_path')}")
            print(f"  lines: {pl.get('line_start')}–{pl.get('line_end')}")
            print(f"  type : {pl.get('chunk_type')}")

            # 重建结构化文本（即实际 embed 的内容）
            from app.services.parser_service import (
                _build_structured_text, _extract_leading_comment, _extract_annotations
            )
            raw = pl.get("text", "")
            comment  = _extract_leading_comment(raw)
            anns     = _extract_annotations(raw)
            structured = _build_structured_text(
                raw_code    = raw,
                file_path   = pl.get("file_path", ""),
                class_name  = pl.get("class_name", ""),
                method_name = pl.get("symbol", ""),
                signature   = "",
                annotations = anns,
                calls       = [],
                comment     = comment,
            )
            print(f"\n  --- 重建的结构化文本（embed 输入）---")
            print(structured[:600])
            print()


asyncio.run(check())
