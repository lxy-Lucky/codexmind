<template>
  <div class="tab-deps">
    <!-- 工具栏 -->
    <div class="deps-toolbar">
      <div class="view-select-wrap">
        <select
          class="view-select"
          :value="activeView"
          @change="switchView(($event.target as HTMLSelectElement).value as ViewKey)"
        >
          <option v-for="v in views" :key="v.key" :value="v.key">{{ v.label }}</option>
        </select>
        <span class="view-select-arrow">▾</span>
      </div>
      <div class="legend">
        <span class="dot controller">{{ t('deps.legend.controller') }}</span>
        <span class="dot service">{{ t('deps.legend.service') }}</span>
        <span class="dot dao">{{ t('deps.legend.dao') }}</span>
        <span class="dot sql">{{ t('deps.legend.sql') }}</span>
        <span class="dot util">{{ t('deps.legend.util') }}</span>
      </div>
    </div>

    <!-- 图容器 -->
    <div class="graph-wrap" ref="graphWrap">
      <svg ref="svgRef" class="graph-svg" />
      <div class="graph-empty" v-if="isEmpty && !loading">
        <span>{{ t('deps.empty') }}</span>
      </div>
      <div class="graph-loading" v-if="loading">{{ t('deps.loading') }}</div>

      <!-- 浮动控件 -->
      <div class="floating-controls" v-if="!isEmpty">
        <div class="depth-control" v-if="activeView !== 'class'">
          <span class="depth-label">{{ t('deps.depth') }}</span>
          <button @click="changeDepth(-1)" :disabled="depth <= 1">−</button>
          <span class="depth-val">{{ depth }}</span>
          <button @click="changeDepth(1)" :disabled="depth >= 5">+</button>
        </div>
        <button class="recenter-btn" @click="fitToView" :title="t('deps.resetTitle')">{{ t('deps.reset') }}</button>
      </div>

      <!-- 提示：双击展开 -->
      <div class="hint-banner" v-if="!isEmpty && !loading && activeView !== 'class'">
        {{ t('deps.dblClickHint') }}
      </div>
    </div>

    <!-- 节点详情浮层 -->
    <div
      v-if="hovered"
      class="node-tooltip"
      :style="{ left: tooltipPos.x + 'px', top: tooltipPos.y + 'px' }"
    >
      <div class="tt-name">{{ hovered.name }}</div>
      <div class="tt-class" v-if="hovered.class_name">{{ hovered.class_name }}</div>
      <div class="tt-file">{{ hovered.file_path }}</div>
      <div class="tt-meta">PR: {{ hovered.pagerank.toFixed(4) }} · In: {{ hovered.in_degree }}</div>
    </div>

    <!-- 影响域统计 -->
    <div class="impact-banner" v-if="activeView === 'impact' && impactData">
      <i18n-t keypath="deps.impactSummary" tag="span">
        <template #n><strong>{{ impactData.total_affected }}</strong></template>
        <template #d>{{ impactData.max_depth }}</template>
      </i18n-t>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, nextTick, onMounted, onUnmounted } from 'vue'
import { useI18n } from 'vue-i18n'
import * as d3 from 'd3'
import { graphApi, type GraphNode, type GraphEdge, type GraphResponse, type ImpactResponse } from '@/api/graph'

const { t } = useI18n()

const props = defineProps<{
  repoId: string
  symbolId?: string
  className?: string
  requestedView?: ViewKey
  reloadTick?: number
}>()

// ── State ──────────────────────────────────────────────────────────────────
type ViewKey = 'method' | 'class' | 'impact'
const views = computed(() => [
  { key: 'method' as ViewKey,  label: t('deps.views.method') },
  { key: 'class'  as ViewKey,  label: t('deps.views.class')  },
  { key: 'impact' as ViewKey,  label: t('deps.views.impact') },
])

