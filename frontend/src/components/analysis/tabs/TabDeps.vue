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
      <div class="depth-control" v-if="activeView !== 'class'">
        <span class="depth-label">深度</span>
        <button @click="changeDepth(-1)" :disabled="depth <= 1">−</button>
        <span class="depth-val">{{ depth }}</span>
        <button @click="changeDepth(1)" :disabled="depth >= 5">+</button>
      </div>
      <div class="legend">
        <span class="dot controller">Controller</span>
        <span class="dot service">Service</span>
        <span class="dot dao">DAO/Mapper</span>
        <span class="dot util">Util/Other</span>
      </div>
    </div>

    <!-- 图容器 -->
    <div class="graph-wrap" ref="graphWrap">
      <svg ref="svgRef" class="graph-svg" />
      <div class="graph-empty" v-if="isEmpty && !loading">
        <span>选中代码行后，调用图将在此显示</span>
      </div>
      <div class="graph-loading" v-if="loading">加载中…</div>
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
      <div class="tt-meta">PageRank: {{ hovered.pagerank.toFixed(4) }} · 被调用: {{ hovered.in_degree }}</div>
    </div>

    <!-- 影响域统计 -->
    <div class="impact-banner" v-if="activeView === 'impact' && impactData">
      共 <strong>{{ impactData.total_affected }}</strong> 个方法受影响（{{ impactData.max_depth }} 跳以内）
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch, nextTick, onMounted, onUnmounted } from 'vue'
import * as d3 from 'd3'
import { graphApi, type GraphNode, type GraphEdge, type GraphResponse, type ImpactResponse } from '@/api/graph'

const props = defineProps<{
  repoId: string
  symbolId?: string
  className?: string
  requestedView?: ViewKey   // 由工具栏「依赖」按钮驱动，决定激活哪个子视图
  reloadTick?: number       // 每次点「依赖」按钮都自增，强制重新加载
}>()

// ── State ──────────────────────────────────────────────────────────────────
type ViewKey = 'method' | 'class' | 'impact'
const views = [
  { key: 'method' as ViewKey,  label: '方法调用图' },
  { key: 'class'  as ViewKey,  label: '类依赖图'   },
  { key: 'impact' as ViewKey,  label: '影响域'      },
]

const activeView  = ref<ViewKey>('method')
const depth       = ref(2)
const loading     = ref(false)
const isEmpty     = ref(true)
const hovered     = ref<GraphNode | null>(null)
const tooltipPos  = ref({ x: 0, y: 0 })
const impactData  = ref<ImpactResponse | null>(null)

const svgRef   = ref<SVGSVGElement>()
const graphWrap = ref<HTMLDivElement>()

let simulation: d3.Simulation<any, any> | null = null

// ── Node color by layer ────────────────────────────────────────────────────
function nodeColor(node: GraphNode): string {
  const fp = (node.file_path || '').toLowerCase()
  const nm = (node.name || '').toLowerCase()
  if (fp.includes('controller') || nm.includes('controller')) return 'var(--color-controller, #6366f1)'
  if (fp.includes('service')    || nm.includes('service'))    return 'var(--color-service,    #10b981)'
  if (fp.includes('mapper')     || fp.includes('dao')
    || fp.includes('repository'))                              return 'var(--color-dao,        #f59e0b)'
  return 'var(--color-util, #64748b)'
}

