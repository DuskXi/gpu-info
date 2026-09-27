<script setup>
import { computed, ref } from 'vue'
import { useMonitorData } from '../composables/useMonitorData'
import { bytes, number, percent, time } from '../format'
import { memoryAccounts } from '../accounting'
import { processPieGradient, processPieSliceAt, processVramComposition } from '../processVram'

const { snapshot } = useMonitorData()
const system = computed(() => snapshot.value?.system)
const gpus = computed(() => snapshot.value?.gpus ?? [])
const gpuCapacity = computed(() => new Map(gpus.value.map(gpu => [gpu.id, gpu.memory?.total])))
const processes = computed(() => snapshot.value?.processes ?? [])
const processCompositions = computed(() => new Map(
  gpus.value.map(gpu => [gpu.id, processVramComposition(snapshot.value, gpu.id)])
))
const accounting = computed(() => memoryAccounts(snapshot.value))
const displayState = computed(() => snapshot.value?.display_state)
const displayEvents = computed(() => [...(snapshot.value?.display_events ?? [])].reverse())
const query = ref('')
const gpuFilter = ref('all')
const sort = ref('used_memory')
const descending = ref(true)
const showAll = ref(false)
const tooltip = ref(null)

function devicePercent(gpu) {
  const total = gpu.memory?.total
  const used = gpu.memory?.used
  return Number.isFinite(total) && total > 0 && Number.isFinite(used)
    ? Math.max(0, Math.min(100, used / total * 100)) : 0
}
function deviceFree(gpu) {
  const memory = gpu.memory
  return Number.isFinite(memory?.free) ? memory.free
    : Number.isFinite(memory?.total) && Number.isFinite(memory?.used)
      ? Math.max(0, memory.total - memory.used) : null
}
function openTooltip(event, key, detail) {
  if (tooltip.value?.key === key) {
    tooltip.value = { ...tooltip.value, ...detail }
    return
  }
  const rect = event.currentTarget.getBoundingClientRect()
  const x = Number.isFinite(event.clientX) ? event.clientX : rect.left + rect.width / 2
  const y = Number.isFinite(event.clientY) ? event.clientY : rect.top + rect.height / 2
  const left = Math.max(12, Math.min(window.innerWidth - 266, x + 14))
  const top = y + 300 > window.innerHeight ? Math.max(12, y - 310) : y + 14
  tooltip.value = { ...detail, key, left, top }
}
function closeTooltip(key) {
  if (tooltip.value?.key === key) tooltip.value = null
}
function closeTooltipGroup(prefix) {
  if (tooltip.value?.key.startsWith(prefix)) tooltip.value = null
}
function processDetail(slice, context) {
  return {
    name: slice.name,
    subtitle: slice.pid == null ? '合并项' : `PID ${slice.pid}`,
    value: bytes(slice.value),
    share: `${number(slice.percent, 1, '%')} · ${context}`,
    note: slice.members ? '多个进程读数之和，可能包含共享资源' : slice.source,
    color: slice.color,
    members: slice.members?.slice(0, 6),
    remainingMembers: Math.max(0, (slice.members?.length ?? 0) - 6),
  }
}
function openProcessTooltip(event, key, slice, context) {
  openTooltip(event, key, processDetail(slice, context))
}
function onPieMove(event, slices, gpuId, kind) {
  const rect = event.currentTarget.getBoundingClientRect()
  const radius = rect.width / 2
  const index = processPieSliceAt(slices,
    (event.clientX - rect.left - radius) / radius,
    (event.clientY - rect.top - radius) / radius)
  const prefix = `${gpuId}:pie:${kind}:`
  if (index < 0) return closeTooltipGroup(prefix)
  openProcessTooltip(event, `${prefix}${index}`, slices[index],
    kind === 'zoom' ? '其余进程放大图' : '全体进程读数')
}
function openDeviceTooltip(event, gpu, kind) {
  const used = kind === 'used'
  const value = used ? gpu.memory?.used : deviceFree(gpu)
  openTooltip(event, `${gpu.id}:device:${kind}`, {
    name: used ? '整卡显存已用' : '整卡显存可用',
    subtitle: `GPU ${gpu.id} · NVML`,
    value: bytes(value),
    share: `${number(used ? devicePercent(gpu) : 100 - devicePercent(gpu), 1, '%')} · 物理显存总量`,
    note: '整卡读数；无法据此划分各进程的独占显存',
    color: used ? '#0072b2' : '#d8e6eb',
  })
}