const activeView  = ref<ViewKey>('method')
const depth       = ref(1)   // 默认 depth=1，不炸图
const loading     = ref(false)
const isEmpty     = ref(true)
const hovered     = ref<GraphNode | null>(null)
const tooltipPos  = ref({ x: 0, y: 0 })
const impactData  = ref<ImpactResponse | null>(null)

const svgRef   = ref<SVGSVGElement>()
const graphWrap = ref<HTMLDivElement>()

let simulation: d3.Simulation<any, any> | null = null
let zoomBehavior: d3.ZoomBehavior<SVGSVGElement, unknown> | null = null
let svgSel: d3.Selection<SVGSVGElement, unknown, null, undefined> | null = null
let lastNodeData: any[] = []
let lastViewport = { W: 0, H: 0 }

// 记录当前图里的 edges（用于高亮路径）
let currentEdges: GraphEdge[] = []
let selectedNodeId = ''

// ── Node color by layer ────────────────────────────────────────────────────
function nodeColor(node: GraphNode): string {
  if (node.node_type === 'sql')                                 return '#ec4899'
  const fp = (node.file_path || '').toLowerCase()
  const nm = (node.name || '').toLowerCase()
  if (fp.includes('controller') || nm.includes('controller')) return '#6366f1'
  if (fp.includes('service')    || nm.includes('service'))    return '#10b981'
  if (fp.includes('mapper')     || fp.includes('dao')
    || fp.includes('repository'))                              return '#f59e0b'
  return '#64748b'
}

function nodeRadius(node: GraphNode): number {
  const base = node.node_type === 'class' ? 14
             : node.node_type === 'sql'   ? 11
             : 10
  return base + Math.min(node.in_degree * 0.6, 6)
}

// ── 层级计算：BFS 从 center 出发，caller 标负层、callee 标正层 ──
function computeLayerMap(
  nodes: GraphNode[], edges: GraphEdge[], centerId: string,
): Map<string, number> {
  const layerMap = new Map<string, number>()
  layerMap.set(centerId, 0)

  // 构建邻接表
  const outgoing = new Map<string, Set<string>>() // source -> targets (callees)
  const incoming = new Map<string, Set<string>>() // target -> sources (callers)
  for (const e of edges) {
    const src = typeof e.source === 'string' ? e.source : (e.source as any).id
    const tgt = typeof e.target === 'string' ? e.target : (e.target as any).id
    if (!outgoing.has(src)) outgoing.set(src, new Set())
    outgoing.get(src)!.add(tgt)
    if (!incoming.has(tgt)) incoming.set(tgt, new Set())
    incoming.get(tgt)!.add(src)
  }

  // BFS 向下（callees → 正数层）
  let queue = [centerId]
  while (queue.length) {
    const next: string[] = []
    for (const nid of queue) {
      const layer = layerMap.get(nid)!
      for (const callee of outgoing.get(nid) || []) {
        if (!layerMap.has(callee)) {
          layerMap.set(callee, layer + 1)
          next.push(callee)
        }
      }
    }
    queue = next
  }

  // BFS 向上（callers → 负数层）
  queue = [centerId]
  const visited = new Set([centerId])
  while (queue.length) {
    const next: string[] = []
    for (const nid of queue) {
      const layer = layerMap.get(nid)!
      for (const caller of incoming.get(nid) || []) {
        if (!visited.has(caller)) {
          visited.add(caller)
          if (!layerMap.has(caller)) {
            layerMap.set(caller, layer - 1)
          }
          next.push(caller)
        }
      }
    }
    queue = next
  }

  // 没有被 BFS 到的节点给个默认层（不应该发生）
  for (const n of nodes) {
    if (!layerMap.has(n.id)) layerMap.set(n.id, 0)
  }
  return layerMap
}

