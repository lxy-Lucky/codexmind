<template>
  <div class="tab-deps">
    <!-- 工具栏（保留视图切换 + 图例） -->
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

      <!-- 浮动控件：深度调节 + 重置视图（右下角） -->
      <div class="floating-controls" v-if="!isEmpty">
        <div class="depth-control" v-if="activeView !== 'class'">
          <span class="depth-label">{{ t('deps.depth') }}</span>
          <button @click="changeDepth(-1)" :disabled="depth <= 1">−</button>
          <span class="depth-val">{{ depth }}</span>
          <button @click="changeDepth(1)" :disabled="depth >= 5">+</button>
        </div>
        <button class="recenter-btn" @click="fitToView" :title="t('deps.resetTitle')">{{ t('deps.reset') }}</button>
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
      <div class="tt-meta">{{ t('deps.tooltipMeta', { pr: hovered.pagerank.toFixed(4), in: hovered.in_degree }) }}</div>
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
  requestedView?: ViewKey   // 由工具栏「依赖」按钮驱动，决定激活哪个子视图
  reloadTick?: number       // 每次点「依赖」按钮都自增，强制重新加载
}>()

// ── State ──────────────────────────────────────────────────────────────────
type ViewKey = 'method' | 'class' | 'impact'
const views = computed(() => [
  { key: 'method' as ViewKey,  label: t('deps.views.method') },
  { key: 'class'  as ViewKey,  label: t('deps.views.class')  },
  { key: 'impact' as ViewKey,  label: t('deps.views.impact') },
])

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
// 保存 zoom behavior、SVG selection、节点数据的引用，给"重置视图"按钮 / fitToView 用
let zoomBehavior: d3.ZoomBehavior<SVGSVGElement, unknown> | null = null
let svgSel: d3.Selection<SVGSVGElement, unknown, null, undefined> | null = null
let lastNodeData: any[] = []
let lastViewport = { W: 0, H: 0 }

// ── Node color by layer ────────────────────────────────────────────────────
function nodeColor(node: GraphNode): string {
  // SQL 节点（MyBatis XML 语句）独立配色，比 DAO interface 更暖一档
  if (node.node_type === 'sql')                                 return 'var(--color-sql,        #ec4899)'
  const fp = (node.file_path || '').toLowerCase()
  const nm = (node.name || '').toLowerCase()
  if (fp.includes('controller') || nm.includes('controller')) return 'var(--color-controller, #6366f1)'
  if (fp.includes('service')    || nm.includes('service'))    return 'var(--color-service,    #10b981)'
  if (fp.includes('mapper')     || fp.includes('dao')
    || fp.includes('repository'))                              return 'var(--color-dao,        #f59e0b)'
  return 'var(--color-util, #64748b)'
}