const shownProcesses = computed(() => {
  const q = query.value.trim().toLowerCase()
  return processes.value
    .filter(p => gpuFilter.value === 'all' || p.gpu_id === Number(gpuFilter.value))
    .filter(p => !q || p.name?.toLowerCase().includes(q) || String(p.pid).includes(q))
    .sort((a, b) => {
      if (sort.value !== 'name') {
        const av = Number.isFinite(a[sort.value]) ? a[sort.value] : null
        const bv = Number.isFinite(b[sort.value]) ? b[sort.value] : null
        if (av == null || bv == null) return av == null ? (bv == null ? 0 : 1) : -1
      }
      const av = a[sort.value] ?? ''
      const bv = b[sort.value] ?? ''
      const order = typeof av === 'string'
        ? String(av).localeCompare(String(bv))
        : av - bv
      return descending.value ? -order : order
    })
})
const visibleProcesses = computed(() =>
  showAll.value || query.value ? shownProcesses.value : shownProcesses.value.slice(0, 20)
)

function sortBy(key) {
  descending.value = sort.value === key ? !descending.value : key !== 'name'
  sort.value = key
}
function counterOverCapacity(proc) {
  const capacity = gpuCapacity.value.get(proc.gpu_id)
  return Number.isFinite(proc.claimed_memory) && Number.isFinite(capacity)
    && proc.claimed_memory > capacity
}
function displayLabel(display) {
  const resolution = display.width && display.height ? `${display.width} × ${display.height}` : '分辨率未知'
  const refresh = Number.isFinite(display.refresh_hz) ? ` · ${display.refresh_hz.toFixed(1)} Hz` : ''
  const hdr = display.hdr_enabled == null ? '' : ` · HDR ${display.hdr_enabled ? '开' : '关'}`
  return `${resolution}${refresh}${hdr}`
}
</script>