// ── 类聚合力：同一个 class_name 的节点互相吸引 ──
function classClusterForce(alpha: number) {
  const classGroups = new Map<string, any[]>()
  for (const n of lastNodeData) {
    const cls = n.class_name || '__none__'
    if (!classGroups.has(cls)) classGroups.set(cls, [])
    classGroups.get(cls)!.push(n)
  }
  for (const [, group] of classGroups) {
    if (group.length < 2) continue
    // 计算质心
    let cx = 0, cy = 0
    for (const n of group) { cx += n.x; cy += n.y }
    cx /= group.length; cy /= group.length
    // 向质心靠拢（弱力）
    const strength = 0.15 * alpha
    for (const n of group) {
      n.vx += (cx - n.x) * strength
      n.vy += (cy - n.y) * strength
    }
  }
}

// ── Fetch & Render ─────────────────────────────────────────────────────────
async function loadGraph() {
  if (!props.repoId) return

  loading.value = true
  isEmpty.value = false
  impactData.value = null

  try {
    let data: GraphResponse | ImpactResponse

    if (activeView.value === 'method') {
      if (!props.symbolId) { isEmpty.value = true; return }
      data = await graphApi.getMethodGraph(props.repoId, props.symbolId, depth.value)
    } else if (activeView.value === 'impact') {
      if (!props.symbolId) { isEmpty.value = true; return }
      data = await graphApi.getImpact(props.repoId, props.symbolId, depth.value)
      impactData.value = data as ImpactResponse
    } else {
      data = await graphApi.getClassGraph(props.repoId, props.className)
    }

    if (!data.nodes.length) { isEmpty.value = true; return }
    currentEdges = data.edges
    await nextTick()
    renderGraph(data.nodes, data.edges, data.center_id)
  } catch (e) {
    console.error('Graph load error', e)
    isEmpty.value = true
  } finally {
    loading.value = false
  }
}

