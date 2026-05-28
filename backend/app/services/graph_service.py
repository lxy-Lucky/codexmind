from __future__ import annotations

"""
Graph Service
-------------
保留影响域查询，供聊天 impact 卡片（analysis_service）调用。
查询直接走 Neo4j Cypher，无需后端图算法代码。
"""

import logging

from app.core.neo4j_client import run_query
from app.models.graph import GraphNode, GraphEdge, ImpactResponse

logger = logging.getLogger(__name__)


# ── 影响域（反向图）───────────────────────────────────────────────────────────

async def get_impact(
    repo_id: str,
    symbol_id: str,
    max_depth: int = 3,
) -> ImpactResponse:
    """
    反向遍历：修改 symbol_id 对应的方法后，哪些调用者会受影响。
    按 depth 排序，高 pagerank 的节点优先展示。
    """
    max_depth = min(max_depth, 5)

    # 反向追：从 target 出发，沿 CALLS 反向找上游
    # IMPLEMENTS 作为"免费穿越"：
    #   若 target 是 sql，找 java interface 端作为附加目标
    #   若 target 是 java，找 IMPLEMENTS 指向的 sql 端作为附加目标
    rows = await run_query(
        f"""
        MATCH (target:Method {{id: $symbol_id}})
        OPTIONAL MATCH (target)-[:IMPLEMENTS]->(sql_target:Method {{repo_id: $repo_id}})
        OPTIONAL MATCH (java_target:Method {{repo_id: $repo_id}})-[:IMPLEMENTS]->(target)
        WITH target,
             collect(DISTINCT sql_target) + collect(DISTINCT java_target) AS linked
        WITH target, linked + [target] AS all_targets
        UNWIND all_targets AS t
        MATCH path = (caller:Method {{repo_id: $repo_id}})-[:CALLS*1..{max_depth}]->(t)
        WHERE caller.id <> target.id
        WITH caller, min(length(path)) AS hop
        RETURN DISTINCT
          caller.id          AS id,
          caller.name        AS name,
          caller.class_name  AS class_name,
          caller.file_path   AS file_path,
          caller.line_start  AS line_start,
          caller.line_end    AS line_end,
          coalesce(caller.chunk_type, 'method') AS chunk_type,
          coalesce(caller.pagerank, 0.0) AS pagerank,
          coalesce(caller.in_degree, size([(caller)<-[:CALLS]-() | 1])) AS in_degree,
          hop AS depth
        ORDER BY depth ASC, pagerank DESC
        LIMIT 100
        """,
        {"symbol_id": symbol_id, "repo_id": repo_id},
    )

    impact_node_ids = [r["id"] for r in rows if r.get("id")] + [symbol_id]

    edge_rows = await run_query(
        """
        MATCH (a:Method)-[r:CALLS|IMPLEMENTS]->(b:Method)
        WHERE a.id IN $node_ids AND b.id IN $node_ids
        RETURN DISTINCT a.id AS src, b.id AS tgt,
               type(r) AS edge_type,
               r.confidence AS conf, r.call_count AS cnt
        """,
        {"node_ids": impact_node_ids},
    ) if impact_node_ids else []

    nodes = [
        GraphNode(
            id         = r["id"],
            name       = r.get("name") or "",
            class_name = r.get("class_name"),
            file_path  = r.get("file_path") or "",
            line_start = r.get("line_start"),
            line_end   = r.get("line_end"),
            node_type  = "sql" if r.get("chunk_type") == "sql" else "method",
            pagerank   = r.get("pagerank") or 0.0,
            in_degree  = r.get("in_degree") or 0,
        )
        for r in rows
    ]

    edges = [
        GraphEdge(
            source     = r["src"],
            target     = r["tgt"],
            edge_type  = r.get("edge_type") or "CALLS",
            confidence = r.get("conf") or 1.0,
            call_count = r.get("cnt") or 1,
        )
        for r in edge_rows
        if r.get("src") and r.get("tgt")
    ]

    return ImpactResponse(
        nodes          = nodes,
        edges          = edges,
        center_id      = symbol_id,
        max_depth      = max_depth,
        total_affected = len(nodes),
    )
