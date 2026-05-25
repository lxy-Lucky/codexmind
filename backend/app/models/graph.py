from __future__ import annotations

from typing import Optional
from pydantic import BaseModel


class GraphNode(BaseModel):
    id: str
    name: str                    # method_name 或 class_name
    class_name: Optional[str] = None
    file_path: str
    line_start: Optional[int] = None
    line_end: Optional[int] = None
    node_type: str = "method"    # method | sql | class | file
    pagerank: float = 0.0
    in_degree: int = 0           # 被调用次数


class GraphEdge(BaseModel):
    source: str                  # node id
    target: str                  # node id
    edge_type: str = "CALLS"     # CALLS | IMPLEMENTS（Java interface → XML SQL）
    confidence: float = 1.0
    call_count: int = 1


class GraphResponse(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    center_id: str               # 查询起点 node id
    depth: int = 1


class ImpactResponse(BaseModel):
    """影响域：修改某方法后，哪些调用者会受影响"""
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    center_id: str
    max_depth: int = 3
    total_affected: int = 0


class PathResponse(BaseModel):
    """两个方法之间的最短调用路径"""
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    from_id: str
    to_id: str
    path_length: int = 0
    found: bool = False