function renderGraph(nodes: GraphNode[], edges: GraphEdge[], centerId: string) {
  if (!svgRef.value || !graphWrap.value) return

  const W = graphWrap.value.clientWidth  || 800
  const H = graphWrap.value.clientHeight || 500
  lastViewport = { W, H }
  selectedNodeId = ''

  // 清空旧图
  d3.select(svgRef.value).selectAll('*').remove()
  if (simulation) simulation.stop()

  const svg = d3.select(svgRef.value)
    .attr('width', W)
    .attr('height', H)
  svgSel = svg

  // Zoom
  const g = svg.append('g')
  zoomBehavior = d3.zoom<SVGSVGElement, unknown>()
    .scaleExtent([0.1, 4])
    .on('zoom', (e) => g.attr('transform', e.transform))
  svg.call(zoomBehavior)

  // 点击空白处取消选中
  svg.on('click', () => {
    selectedNodeId = ''
    updateHighlight()
  })

  // Arrow markers: normal + highlighted
  const defs = svg.append('defs')
  for (const [id, color] of [['arrow', '#475569'], ['arrow-hl', '#00d4ff']] as const) {
    defs.append('marker')
      .attr('id', id)
      .attr('viewBox', '0 -4 8 8')
      .attr('refX', 18).attr('refY', 0)
      .attr('markerWidth', 6).attr('markerHeight', 6)
      .attr('orient', 'auto')
      .append('path')
      .attr('d', 'M0,-4L8,0L0,4')
      .attr('fill', color)
  }

  // ── 层级计算 ──
  const layerMap = computeLayerMap(nodes, edges, centerId)

  // 节点初始位置：按层分排
  const cx = W / 2, cy = H / 2
  const layerSpacing = 100
  const nodeMap = new Map(
    nodes.map((n, i) => {
      const layer = layerMap.get(n.id) ?? 0
      // 同层内水平分散
      const sameLayerNodes = nodes.filter(nn => (layerMap.get(nn.id) ?? 0) === layer)
      const idx = sameLayerNodes.indexOf(n)
      const xSpread = Math.min(W * 0.6, sameLayerNodes.length * 60)
      const xOffset = sameLayerNodes.length > 1
        ? -xSpread / 2 + (idx / (sameLayerNodes.length - 1)) * xSpread
        : 0
      return [n.id, {
        ...n,
        x: cx + xOffset + (Math.random() - 0.5) * 20,
        y: cy + layer * layerSpacing + (Math.random() - 0.5) * 10,
        layer,
      }]
    })
  )

  // 中心节点 pin 住
  const centerNode = nodeMap.get(centerId) as any
  if (centerNode) {
    centerNode.fx = cx
    centerNode.fy = cy
  }

  const links = edges
    .filter(e => nodeMap.has(e.source) && nodeMap.has(e.target))
    .map(e => ({ ...e, source: nodeMap.get(e.source)!, target: nodeMap.get(e.target)! }))

  const nodeData = Array.from(nodeMap.values())
  lastNodeData = nodeData

  const N = nodeData.length

  // ── Force 配置：层级 + 聚合 ──
  // 核心思路：forceY 按 layer 把节点拉到该层的 Y 位置，
  //          classCluster 让同类方法靠拢，
  //          charge 保持节点间距但不太强
  const chargeStrength = N > 60 ? -60 : N > 30 ? -120 : -200
  const linkDist = N > 60 ? 60 : N > 30 ? 80 : 100

  simulation = d3.forceSimulation(nodeData)
    .force('link', d3.forceLink(links).id((d: any) => d.id).distance(linkDist).strength(0.6))
    .force('charge', d3.forceManyBody().strength(chargeStrength))
    .force('collide', d3.forceCollide().radius((d: any) => nodeRadius(d) + 8))
    // 层级 Y 约束：每层一个目标 Y 位置，strength 较强保持层次感
    .force('layerY', d3.forceY((d: any) => cy + d.layer * layerSpacing).strength(0.4))
    // X 居中力（弱，让整体居中但不覆盖类聚合）
    .force('centerX', d3.forceX(cx).strength(0.03))
    // 类聚合：在 tick 中手动施加
    .alphaDecay(0.03)  // 稍慢收敛，让聚合力生效

  // 手动加类聚合力
  simulation.on('tick.cluster', () => classClusterForce(simulation!.alpha()))

  // ── Edges ──
  const link = g.append('g').attr('class', 'edges').selectAll('line')
    .data(links).join('line')
    .attr('stroke', (d: any) => d.edge_type === 'IMPLEMENTS' ? '#ec4899' : '#475569')
    .attr('stroke-opacity', 0.6)
    .attr('stroke-width', (d: any) => Math.min(Math.sqrt(d.call_count || 1), 3))
    .attr('stroke-dasharray', (d: any) => d.edge_type === 'IMPLEMENTS' ? '5,4' : null)
    .attr('marker-end', 'url(#arrow)')

  // ── Nodes ──
  const sqlNodes   = nodeData.filter((d: any) => d.node_type === 'sql')
  const otherNodes = nodeData.filter((d: any) => d.node_type !== 'sql')

  const nodeG = g.append('g').attr('class', 'nodes')

  // 圆形节点
  const circleSel = nodeG.selectAll('circle')
    .data(otherNodes).join('circle')
    .attr('r', (d: any) => nodeRadius(d))
    .attr('fill', (d: any) => nodeColor(d))
    .attr('stroke', (d: any) => d.id === centerId ? '#fff' : 'rgba(255,255,255,0.15)')
    .attr('stroke-width', (d: any) => d.id === centerId ? 2.5 : 1)
    .style('cursor', 'pointer')

  // 菱形 SQL 节点
  const diamondSel = nodeG.selectAll('rect.sql')
    .data(sqlNodes).join('rect')
    .attr('class', 'sql')
    .attr('width',  (d: any) => nodeRadius(d) * 1.6)
    .attr('height', (d: any) => nodeRadius(d) * 1.6)
    .attr('fill', (d: any) => nodeColor(d))
    .attr('stroke', (d: any) => d.id === centerId ? '#fff' : 'rgba(255,255,255,0.15)')
    .attr('stroke-width', (d: any) => d.id === centerId ? 2.5 : 1)
    .style('cursor', 'pointer')

  // 统一行为
  const allNodes: any = circleSel.merge(diamondSel as any)

  // 单击：高亮直接连接的路径
  allNodes.on('click', (evt: MouseEvent, d: any) => {
    evt.stopPropagation()
    selectedNodeId = selectedNodeId === d.id ? '' : d.id
    updateHighlight()
  })

  // 双击：以该节点为中心重新查图
  allNodes.on('dblclick', (_evt: MouseEvent, d: any) => {
    if (d.id === centerId) return // 已经是中心
    emit('expandNode', d.id, d.class_name)
  })

  // Hover tooltip
  allNodes
    .on('mousemove', (evt: MouseEvent, d: any) => {
      const rect = graphWrap.value!.getBoundingClientRect()
      hovered.value = d
      tooltipPos.value = { x: evt.clientX - rect.left + 12, y: evt.clientY - rect.top - 10 }
    })
    .on('mouseleave', () => { hovered.value = null })

  // Drag
  allNodes.call(d3.drag<SVGElement, any>()
    .on('start', (e, d) => { if (!e.active) simulation!.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y })
    .on('drag',  (e, d) => { d.fx = e.x; d.fy = e.y })
    .on('end',   (e, d) => {
      if (!e.active) simulation!.alphaTarget(0)
      // 中心节点保持 pin
      if (d.id !== centerId) { d.fx = null; d.fy = null }
    })
  )

  // Labels
  const label = g.append('g').attr('class', 'labels').selectAll('text')
    .data(nodeData).join('text')
    .text((d: any) => d.name.length > 18 ? d.name.slice(0, 16) + '…' : d.name)
    .attr('font-size', 11)
    .attr('fill', '#94a3b8')
    .attr('text-anchor', 'middle')
    .attr('dy', (d: any) => nodeRadius(d) + 13)
    .style('pointer-events', 'none')

  // 高亮更新函数
  function updateHighlight() {
    if (!selectedNodeId) {
      // 取消高亮：恢复所有
      circleSel.attr('opacity', 1)
      diamondSel.attr('opacity', 1)
      link.attr('stroke-opacity', 0.6)
        .attr('stroke', (d: any) => d.edge_type === 'IMPLEMENTS' ? '#ec4899' : '#475569')
        .attr('stroke-width', (d: any) => Math.min(Math.sqrt(d.call_count || 1), 3))
        .attr('marker-end', 'url(#arrow)')
      label.attr('opacity', 1)
      return
    }

    // 找出直接连接的节点
    const connected = new Set<string>([selectedNodeId])
    for (const l of links) {
      const src = (l.source as any).id
      const tgt = (l.target as any).id
      if (src === selectedNodeId) connected.add(tgt)
      if (tgt === selectedNodeId) connected.add(src)
    }

    // 淡化非连接节点
    circleSel.attr('opacity', (d: any) => connected.has(d.id) ? 1 : 0.15)
    diamondSel.attr('opacity', (d: any) => connected.has(d.id) ? 1 : 0.15)
    label.attr('opacity', (d: any) => connected.has(d.id) ? 1 : 0.1)

    // 高亮连接的边
    link
      .attr('stroke-opacity', (d: any) => {
        const src = (d.source as any).id
        const tgt = (d.target as any).id
        return (src === selectedNodeId || tgt === selectedNodeId) ? 1 : 0.08
      })
      .attr('stroke', (d: any) => {
        const src = (d.source as any).id
        const tgt = (d.target as any).id
        if (src === selectedNodeId || tgt === selectedNodeId) return '#00d4ff'
        return d.edge_type === 'IMPLEMENTS' ? '#ec4899' : '#475569'
      })
      .attr('stroke-width', (d: any) => {
        const src = (d.source as any).id
        const tgt = (d.target as any).id
        if (src === selectedNodeId || tgt === selectedNodeId) return 2.5
        return Math.min(Math.sqrt(d.call_count || 1), 3)
      })
      .attr('marker-end', (d: any) => {
        const src = (d.source as any).id
        const tgt = (d.target as any).id
        return (src === selectedNodeId || tgt === selectedNodeId) ? 'url(#arrow-hl)' : 'url(#arrow)'
      })
  }

  // ── Tick ──
  const pad = 40
  simulation.on('tick', () => {
    // 约束边界
    nodeData.forEach((d: any) => {
      if (d.fx == null) d.x = Math.max(pad, Math.min(W - pad, d.x))
      if (d.fy == null) d.y = Math.max(pad, Math.min(H - pad, d.y))
    })
    link
      .attr('x1', (d: any) => d.source.x)
      .attr('y1', (d: any) => d.source.y)
      .attr('x2', (d: any) => d.target.x)
      .attr('y2', (d: any) => d.target.y)
    circleSel
      .attr('cx', (d: any) => d.x)
      .attr('cy', (d: any) => d.y)
    diamondSel
      .attr('x', (d: any) => d.x - nodeRadius(d) * 0.8)
      .attr('y', (d: any) => d.y - nodeRadius(d) * 0.8)
      .attr('transform', (d: any) => `rotate(45 ${d.x} ${d.y})`)
    label
      .attr('x', (d: any) => d.x)
      .attr('y', (d: any) => d.y)
  })

  // simulation 冷却完成后自动缩放
  simulation.on('end', () => fitToView())
}