function nodeRadius(node: GraphNode): number {
  const base = node.node_type === 'class' ? 14 : 10
  return base + Math.min(node.in_degree * 0.8, 8)
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

  // 清空旧图
  d3.select(svgRef.value).selectAll('*').remove()
  if (simulation) simulation.stop()

  const svg = d3.select(svgRef.value)
    .attr('width', W)
    .attr('height', H)

  // Zoom
  const g = svg.append('g')
  svg.call(d3.zoom<SVGSVGElement, unknown>()
    .scaleExtent([0.2, 3])
    .on('zoom', (e) => g.attr('transform', e.transform))
  )

  // Arrow marker
  svg.append('defs').append('marker')
    .attr('id', 'arrow')
    .attr('viewBox', '0 -4 8 8')
    .attr('refX', 18).attr('refY', 0)
    .attr('markerWidth', 6).attr('markerHeight', 6)
    .attr('orient', 'auto')
    .append('path')
    .attr('d', 'M0,-4L8,0L0,4')
    .attr('fill', '#94a3b8')

  // Build d3 node/link data
  const nodeMap = new Map(nodes.map(n => [n.id, { ...n, x: W / 2, y: H / 2 }]))
  const links = edges
    .filter(e => nodeMap.has(e.source) && nodeMap.has(e.target))
    .map(e => ({ ...e, source: nodeMap.get(e.source)!, target: nodeMap.get(e.target)! }))

  const nodeData = Array.from(nodeMap.values())

  // Simulation
  simulation = d3.forceSimulation(nodeData)
    .force('link', d3.forceLink(links).id((d: any) => d.id).distance(90).strength(0.5))
    .force('charge', d3.forceManyBody().strength(-300))
    .force('center', d3.forceCenter(W / 2, H / 2))
    .force('collide', d3.forceCollide().radius((d: any) => nodeRadius(d) + 6))

  // Edges
  const link = g.append('g').selectAll('line')
    .data(links).join('line')
    .attr('stroke', '#94a3b8')
    .attr('stroke-opacity', 0.5)
    .attr('stroke-width', (d: any) => Math.min(Math.sqrt(d.call_count), 4))
    .attr('marker-end', 'url(#arrow)')

  // Nodes
  const node = g.append('g').selectAll('circle')
    .data(nodeData).join('circle')
    .attr('r', (d: any) => nodeRadius(d))
    .attr('fill', (d: any) => nodeColor(d))
    .attr('stroke', (d: any) => d.id === centerId ? '#fff' : 'none')
    .attr('stroke-width', 2.5)
    .style('cursor', 'pointer')
    .on('mousemove', (evt, d: any) => {
      const rect = graphWrap.value!.getBoundingClientRect()
      hovered.value = d
      tooltipPos.value = { x: evt.clientX - rect.left + 12, y: evt.clientY - rect.top - 10 }
    })
    .on('mouseleave', () => { hovered.value = null })
    .call(d3.drag<SVGCircleElement, any>()
      .on('start', (e, d) => { if (!e.active) simulation!.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y })
      .on('drag',  (e, d) => { d.fx = e.x; d.fy = e.y })
      .on('end',   (e, d) => { if (!e.active) simulation!.alphaTarget(0); d.fx = null; d.fy = null })
    )

  // Labels
  const label = g.append('g').selectAll('text')
    .data(nodeData).join('text')
    .text((d: any) => d.name.length > 18 ? d.name.slice(0, 16) + '…' : d.name)
    .attr('font-size', 11)
    .attr('fill', '#e2e8f0')
    .attr('text-anchor', 'middle')
    .attr('dy', (d: any) => nodeRadius(d) + 13)
    .style('pointer-events', 'none')

  simulation.on('tick', () => {
    link
      .attr('x1', (d: any) => d.source.x)
      .attr('y1', (d: any) => d.source.y)
      .attr('x2', (d: any) => d.target.x)
      .attr('y2', (d: any) => d.target.y)
    node
      .attr('cx', (d: any) => d.x)
      .attr('cy', (d: any) => d.y)
    label
      .attr('x', (d: any) => d.x)
      .attr('y', (d: any) => d.y)
  })
}

// ── Controls ───────────────────────────────────────────────────────────────
function switchView(v: ViewKey) {
  activeView.value = v
  loadGraph()
}

function changeDepth(delta: number) {
  depth.value = Math.max(1, Math.min(5, depth.value + delta))
  loadGraph()
}

// ── Watchers ───────────────────────────────────────────────────────────────

/**
 * reloadTick 每次点「依赖」按钮都自增。
 * 即使 symbolId / requestedView 没有变化，也强制重新加载图，
 * 解决"换了方法再点没反应"的问题。
 */
watch(() => props.reloadTick, (tick, oldTick) => {
  if (tick === oldTick || tick === undefined) return
  if (props.requestedView) activeView.value = props.requestedView
  loadGraph()
})

/**
 * 工具栏「依赖」按钮触发：requestedView 变化时切换子视图并重新加载。
 * Vue 3 在同一 tick 内对多个响应式源的变更只触发一次 watcher，
 * 所以 requestedView + symbolId/className 同时变化时不会重复调用 loadGraph。
 */
watch(() => props.requestedView, (v) => {
  if (!v) return
  activeView.value = v
  loadGraph()
})

// 搜索结果点击后 symbolId 变化 → 如果当前是方法/影响域视图则刷新
watch(() => props.symbolId, (newId, oldId) => {
  if (newId !== oldId && newId && activeView.value !== 'class') loadGraph()
})

// className 变化 → 如果当前是类依赖图视图则刷新
watch(() => props.className, (newCls, oldCls) => {
  if (newCls !== oldCls && newCls && activeView.value === 'class') loadGraph()
})

onMounted(() => {
  // 初次挂载时若已有数据，根据 requestedView 或可用 prop 决定加载
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