function nodeRadius(node: GraphNode): number {
  const base = node.node_type === 'class' ? 14
             : node.node_type === 'sql'   ? 11   // SQL 略大一点
             : 10
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
  lastViewport = { W, H }

  // 清空旧图
  d3.select(svgRef.value).selectAll('*').remove()
  if (simulation) simulation.stop()

  const svg = d3.select(svgRef.value)
    .attr('width', W)
    .attr('height', H)
  svgSel = svg

  // Zoom（保存 behavior，供 fitToView / 重置视图按钮重新设置 transform）
  const g = svg.append('g')
  zoomBehavior = d3.zoom<SVGSVGElement, unknown>()
    .scaleExtent([0.1, 4])
    .on('zoom', (e) => g.attr('transform', e.transform))
  svg.call(zoomBehavior)

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

  // 节点初始位置：在中心周围一圈上均匀分布（不要全堆在 W/2,H/2，
  // 否则 charge 一上来全炸出 viewport，肉眼看到的就是"图飞出去了"）
  const cx = W / 2, cy = H / 2
  const ringR = Math.min(W, H) * 0.25
  const nodeMap = new Map(
    nodes.map((n, i) => {
      const angle = (i / Math.max(nodes.length, 1)) * 2 * Math.PI
      const x = n.id === centerId ? cx : cx + Math.cos(angle) * ringR
      const y = n.id === centerId ? cy : cy + Math.sin(angle) * ringR
      return [n.id, { ...n, x, y }]
    })
  )
  // 中心节点钉在视口中心，不让 force 把它甩走
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

  // Force 参数随节点数量自适应：节点多 → 减小斥力 + 缩短连线，避免铺满屏幕外
  const N = nodeData.length
  const chargeStrength = N > 60 ? -80 : N > 30 ? -180 : -280
  const linkDistance   = N > 60 ?  55 : N > 30 ?   75 :   95

  // Simulation
  simulation = d3.forceSimulation(nodeData)
    .force('link', d3.forceLink(links).id((d: any) => d.id).distance(linkDistance).strength(0.5))
    .force('charge', d3.forceManyBody().strength(chargeStrength))
    .force('center', d3.forceCenter(cx, cy).strength(0.08))   // 弱中心力，配合 ring init
    .force('collide', d3.forceCollide().radius((d: any) => nodeRadius(d) + 6))
    // X/Y 软约束：把节点往视口内拉，等效于"边界弹性墙"
    .force('x', d3.forceX(cx).strength(0.05))
    .force('y', d3.forceY(cy).strength(0.05))

  // Edges：CALLS 实线、IMPLEMENTS 虚线（Java interface → XML SQL 的桥接关系）
  const link = g.append('g').selectAll('line')
    .data(links).join('line')
    .attr('stroke', (d: any) => d.edge_type === 'IMPLEMENTS' ? '#ec4899' : '#94a3b8')
    .attr('stroke-opacity', (d: any) => d.edge_type === 'IMPLEMENTS' ? 0.7 : 0.5)
    .attr('stroke-width', (d: any) => Math.min(Math.sqrt(d.call_count), 4))
    .attr('stroke-dasharray', (d: any) => d.edge_type === 'IMPLEMENTS' ? '5,4' : null)
    .attr('marker-end', 'url(#arrow)')

  // Nodes：SQL 节点用菱形（rect rotate 45°），其他用圆形
  // d3 selectAll 多形状最干净的做法是各跑一轮 join
  const sqlNodes    = nodeData.filter((d: any) => d.node_type === 'sql')
  const otherNodes  = nodeData.filter((d: any) => d.node_type !== 'sql')

  const nodeG = g.append('g')

  // 圆形：普通方法 / Controller / Service / DAO interface
  const circleSel = nodeG.selectAll('circle')
    .data(otherNodes).join('circle')
    .attr('r', (d: any) => nodeRadius(d))
    .attr('fill', (d: any) => nodeColor(d))
    .attr('stroke', (d: any) => d.id === centerId ? '#fff' : 'none')
    .attr('stroke-width', 2.5)
    .style('cursor', 'pointer')

  // 菱形：SQL 语句
  const diamondSel = nodeG.selectAll('rect.sql')
    .data(sqlNodes).join('rect')
    .attr('class', 'sql')
    .attr('width',  (d: any) => nodeRadius(d) * 1.6)
    .attr('height', (d: any) => nodeRadius(d) * 1.6)
    .attr('fill', (d: any) => nodeColor(d))
    .attr('stroke', (d: any) => d.id === centerId ? '#fff' : 'none')
    .attr('stroke-width', 2.5)
    .style('cursor', 'pointer')

  // 统一行为绑定到两种 selection
  const allNodes: any = circleSel.merge(diamondSel as any)
  allNodes
    .on('mousemove', (evt: MouseEvent, d: any) => {
      const rect = graphWrap.value!.getBoundingClientRect()
      hovered.value = d
      tooltipPos.value = { x: evt.clientX - rect.left + 12, y: evt.clientY - rect.top - 10 }
    })
    .on('mouseleave', () => { hovered.value = null })
    .call(d3.drag<SVGElement, any>()
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

  // 硬边界：tick 内把节点位置 clamp 到 [pad, W-pad] × [pad, H-pad]，
  // 哪怕 force 算出来 fly-off-screen，渲染前也拉回来
  const pad = 40
  simulation.on('tick', () => {
    nodeData.forEach((d: any) => {
      d.x = Math.max(pad, Math.min(W - pad, d.x))
      d.y = Math.max(pad, Math.min(H - pad, d.y))
    })
    link
      .attr('x1', (d: any) => d.source.x)
      .attr('y1', (d: any) => d.source.y)
      .attr('x2', (d: any) => d.target.x)
      .attr('y2', (d: any) => d.target.y)
    circleSel
      .attr('cx', (d: any) => d.x)
      .attr('cy', (d: any) => d.y)
    // 菱形：rect 用左上角定位，旋转 45° 后中心还在 (x, y)
    diamondSel
      .attr('x', (d: any) => d.x - nodeRadius(d) * 0.8)
      .attr('y', (d: any) => d.y - nodeRadius(d) * 0.8)
      .attr('transform', (d: any) => `rotate(45 ${d.x} ${d.y})`)
    label
      .attr('x', (d: any) => d.x)
      .attr('y', (d: any) => d.y)
  })

  // simulation 冷却完成后，按节点包围盒自动缩放使整图刚好充满视口
  simulation.on('end', () => fitToView())
}


// ── 视图自适应 ─────────────────────────────────────────────────────────────
// 计算当前所有节点包围盒，平移+缩放使其居中铺满。被 simulation.on('end') 和
// 工具栏「重置视图」按钮共用。
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

  // padding 给标签留位置
  const padInner = 60
  const scale = Math.min(
    (W - padInner * 2) / w,
    (H - padInner * 2) / h,
    1.4,    // 节点少时也别放太大
  )
  const tx = W / 2 - scale * (minX + w / 2)
  const ty = H / 2 - scale * (minY + h / 2)

  svgSel.transition().duration(400).call(
    zoomBehavior.transform,
    d3.zoomIdentity.translate(tx, ty).scale(scale)
  )
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

/* 浮动控件容器：贴在图右下角，半透明背景，悬停时更明显 */
.floating-controls {
  position: absolute;
  right: 12px;
  bottom: 12px;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  border-radius: 8px;
  background: rgba(15, 23, 42, 0.78);
  backdrop-filter: blur(6px);
  border: 1px solid rgba(148, 163, 184, 0.18);
  z-index: 10;
  opacity: 0.65;
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