// ── 视图自适应 ─────────────────────────────────────────────────────────────
function fitToView() {
  if (!svgSel || !zoomBehavior || !lastNodeData.length) return
  const { W, H } = lastViewport
  if (!W || !H) return

  const xs = lastNodeData.map((d: any) => d.x)
  const ys = lastNodeData.map((d: any) => d.y)
  const minX = Math.min(...xs), maxX = Math.max(...xs)
  const minY = Math.min(...ys), maxY = Math.max(...ys)
  const w = maxX - minX || 1
  const h = maxY - minY || 1

  const padInner = 60
  const scale = Math.min(
    (W - padInner * 2) / w,
    (H - padInner * 2) / h,
    1.4,
  )
  const tx = W / 2 - scale * (minX + w / 2)
  const ty = H / 2 - scale * (minY + h / 2)

  svgSel.transition().duration(400).call(
    zoomBehavior.transform,
    d3.zoomIdentity.translate(tx, ty).scale(scale)
  )
}

// ── Controls ───────────────────────────────────────────────────────────────
const emit = defineEmits<{
  (e: 'expandNode', symbolId: string, className?: string): void
}>()

function switchView(v: ViewKey) {
  activeView.value = v
  loadGraph()
}

function changeDepth(delta: number) {
  depth.value = Math.max(1, Math.min(5, depth.value + delta))
  loadGraph()
}

