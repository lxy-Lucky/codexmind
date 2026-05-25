import { http } from './client'

export interface GraphNode {
  id: string
  name: string
  class_name?: string
  file_path: string
  line_start?: number
  line_end?: number
  node_type: 'method' | 'sql' | 'class' | 'file'
  pagerank: number
  in_degree: number
}

export interface GraphEdge {
  source: string
  target: string
  edge_type?: 'CALLS' | 'IMPLEMENTS'
  confidence: number
  call_count: number
}

export interface GraphResponse {
  nodes: GraphNode[]
  edges: GraphEdge[]
  center_id: string
  depth: number
}

export interface ImpactResponse {
  nodes: GraphNode[]
  edges: GraphEdge[]
  center_id: string
  max_depth: number
  total_affected: number
}

export interface PathResponse {
  nodes: GraphNode[]
  edges: GraphEdge[]
  from_id: string
  to_id: string
  path_length: number
  found: boolean
}

export const graphApi = {
  /** 方法级调用图（双向 N 跳） */
  getMethodGraph(repoId: string, symbolId: string, depth = 2): Promise<GraphResponse> {
    return http.get(`/api/graph/${repoId}/method/${symbolId}`, { params: { depth } })
      .then(r => r.data)
  },

  /** 影响域：修改此方法后谁会受影响 */
  getImpact(repoId: string, symbolId: string, maxDepth = 3): Promise<ImpactResponse> {
    return http.get(`/api/graph/${repoId}/impact/${symbolId}`, { params: { max_depth: maxDepth } })
      .then(r => r.data)
  },

  /** 类级依赖图 */
  getClassGraph(repoId: string, className?: string): Promise<GraphResponse> {
    return http.get(`/api/graph/${repoId}/class`, {
      params: className ? { class_name: className } : {}
    }).then(r => r.data)
  },

  /** 两方法间最短调用路径 */
  getCallPath(repoId: string, fromId: string, toId: string): Promise<PathResponse> {
    return http.get(`/api/graph/${repoId}/path`, { params: { from_id: fromId, to_id: toId } })
      .then(r => r.data)
  },
}
