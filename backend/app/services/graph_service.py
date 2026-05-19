from __future__ import annotations

"""
Graph Service
-------------
封装所有调用图查询，供 API 层调用。
所有查询直接走 Neo4j Cypher，无需后端图算法代码。
"""

import logging

from app.core.neo4j_client import run_query
from app.models.graph import (
    GraphNode, GraphEdge, GraphResponse,
    ImpactResponse, PathResponse,
)

logger = logging.getLogger(__name__)


def _row_to_node(row: dict, prefix: str = "m") -> GraphNode:
    return GraphNode(
        id         = row.get(f"{prefix}_id") or row.get("id") or "",
        name       = row.get(f"{prefix}_name") or row.get("name") or "",
        class_name = row.get(f"{prefix}_class"),
        file_path  = row.get(f"{prefix}_file") or row.get("file_path") or "",
        line_start = row.get(f"{prefix}_line_start"),
        line_end   = row.get(f"{prefix}_line_end"),
        node_type  = row.get(f"{prefix}_type") or "method",
        pagerank   = row.get(f"{prefix}_pagerank") or 0.0,
        in_degree  = row.get(f"{prefix}_in_degree") or 0,
    )


# ── 方法级调用图（N 跳）──────────────────────────────────────────────────────

async def get_method_graph(
    repo_id: str,
    symbol_id: str,
    depth: int = 2,
) -> GraphResponse:
    """
    以 symbol_id 为中心，展开 depth 跳的调用图（双向：调用者 + 被调用者）。
    """
    depth = min(depth, 4)  # 最多 4 跳，防止图爆炸

    rows = await run_query(
        """
        MATCH (center:Method {id: $symbol_id})
        CALL {
          WITH center
          MATCH path = (center)-[:CALLS*0..$depth]->(callee:Method {repo_id: $repo_id})
          RETURN nodes(path) AS ns, relationships(path) AS rs
          UNION
          WITH center
          MATCH path = (caller:Method {repo_id: $repo_id})-[:CALLS*1..$depth]->(center)
          RETURN nodes(path) AS ns, relationships(path) AS rs
        }
        UNWIND ns AS n
        WITH DISTINCT n, rs
        RETURN
          n.id          AS id,
          n.name        AS name,
          n.class_name  AS class_name,
          n.file_path   AS file_path,
          n.line_start  AS line_start,
          n.line_end    AS line_end,
          coalesce(n.pagerank, 0.0) AS pagerank,
          size([(n)<-[:CALLS]-() | 1]) AS in_degree
        """,
        {"symbol_id": symbol_id, "repo_id": repo_id, "depth": depth},
    )

    edge_rows = await run_query(
        """
        MATCH (center:Method {id: $symbol_id})
        CALL {
          WITH center
          MATCH (a:Method {repo_id: $repo_id})-[r:CALLS]->(b:Method {repo_id: $repo_id})
          WHERE (center)-[:CALLS*0..$depth]->(a) OR (a)-[:CALLS*0..$depth]->(center)
             OR (center)-[:CALLS*0..$depth]->(b) OR (b)-[:CALLS*0..$depth]->(center)
          RETURN a.id AS src, b.id AS tgt, r.confidence AS conf, r.call_count AS cnt
        }
        RETURN DISTINCT src, tgt, conf, cnt
        """,
        {"symbol_id": symbol_id, "repo_id": repo_id, "depth": depth},
    )

    nodes = [
        GraphNode(
            id         = r["id"],
            name       = r["name"] or "",
            class_name = r.get("class_name"),
            file_path  = r.get("file_path") or "",
            line_start = r.get("line_start"),
            line_end   = r.get("line_end"),
            node_type  = "method",
            pagerank   = r.get("pagerank") or 0.0,
            in_degree  = r.get("in_degree") or 0,
        )
        for r in rows
    ]

    edges = [
        GraphEdge(
            source     = r["src"],
            target     = r["tgt"],
            confidence = r.get("conf") or 1.0,
            call_count = r.get("cnt") or 1,
        )
        for r in edge_rows
        if r.get("src") and r.get("tgt")
    ]

    return GraphResponse(
        nodes     = nodes,
        edges     = edges,
        center_id = symbol_id,
        depth     = depth,
    )


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

    rows = await run_query(
        """
        MATCH (target:Method {id: $symbol_id})
        MATCH path = (caller:Method {repo_id: $repo_id})-[:CALLS*1..$depth]->(target)
        WITH caller, length(path) AS hop
        RETURN DISTINCT
          caller.id          AS id,
          caller.name        AS name,
          caller.class_name  AS class_name,
          caller.file_path   AS file_path,
          caller.line_start  AS line_start,
          caller.line_end    AS line_end,
          coalesce(caller.pagerank, 0.0) AS pagerank,
          size([(caller)<-[:CALLS]-() | 1]) AS in_degree,
          min(hop) AS depth
        ORDER BY depth ASC, pagerank DESC
        LIMIT 100
        """,
        {"symbol_id": symbol_id, "repo_id": repo_id, "depth": max_depth},
    )

    edge_rows = await run_query(
        """
        MATCH (target:Method {id: $symbol_id})
        MATCH (a:Method {repo_id: $repo_id})-[r:CALLS]->(b:Method {repo_id: $repo_id})
        WHERE (a)-[:CALLS*1..$depth]->(target) AND (b)-[:CALLS*0..$depth]->(target)
        RETURN DISTINCT a.id AS src, b.id AS tgt,
               r.confidence AS conf, r.call_count AS cnt
        LIMIT 200
        """,
        {"symbol_id": symbol_id, "repo_id": repo_id, "depth": max_depth},
    )

    nodes = [
        GraphNode(
            id         = r["id"],
            name       = r.get("name") or "",
            class_name = r.get("class_name"),
            file_path  = r.get("file_path") or "",
            line_start = r.get("line_start"),
            line_end   = r.get("line_end"),
            node_type  = "method",
            pagerank   = r.get("pagerank") or 0.0,
            in_degree  = r.get("in_degree") or 0,
        )
        for r in rows
    ]

    edges = [
        GraphEdge(
            source     = r["src"],
            target     = r["tgt"],
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


# ── 类级依赖图 ────────────────────────────────────────────────────────────────

async def get_class_graph(repo_id: str, class_name: str | None = None) -> GraphResponse:
    """
    类级别依赖图：聚合方法调用边 → 类与类之间的调用关系。
    class_name 为 None 时返回整个 repo 的类图。
    """
    params: dict = {"repo_id": repo_id}
    where_clause = ""
    if class_name:
        where_clause = "WHERE c1.name = $class_name OR c2.name = $class_name"
        params["class_name"] = class_name

    rows = await run_query(
        f"""
        MATCH (m1:Method {{repo_id: $repo_id}})-[:CALLS]->(m2:Method {{repo_id: $repo_id}})
        MATCH (m1)-[:BELONGS_TO]->(c1:Class {{repo_id: $repo_id}})
        MATCH (m2)-[:BELONGS_TO]->(c2:Class {{repo_id: $repo_id}})
        WHERE c1 <> c2
        {where_clause}
        WITH c1, c2, count(*) AS weight
        RETURN
          c1.id        AS src_id,
          c1.name      AS src_name,
          c1.file_path AS src_file,
          c2.id        AS tgt_id,
          c2.name      AS tgt_name,
          c2.file_path AS tgt_file,
          weight
        ORDER BY weight DESC
        LIMIT 200
        """,
        params,
    )

    node_map: dict[str, GraphNode] = {}
    edges: list[GraphEdge] = []

    for r in rows:
        for nid, nname, nfile in [
            (r["src_id"], r["src_name"], r["src_file"]),
            (r["tgt_id"], r["tgt_name"], r["tgt_file"]),
        ]:
            if nid and nid not in node_map:
                node_map[nid] = GraphNode(
                    id        = nid,
                    name      = nname or "",
                    file_path = nfile or "",
                    node_type = "class",
                )
        if r.get("src_id") and r.get("tgt_id"):
            edges.append(GraphEdge(
                source     = r["src_id"],
                target     = r["tgt_id"],
                call_count = r.get("weight") or 1,
            ))

    return GraphResponse(
        nodes     = list(node_map.values()),
        edges     = edges,
        center_id = "",
        depth     = 1,
    )


# ── 两点间最短路径 ─────────────────────────────────────────────────────────────

async def get_call_path(
    repo_id: str,
    from_symbol_id: str,
    to_symbol_id: str,
) -> PathResponse:
    """查找两个方法之间的最短调用路径"""
    rows = await run_query(
        """
        MATCH (from:Method {id: $from_id}),
              (to:Method   {id: $to_id})
        MATCH path = shortestPath((from)-[:CALLS*1..8]->(to))
        UNWIND nodes(path) AS n
        RETURN DISTINCT
          n.id         AS id,
          n.name       AS name,
          n.class_name AS class_name,
          n.file_path  AS file_path,
          n.line_start AS line_start,
          n.line_end   AS line_end,
          length(path) AS path_length
        """,
        {"from_id": from_symbol_id, "to_id": to_symbol_id},
    )

    if not rows:
        return PathResponse(
            nodes     = [],
            edges     = [],
            from_id   = from_symbol_id,
            to_id     = to_symbol_id,
            path_length = 0,
            found     = False,
        )

    path_length = rows[0].get("path_length") or 0
    nodes = [
        GraphNode(
            id         = r["id"],
            name       = r.get("name") or "",
            class_name = r.get("class_name"),
            file_path  = r.get("file_path") or "",
            line_start = r.get("line_start"),
            line_end   = r.get("line_end"),
            node_type  = "method",
        )
        for r in rows
    ]

    # 重建边（顺序相邻节点之间）
    edges = [
        GraphEdge(source=nodes[i].id, target=nodes[i + 1].id)
        for i in range(len(nodes) - 1)
    ]

    return PathResponse(
        nodes       = nodes,
        edges       = edges,
        from_id     = from_symbol_id,
        to_id       = to_symbol_id,
        path_length = path_length,
        found       = True,
    )
