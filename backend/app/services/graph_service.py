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
    注意：Cypher 变长路径范围不接受参数，depth 直接格式化进查询字符串。

    两步策略：
      Step 1 - 路径遍历收集所有相关节点（有深度上限，结果集小）
      Step 2 - 用节点 ID 集合查边（索引扫描，无路径遍历，快几个数量级）
    """
    depth = min(depth, 5)

    # Step 1：收集 depth 跳内所有节点
    # IMPLEMENTS 作为"免费穿越"：在 CALLS 路径的基础上，每个节点额外展开 IMPLEMENTS 目标。
    # 即 service -[CALLS]-> MapperInterface -[IMPLEMENTS]-> XmlSql 时，
    # depth=1 就能看到 XmlSql（IMPLEMENTS 不占深度计数）。
    rows = await run_query(
        f"""
        MATCH (center:Method {{id: $symbol_id}})
        CALL {{
          WITH center
          MATCH (center)-[:CALLS*0..{depth}]->(n:Method {{repo_id: $repo_id}})
          RETURN n
          UNION
          WITH center
          MATCH (center)-[:CALLS*0..{depth}]->(m:Method {{repo_id: $repo_id}})-[:IMPLEMENTS]->(n:Method {{repo_id: $repo_id}})
          RETURN n
          UNION
          WITH center
          MATCH (n:Method {{repo_id: $repo_id}})-[:CALLS*1..{depth}]->(center)
          RETURN n
          UNION
          WITH center
          MATCH (n:Method {{repo_id: $repo_id}})-[:CALLS*1..{depth}]->(m:Method)-[:IMPLEMENTS]->(center)
          RETURN n
          UNION
          WITH center
          MATCH (center)-[:IMPLEMENTS]->(n:Method {{repo_id: $repo_id}})
          RETURN n
          UNION
          WITH center
          MATCH (n:Method {{repo_id: $repo_id}})-[:IMPLEMENTS]->(center)
          RETURN n
        }}
        WITH DISTINCT n
        WHERE n IS NOT NULL
        RETURN
          n.id          AS id,
          n.name        AS name,
          n.class_name  AS class_name,
          n.file_path   AS file_path,
          n.line_start  AS line_start,
          n.line_end    AS line_end,
          coalesce(n.chunk_type, 'method') AS chunk_type,
          coalesce(n.pagerank, 0.0) AS pagerank,
          coalesce(n.in_degree, size([(n)<-[:CALLS]-() | 1])) AS in_degree
        LIMIT 300
        """,
        {"symbol_id": symbol_id, "repo_id": repo_id},
    )

    node_ids = [r["id"] for r in rows if r.get("id")]

    # Step 2：在节点 ID 集合内查边（CALLS + IMPLEMENTS 都拿，前端区分样式）
    edge_rows = await run_query(
        """
        MATCH (a:Method)-[r:CALLS|IMPLEMENTS]->(b:Method)
        WHERE a.id IN $node_ids AND b.id IN $node_ids
        RETURN DISTINCT a.id AS src, b.id AS tgt,
               type(r) AS edge_type,
               r.confidence AS conf, r.call_count AS cnt
        """,
        {"node_ids": node_ids},
    ) if node_ids else []

    nodes = [
        GraphNode(
            id         = r["id"],
            name       = r["name"] or "",
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


# ── 类级依赖图 ────────────────────────────────────────────────────────────────

async def get_class_graph(repo_id: str, class_name: str | None = None) -> GraphResponse:
    """
    类级别依赖图：聚合方法调用边 → 类与类之间的调用关系。
    class_name 为 None 时返回整个 repo 的类图。
    """
    params: dict = {"repo_id": repo_id}
    class_filter = ""
    if class_name:
        class_filter = "AND (c1.name = $class_name OR c2.name = $class_name)"
        params["class_name"] = class_name

    rows = await run_query(
        f"""
        MATCH (m1:Method {{repo_id: $repo_id}})-[:CALLS]->(m2:Method {{repo_id: $repo_id}})
        MATCH (m1)-[:BELONGS_TO]->(c1:Class {{repo_id: $repo_id}})
        MATCH (m2)-[:BELONGS_TO]->(c2:Class {{repo_id: $repo_id}})
        WHERE c1 <> c2
        {class_filter}
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
    """查找两个方法之间的最短调用路径。以列表形式返回有序节点，不用 DISTINCT 以保留路径顺序。"""
    rows = await run_query(
        """
        MATCH (from:Method {id: $from_id}),
              (to:Method   {id: $to_id})
        MATCH path = shortestPath((from)-[:CALLS*1..8]->(to))
        WITH nodes(path) AS ns, length(path) AS path_length
        RETURN
          [n IN ns | {
            id: n.id, name: n.name, class_name: n.class_name,
            file_path: n.file_path, line_start: n.line_start, line_end: n.line_end
          }] AS path_nodes,
          path_length
        LIMIT 1
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
            id         = n["id"],
            name       = n.get("name") or "",
            class_name = n.get("class_name"),
            file_path  = n.get("file_path") or "",
            line_start = n.get("line_start"),
            line_end   = n.get("line_end"),
            node_type  = "method",
        )
        for n in (rows[0].get("path_nodes") or [])
    ]

    # 路径节点有序，直接按顺序重建边
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