// ── Watchers ───────────────────────────────────────────────────────────────
watch(() => props.reloadTick, (tick, oldTick) => {
  if (tick === oldTick || tick === undefined) return
  if (props.requestedView) activeView.value = props.requestedView
  loadGraph()
})

watch(() => props.requestedView, (v) => {
  if (!v) return
  activeView.value = v
  loadGraph()
})

watch(() => props.symbolId, (newId, oldId) => {
  if (newId !== oldId && newId && activeView.value !== 'class') loadGraph()
})

watch(() => props.className, (newCls, oldCls) => {
  if (newCls !== oldCls && newCls && activeView.value === 'class') loadGraph()
})

onMounted(() => {
  if (props.requestedView) {
    activeView.value = props.requestedView
    loadGraph()
  } else if (props.symbolId) {
    loadGraph()
  }
})
onUnmounted(() => { simulation?.stop() })
</script>

<style scoped>
.tab-deps {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--bg-secondary, #0f172a);
  gap: 0;
}

.deps-toolbar {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 8px 12px;
  border-bottom: 1px solid var(--border, #1e293b);
  flex-shrink: 0;
}

.view-select-wrap {
  position: relative;
  display: flex;
  align-items: center;
}
.view-select {
  padding: 4px 26px 4px 10px;
  border-radius: 6px;
  border: 1px solid var(--border, #1e293b);
  background: var(--bg-deep, #0f172a);
  color: #fff;
  font-size: 12px;
  cursor: pointer;
  appearance: none;
  -webkit-appearance: none;
  outline: none;
}
.view-select:focus { border-color: var(--accent, #6366f1); }
.view-select-arrow {
  position: absolute;
  right: 8px;
  pointer-events: none;
  color: var(--text-secondary, #94a3b8);
  font-size: 10px;
  line-height: 1;
}

/* 浮动控件 */
.floating-controls {
  position: absolute;
  right: 12px;
  bottom: 12px;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  border-radius: 8px;
  background: rgba(15, 23, 42, 0.85);
  backdrop-filter: blur(6px);
  border: 1px solid rgba(148, 163, 184, 0.18);
  z-index: 10;
  opacity: 0.7;
  transition: opacity 0.15s ease;
}
.floating-controls:hover { opacity: 1; }

.depth-control {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--text-secondary, #94a3b8);
}
.depth-control button {
  width: 22px; height: 22px;
  border-radius: 4px;
  border: 1px solid var(--border, #1e293b);
  background: transparent;
  color: #fff;
  cursor: pointer;
  line-height: 1;
}
.depth-control button:disabled { opacity: 0.3; cursor: not-allowed; }
.depth-val { font-weight: 600; color: #fff; min-width: 14px; text-align: center; }

.recenter-btn {
  padding: 4px 10px;
  border-radius: 6px;
  border: 1px solid var(--border, #1e293b);
  background: transparent;
  color: var(--text-secondary, #94a3b8);
  font-size: 11px;
  cursor: pointer;
  white-space: nowrap;
}
.recenter-btn:hover {
  color: #fff;
  border-color: var(--accent, #6366f1);
}

/* 提示横幅 */
.hint-banner {
  position: absolute;
  left: 12px;
  bottom: 12px;
  padding: 4px 10px;
  border-radius: 6px;
  background: rgba(15, 23, 42, 0.78);
  backdrop-filter: blur(6px);
  border: 1px solid rgba(148, 163, 184, 0.12);
  font-size: 10px;
  color: #64748b;
  z-index: 10;
  pointer-events: none;
}

.legend {
  display: flex;
  gap: 12px;
  margin-left: auto;
  font-size: 11px;
}
.dot { display: flex; align-items: center; gap: 5px; color: var(--text-secondary, #94a3b8); }
.dot::before {
  content: ''; width: 8px; height: 8px; border-radius: 50%;
}
.dot.controller::before { background: #6366f1; }
.dot.service::before    { background: #10b981; }
.dot.dao::before        { background: #f59e0b; }
.dot.sql::before        { background: #ec4899; transform: rotate(45deg); }
.dot.util::before       { background: #64748b; }

.graph-wrap {
  flex: 1;
  position: relative;
  overflow: hidden;
}
.graph-svg { display: block; }
.graph-empty, .graph-loading {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-secondary, #94a3b8);
  font-size: 13px;
}

.node-tooltip {
  position: absolute;
  pointer-events: none;
  background: #1e293b;
  border: 1px solid #334155;
  border-radius: 8px;
  padding: 8px 12px;
  font-size: 12px;
  color: #e2e8f0;
  z-index: 99;
  max-width: 280px;
}
.tt-name  { font-weight: 600; margin-bottom: 2px; }
.tt-class { color: #94a3b8; font-size: 11px; }
.tt-file  { color: #64748b; font-size: 11px; margin-top: 2px; word-break: break-all; }
.tt-meta  { color: #475569; font-size: 11px; margin-top: 4px; }

.impact-banner {
  padding: 6px 14px;
  background: #1e293b;
  border-top: 1px solid #334155;
  font-size: 12px;
  color: #94a3b8;
  flex-shrink: 0;
}
.impact-banner strong { color: #f59e0b; }
</style>