<template>
  <div class="page">
    <section class="page-intro">
      <div>
        <p class="eyebrow">SYSTEM / OVERVIEW</p>
        <h1>GPU 状态<span class="title-period">.</span></h1>
        <p class="intro-note">直接查看设备负载、专用显存和占用进程。</p>
      </div>
      <div class="intro-count"><strong>{{ gpus.length }}</strong><span>NVIDIA GPU</span></div>
    </section>

    <div v-if="!snapshot" class="empty-state">正在等待第一帧硬件数据…</div>
    <template v-else>
      <p v-if="snapshot.errors?.length" class="notice">{{ snapshot.errors.join(' · ') }}</p>

      <section class="system-strip" aria-label="系统指标">
        <div class="strip-cell">
          <span class="label">CPU 占用</span>
          <strong>{{ percent(system?.cpu_percent) }}</strong>
        </div>
        <div class="strip-cell">
          <span class="label">系统内存</span>
          <strong>{{ percent(system?.memory?.percent) }}</strong>
          <small>{{ bytes(system?.memory?.used) }} / {{ bytes(system?.memory?.total) }}</small>
        </div>
        <div class="strip-cell">
          <span class="label">设备总显存</span>
          <strong>{{ bytes(gpus.reduce((sum, gpu) => sum + (gpu.memory?.total || 0), 0)) }}</strong>
        </div>
        <div class="strip-cell">
          <span class="label">占用进程</span>
          <strong>{{ processes.length }}</strong>
        </div>
      </section>

      <div class="overview-columns">
      <section class="section-block" aria-labelledby="gpu-heading">
        <div class="section-heading">
          <div><p class="eyebrow">01 / DEVICES</p><h2 id="gpu-heading">图形设备</h2></div>
          <router-link to="/detail" class="text-link">查看时间走势 <span aria-hidden="true">↗</span></router-link>
        </div>
        <div v-if="!gpus.length" class="empty-state">未检测到 NVIDIA GPU。请检查 NVML 和驱动状态。</div>
        <div v-else class="gpu-grid">
          <article v-for="gpu in gpus" :key="gpu.id" class="gpu-card">
            <div class="gpu-card-head">
              <span class="gpu-index">GPU {{ String(gpu.id).padStart(2, '0') }}</span>
              <span class="gpu-source">NVML</span>
            </div>
            <h3>{{ gpu.name }}</h3>
            <div class="hero-stat">
              <div><span class="label">专用显存已用</span><strong>{{ bytes(gpu.memory?.used) }}</strong></div>
              <span>{{ bytes(gpu.memory?.total) }} 总量</span>
            </div>
            <div class="vram-bars">
              <div class="vram-bar-heading"><span>整卡显存 <small>NVML</small></span><span>已用 {{ bytes(gpu.memory?.used) }} · 可用 {{ bytes(deviceFree(gpu)) }}</span></div>
              <div class="meter device-meter" role="group" :aria-label="`GPU ${gpu.id} 整卡显存；已用 ${bytes(gpu.memory?.used)}，可用 ${bytes(deviceFree(gpu))}`">
                <button v-if="devicePercent(gpu) > 0" type="button" class="device-used"
                  :style="{ width: `${devicePercent(gpu)}%` }" :aria-label="`整卡已用 ${bytes(gpu.memory?.used)}`"
                  @pointerenter="openDeviceTooltip($event, gpu, 'used')" @pointerleave="closeTooltip(`${gpu.id}:device:used`)"
                  @focus="openDeviceTooltip($event, gpu, 'used')" @blur="closeTooltip(`${gpu.id}:device:used`)"></button>
                <button v-if="devicePercent(gpu) < 100" type="button" class="device-free"
                  :style="{ width: `${100 - devicePercent(gpu)}%` }" :aria-label="`整卡可用 ${bytes(deviceFree(gpu))}`"
                  @pointerenter="openDeviceTooltip($event, gpu, 'free')" @pointerleave="closeTooltip(`${gpu.id}:device:free`)"
                  @focus="openDeviceTooltip($event, gpu, 'free')" @blur="closeTooltip(`${gpu.id}:device:free`)"></button>
              </div>
              <template v-if="processCompositions.get(gpu.id)?.slices.length">
                <div class="vram-bar-heading"><span>进程读数构成 <small>可重复计入</small></span><span>Σ {{ bytes(processCompositions.get(gpu.id).total) }}</span></div>
                <div class="meter process-meter" role="group" :aria-label="`GPU ${gpu.id} 进程显存读数构成；合计 ${bytes(processCompositions.get(gpu.id).total)}`">
                  <button v-for="(slice, index) in processCompositions.get(gpu.id).slices" :key="slice.pid ?? 'other'" type="button"
                    :style="{ width: `${slice.percent}%`, background: slice.color }"
                    :aria-label="`${slice.name} ${bytes(slice.value)}，占进程读数 ${number(slice.percent, 1, '%')}`"
                    @pointerenter="openProcessTooltip($event, `${gpu.id}:bar:${index}`, slice, '进程读数合计')"
                    @pointerleave="closeTooltip(`${gpu.id}:bar:${index}`)"
                    @focus="openProcessTooltip($event, `${gpu.id}:bar:${index}`, slice, '进程读数合计')"
                    @blur="closeTooltip(`${gpu.id}:bar:${index}`)"></button>
                </div>
              </template>
            </div>
            <div v-if="processCompositions.get(gpu.id)?.slices.length" class="vram-composition">
              <div class="vram-visuals">
                <figure class="vram-chart">
                  <div class="vram-pie" role="img"
                    :aria-label="`GPU ${gpu.id} 全体进程显存读数分布，${processCompositions.get(gpu.id).processCount} 个进程，合计 ${bytes(processCompositions.get(gpu.id).total)}`"
                    :style="{ background: processPieGradient(processCompositions.get(gpu.id).slices) }"
                    @pointermove="onPieMove($event, processCompositions.get(gpu.id).slices, gpu.id, 'all')"
                    @pointerleave="closeTooltipGroup(`${gpu.id}:pie:all:`)"></div>
                  <figcaption>全体进程</figcaption>
                </figure>
                <figure v-if="processCompositions.get(gpu.id).zoom" class="vram-chart">
                  <div class="vram-pie" role="img"
                    :aria-label="`排除 ${processCompositions.get(gpu.id).zoom.excluded} 后，其余进程 ${bytes(processCompositions.get(gpu.id).zoom.total)} 的放大分布`"
                    :style="{ background: processPieGradient(processCompositions.get(gpu.id).zoom.slices) }"
                    @pointermove="onPieMove($event, processCompositions.get(gpu.id).zoom.slices, gpu.id, 'zoom')"
                    @pointerleave="closeTooltipGroup(`${gpu.id}:pie:zoom:`)"></div>
                  <figcaption>其余进程放大<small>不含 {{ processCompositions.get(gpu.id).zoom.excluded }}</small></figcaption>
                </figure>
              </div>
              <div class="vram-legend">
                <span class="label">进程 VRAM 分布 <small>GPU {{ gpu.id }} · {{ time(snapshot?.processes_sampled_at) }}</small></span>
                <button v-for="(slice, index) in processCompositions.get(gpu.id).slices" :key="slice.pid ?? 'other'" type="button" class="vram-legend-row"
                  @pointerenter="openProcessTooltip($event, `${gpu.id}:legend:${index}`, slice, '进程读数合计')"
                  @pointerleave="closeTooltip(`${gpu.id}:legend:${index}`)"
                  @focus="openProcessTooltip($event, `${gpu.id}:legend:${index}`, slice, '进程读数合计')"
                  @blur="closeTooltip(`${gpu.id}:legend:${index}`)">
                  <i :style="{ background: slice.color }"></i>
                  <span :title="slice.pid == null ? slice.name : `${slice.name} · PID ${slice.pid}`">{{ slice.name }}<small v-if="slice.pid != null"> · {{ slice.pid }}</small></span>
                  <strong>{{ bytes(slice.value) }}</strong>
                </button>
                <p>进程读数合计 {{ bytes(processCompositions.get(gpu.id).total) }}；NVML 整卡已用 {{ bytes(processCompositions.get(gpu.id).deviceUsed) }}。{{ processCompositions.get(gpu.id).zoom ? '右图只比较排除最大进程后的剩余读数。' : '' }}共享资源可能重复计入。</p>
              </div>
            </div>
            <p v-else class="vram-composition-empty">暂无可用的进程显存读数</p>
            <div class="gpu-metrics">
              <div><span>GPU 负载</span><strong>{{ percent(gpu.utilization?.gpu) }}</strong></div>
              <div><span>显存控制器</span><strong>{{ percent(gpu.utilization?.memory) }}</strong></div>
              <div><span>温度</span><strong>{{ number(gpu.temperature, 0, '°C') }}</strong></div>
              <div><span>功耗</span><strong>{{ number(gpu.power?.usage, 0, ' W') }}</strong></div>
              <div><span>核心频率</span><strong>{{ number(gpu.clocks?.graphics, 0, ' MHz') }}</strong></div>
              <div><span>显存频率</span><strong>{{ number(gpu.clocks?.memory, 0, ' MHz') }}</strong></div>
            </div>
            <details class="device-details">
              <summary>设备信息</summary>
              <dl>
                <div><dt>驱动</dt><dd>{{ gpu.driver_version || '—' }}</dd></div>
                <div><dt>计算能力</dt><dd>{{ gpu.device_info?.compute_capability || '—' }}</dd></div>
                <div><dt>VBIOS</dt><dd>{{ gpu.device_info?.vbios || '—' }}</dd></div>
                <div><dt>UUID</dt><dd>{{ gpu.device_info?.uuid || '—' }}</dd></div>
                <div><dt>BAR1 已用</dt><dd>{{ bytes(gpu.bar1?.used) }}</dd></div>
                <div><dt>性能状态</dt><dd>{{ gpu.performance_state || '—' }}</dd></div>
              </dl>
            </details>
          </article>
        </div>
        <div v-if="displayState" class="display-panel">
          <div class="display-panel-head"><span class="label">当前显示配置</span><small>{{ displayState.count }} 个活动显示器 · 每 5 秒检查变化</small></div>
          <div v-if="!displayState.displays?.length" class="display-empty">当前没有活动显示路径</div>
          <div v-for="display in displayState.displays" :key="display.id" class="display-row">
            <strong>{{ display.name }}</strong>
            <span>{{ displayLabel(display) }}</span>
          </div>
          <details v-if="displayEvents.length" class="display-log">
            <summary>显示状态记录 <span>{{ displayEvents.length }} 条最近事件</span></summary>
            <ol>
              <li v-for="event in displayEvents" :key="`${event.timestamp}:${event.kind}`">
                <time>{{ time(event.timestamp) }}</time>
                <span>{{ event.kind === 'baseline' ? '启动基线' : '配置变化' }}</span>
                <strong>{{ event.previous && event.previous.count !== event.current.count ? `${event.previous.count} → ` : '' }}{{ event.current.count }} 屏</strong>
                <small>{{ event.current.displays.map(displayLabel).join(' / ') || '无活动显示器' }}</small>
                <small v-if="event.gpu_memory?.length">当时整卡已用 {{ event.gpu_memory.map(item => `GPU ${item.gpu_id} ${bytes(item.used)}`).join(' · ') }}</small>
              </li>
            </ol>
          </details>
        </div>
      </section>

      <section class="section-block process-section" aria-labelledby="process-heading">
        <div class="section-heading">
          <div><p class="eyebrow">02 / PROCESSES</p><h2 id="process-heading">进程显存</h2></div>
          <span class="section-aside">进程采样 {{ time(snapshot?.processes_sampled_at) }} · GPU / PID 去重</span>
        </div>
        <details v-for="account in accounting" :key="account.gpuId" class="accounting-disclosure">
          <summary>
            <span>GPU {{ account.gpuId }} · 显存口径核对</span>
            <strong>设备已用 {{ bytes(account.nvmlUsed) }} <span class="accounting-summary-source">NVML</span></strong>
          </summary>
          <div class="accounting-content">
            <div class="accounting-values">
              <div><span>NVML 设备已用</span><strong>{{ bytes(account.nvmlUsed) }}</strong></div>
              <div><span>Windows 设备专用使用</span><strong>{{ bytes(account.adapterUsed) }}</strong></div>
              <div><span>Σ {{ account.localCount }} 个进程本地用量 · 含重复</span><strong>{{ bytes(account.processLocal) }}</strong></div>
              <div><span>Σ {{ account.processCount }} 个进程专用计数 · 含重复</span><strong>{{ bytes(account.processClaimed) }}</strong></div>
            </div>
            <p>设备已用以 NVML 读数为准。Σ 仅用于排查计数差异，不能当作设备占用或剩余显存。本地用量可包含共享窗口表面；Windows 专用计数还可能异常膨胀。DWM 的独占显存不能从这些计数直接拆出。</p>
            <a href="https://devblogs.microsoft.com/directx/gpus-in-the-task-manager/" target="_blank" rel="noopener noreferrer">微软的计数器说明 ↗</a>
          </div>
        </details>
        <div class="table-toolbar">
          <input v-model="query" type="search" placeholder="搜索进程名或 PID" aria-label="搜索进程" />
          <select v-model="gpuFilter" aria-label="筛选 GPU">
            <option value="all">全部 GPU</option>
            <option v-for="gpu in gpus" :key="gpu.id" :value="String(gpu.id)">GPU {{ gpu.id }}</option>
          </select>
          <span>{{ shownProcesses.length }} 个进程</span>
        </div>
        <div class="table-wrap">
          <table>
            <thead><tr>
              <th><button @click="sortBy('name')">进程 {{ sort === 'name' ? (descending ? '↓' : '↑') : '' }}</button></th>
              <th><button @click="sortBy('pid')">PID {{ sort === 'pid' ? (descending ? '↓' : '↑') : '' }}</button></th>
              <th><button @click="sortBy('gpu_id')">设备 {{ sort === 'gpu_id' ? (descending ? '↓' : '↑') : '' }}</button></th>
              <th class="numeric"><button @click="sortBy('used_memory')" title="Windows Local Usage；可包含跨进程共享资源">本地用量 {{ sort === 'used_memory' ? (descending ? '↓' : '↑') : '' }}</button></th>
              <th class="numeric"><button @click="sortBy('claimed_memory')" title="Windows Dedicated Usage；可能包含共享资源，也可能异常膨胀">专用计数 {{ sort === 'claimed_memory' ? (descending ? '↓' : '↑') : '' }}</button></th>
            </tr></thead>
            <tbody>
              <tr v-for="proc in visibleProcesses" :key="`${proc.gpu_id}:${proc.pid}`">
                <td class="process-name">{{ proc.name }}</td>
                <td class="mono">{{ proc.pid }}</td>
                <td>GPU {{ proc.gpu_id }}</td>
                <td class="numeric strong">{{ bytes(proc.used_memory) }}</td>
                <td class="numeric" :class="{ 'counter-over-capacity': counterOverCapacity(proc) }">
                  {{ bytes(proc.claimed_memory) }}
                  <small v-if="counterOverCapacity(proc)">超物理容量</small>
                </td>
              </tr>
              <tr v-if="!visibleProcesses.length"><td colspan="5" class="empty-cell">没有匹配的占用进程</td></tr>
            </tbody>
          </table>
        </div>
        <button v-if="!query && shownProcesses.length > 20" class="table-more" @click="showAll = !showAll">
          {{ showAll ? '收起进程列表' : `显示全部 ${shownProcesses.length} 个进程` }} <span aria-hidden="true">{{ showAll ? '↑' : '↓' }}</span>
        </button>
        <p class="data-note">进程本地用量与专用计数均可能包含跨进程共享资源，不能相加。专用计数不是进程独占 VRAM；核显未并入 NVIDIA 列表。</p>
      </section>
      </div>
      <div v-if="tooltip" class="vram-tooltip" role="tooltip" :style="{ left: `${tooltip.left}px`, top: `${tooltip.top}px` }">
        <div class="vram-tooltip-head"><i :style="{ background: tooltip.color }"></i><strong>{{ tooltip.name }}</strong></div>
        <small>{{ tooltip.subtitle }}</small>
        <div class="vram-tooltip-reading"><strong>{{ tooltip.value }}</strong><span>{{ tooltip.share }}</span></div>
        <p>{{ tooltip.note }}</p>
        <div v-if="tooltip.members?.length" class="vram-tooltip-members">
          <div v-for="member in tooltip.members" :key="member.pid"><span>{{ member.name }} · {{ member.pid }}</span><strong>{{ bytes(member.value) }}</strong></div>
          <small v-if="tooltip.remainingMembers">另有 {{ tooltip.remainingMembers }} 个进程</small>
        </div>
      </div>
    </template>
  </div>
</template>
