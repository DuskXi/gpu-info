<script setup>
import { computed, ref } from 'vue'
import { useMonitorData } from '../composables/useMonitorData'
import HardwarePanel from '../components/HardwarePanel.vue'
import ProcessVramTable from '../components/ProcessVramTable.vue'
import { bytes, number, percent, time } from '../format'
import { memoryAccounts } from '../accounting'
import { processPieGradient, processPieSliceAt, processVramComposition } from '../processVram'

const { snapshot, history, now, hardwareSampling, hardwareHistory } = useMonitorData()
const system = computed(() => snapshot.value?.system)
const gpus = computed(() => snapshot.value?.gpus ?? [])
const multiGpu = computed(() => gpus.value.length > 1)
const processes = computed(() => snapshot.value?.processes ?? [])
const processCompositions = computed(() => new Map(
  gpus.value.map(gpu => [gpu.id, processVramComposition(snapshot.value, gpu.id)])
))
const accounting = computed(() => memoryAccounts(snapshot.value))
const displayState = computed(() => snapshot.value?.display_state)
const displayEvents = computed(() => [...(snapshot.value?.display_events ?? [])].reverse())
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
function openTooltip(event, key, target) {
  if (tooltip.value?.key === key) return
  const rect = event.currentTarget.getBoundingClientRect()
  const x = Number.isFinite(event.clientX) ? event.clientX : rect.left + rect.width / 2
  const y = Number.isFinite(event.clientY) ? event.clientY : rect.top + rect.height / 2
  const left = Math.max(12, Math.min(window.innerWidth - 266, x + 14))
  const top = y + 300 > window.innerHeight ? Math.max(12, y - 310) : y + 14
  tooltip.value = { ...target, key, left, top }
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
  openTooltip(event, key, {
    kind: 'process', gpuId: Number(key.split(':')[0]), pid: slice.pid,
    context, zoom: context === '其余进程放大图',
  })
}
function deviceDetail(gpu, kind) {
  const used = kind === 'used'
  const value = used ? gpu.memory?.used : deviceFree(gpu)
  return {
    name: used ? '整卡显存已用' : '整卡显存可用',
    subtitle: `GPU ${gpu.id} · NVML`,
    value: bytes(value),
    share: `${number(used ? devicePercent(gpu) : 100 - devicePercent(gpu), 1, '%')} · 物理显存总量`,
    note: '整卡读数；无法据此划分各进程的独占显存',
    color: used ? '#0072b2' : '#d8e6eb',
  }
}
const tooltipDetail = computed(() => {
  const target = tooltip.value
  if (!target) return null
  const gpu = gpus.value.find(item => item.id === target.gpuId)
  if (!gpu) return null
  if (target.kind === 'device') return { ...target, ...deviceDetail(gpu, target.deviceKind) }
  const composition = processCompositions.value.get(target.gpuId)
  const slices = target.zoom ? composition?.zoom?.slices : composition?.slices
  const slice = slices?.find(item => item.pid === target.pid)
  return slice ? { ...target, ...processDetail(slice, target.context) } : null
})
function onPieMove(event, slices, gpuId, kind) {
  const rect = event.currentTarget.getBoundingClientRect()
  const radius = rect.width / 2
  const index = processPieSliceAt(slices,
    (event.clientX - rect.left - radius) / radius,
    (event.clientY - rect.top - radius) / radius)
  const prefix = `${gpuId}:pie:${kind}:`
  if (index < 0) return closeTooltipGroup(prefix)
  openProcessTooltip(event, `${prefix}${slices[index].pid ?? 'other'}`, slices[index],
    kind === 'zoom' ? '其余进程放大图' : '全体进程读数')
}
function openDeviceTooltip(event, gpu, kind) {
  openTooltip(event, `${gpu.id}:device:${kind}`, {
    kind: 'device', gpuId: gpu.id, deviceKind: kind,
  })
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
      <div class="intro-count"><strong><RollingValue :value="gpus.length" /></strong><span>NVIDIA GPU</span></div>
    </section>

    <div v-if="!snapshot" class="empty-state">正在等待第一帧硬件数据…</div>
    <template v-else>
      <p v-if="snapshot.errors?.length" class="notice">{{ snapshot.errors.join(' · ') }}</p>

      <section class="system-strip" aria-label="系统指标">
        <div class="strip-cell">
          <span class="label">CPU 占用</span>
          <strong><RollingValue :value="percent(system?.cpu_percent)" :amount="system?.cpu_percent" /></strong>
        </div>
        <div class="strip-cell">
          <span class="label">系统内存</span>
          <strong><RollingValue :value="percent(system?.memory?.percent)" :amount="system?.memory?.percent" /></strong>
          <small><RollingValue :value="bytes(system?.memory?.used)" :amount="system?.memory?.used" /> / <RollingValue :value="bytes(system?.memory?.total)" :amount="system?.memory?.total" /></small>
        </div>
        <div class="strip-cell">
          <span class="label">设备总显存</span>
          <strong><RollingValue :value="bytes(gpus.reduce((sum, gpu) => sum + (gpu.memory?.total || 0), 0))" :amount="gpus.reduce((sum, gpu) => sum + (gpu.memory?.total || 0), 0)" /></strong>
        </div>
        <div class="strip-cell">
          <span class="label">占用进程</span>
          <strong><RollingValue :value="processes.length" /></strong>
        </div>
      </section>

      <div class="overview-columns" :class="{ 'overview-multi': multiGpu }">
      <section class="section-block" aria-labelledby="gpu-heading">
        <div class="section-heading">
          <div><p class="eyebrow">01 / DEVICES</p><h2 id="gpu-heading">图形设备</h2></div>
          <router-link to="/detail" class="text-link">查看时间走势 <span aria-hidden="true">↗</span></router-link>
        </div>
        <div v-if="!gpus.length" class="empty-state">未检测到 NVIDIA GPU。请检查 NVML 和驱动状态。</div>
        <div v-else class="gpu-grid" :class="{ 'gpu-grid-multi': multiGpu }" :style="multiGpu ? { '--gpu-cols': Math.min(gpus.length, 3) } : null">
          <div v-for="gpu in gpus" :key="gpu.id" class="gpu-column">
          <article class="gpu-card">
            <div class="gpu-card-head">
              <span class="gpu-index">GPU <RollingValue :value="String(gpu.id).padStart(2, '0')" :amount="gpu.id" /></span>
              <span class="gpu-source">NVML</span>
            </div>
            <h3>{{ gpu.name }}</h3>
            <div class="hero-stat">
              <div><span class="label">专用显存已用</span><strong><RollingValue :value="bytes(gpu.memory?.used)" :amount="gpu.memory?.used" /></strong></div>
              <span><RollingValue :value="bytes(gpu.memory?.total)" :amount="gpu.memory?.total" /> 总量</span>
            </div>
            <div class="vram-bars">
              <div class="vram-bar-heading"><span>整卡显存 <small>NVML</small></span><span>已用 <RollingValue :value="bytes(gpu.memory?.used)" :amount="gpu.memory?.used" /> · 可用 <RollingValue :value="bytes(deviceFree(gpu))" :amount="deviceFree(gpu)" /></span></div>
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
                <div class="vram-bar-heading"><span>进程读数构成 <small>可重复计入</small></span><span>Σ <RollingValue :value="bytes(processCompositions.get(gpu.id).total)" :amount="processCompositions.get(gpu.id).total" /></span></div>
                <div class="meter process-meter" role="group" :aria-label="`GPU ${gpu.id} 进程显存读数构成；合计 ${bytes(processCompositions.get(gpu.id).total)}`">
                  <button v-for="slice in processCompositions.get(gpu.id).slices" :key="slice.pid ?? 'other'" type="button"
                    :style="{ width: `${slice.percent}%`, background: slice.color }"
                    :aria-label="`${slice.name} ${bytes(slice.value)}，占进程读数 ${number(slice.percent, 1, '%')}`"
                    @pointerenter="openProcessTooltip($event, `${gpu.id}:bar:${slice.pid ?? 'other'}`, slice, '进程读数合计')"
                    @pointerleave="closeTooltip(`${gpu.id}:bar:${slice.pid ?? 'other'}`)"
                    @focus="openProcessTooltip($event, `${gpu.id}:bar:${slice.pid ?? 'other'}`, slice, '进程读数合计')"
                    @blur="closeTooltip(`${gpu.id}:bar:${slice.pid ?? 'other'}`)"></button>
                </div>
              </template>
            </div>
            <HardwarePanel :gpu="gpu" :state="hardwareSampling" :history="hardwareHistory" :now="now / 1000" />
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
                  <figcaption>其余进程放大<small>不含 <RollingValue :value="processCompositions.get(gpu.id).zoom.excluded" /></small></figcaption>
                </figure>
              </div>
              <div class="vram-legend">
                <span class="label">进程 VRAM 分布 <small>GPU <RollingValue :value="gpu.id" /> · <RollingValue :value="time(snapshot?.processes_sampled_at)" :amount="snapshot?.processes_sampled_at" /></small></span>
                <button v-for="slice in processCompositions.get(gpu.id).slices" :key="slice.pid ?? 'other'" type="button" class="vram-legend-row"
                  @pointerenter="openProcessTooltip($event, `${gpu.id}:legend:${slice.pid ?? 'other'}`, slice, '进程读数合计')"
                  @pointerleave="closeTooltip(`${gpu.id}:legend:${slice.pid ?? 'other'}`)"
                  @focus="openProcessTooltip($event, `${gpu.id}:legend:${slice.pid ?? 'other'}`, slice, '进程读数合计')"
                  @blur="closeTooltip(`${gpu.id}:legend:${slice.pid ?? 'other'}`)">
                  <i :style="{ background: slice.color }"></i>
                  <span :title="slice.pid == null ? slice.name : `${slice.name} · PID ${slice.pid}`"><RollingValue :value="slice.name" /><small v-if="slice.pid != null"> · <RollingValue :value="slice.pid" /></small></span>
                  <strong><RollingValue :value="bytes(slice.value)" :amount="slice.value" /></strong>
                </button>
                <p>进程读数合计 <RollingValue :value="bytes(processCompositions.get(gpu.id).total)" :amount="processCompositions.get(gpu.id).total" />；NVML 整卡已用 <RollingValue :value="bytes(processCompositions.get(gpu.id).deviceUsed)" :amount="processCompositions.get(gpu.id).deviceUsed" />。{{ processCompositions.get(gpu.id).zoom ? '右图只比较排除最大进程后的剩余读数。' : '' }}共享资源可能重复计入。</p>
              </div>
            </div>
            <p v-else class="vram-composition-empty">暂无可用的进程显存读数</p>
            <div class="gpu-metrics">
              <div><span>GPU 负载</span><strong><RollingValue :value="percent(gpu.utilization?.gpu)" :amount="gpu.utilization?.gpu" /></strong></div>
              <div><span>显存控制器</span><strong><RollingValue :value="percent(gpu.utilization?.memory)" :amount="gpu.utilization?.memory" /></strong></div>
              <div><span>温度</span><strong><RollingValue :value="number(gpu.temperature, 0, '°C')" :amount="gpu.temperature" /></strong></div>
              <div><span>功耗</span><strong><RollingValue :value="number(gpu.power?.usage, 0, ' W')" :amount="gpu.power?.usage" /></strong></div>
              <div><span>核心频率</span><strong><RollingValue :value="number(gpu.clocks?.graphics, 0, ' MHz')" :amount="gpu.clocks?.graphics" /></strong></div>
              <div><span>显存频率</span><strong><RollingValue :value="number(gpu.clocks?.memory, 0, ' MHz')" :amount="gpu.clocks?.memory" /></strong></div>
            </div>
            <details class="device-details">
              <summary>设备信息</summary>
              <dl>
                <div><dt>驱动</dt><dd>{{ gpu.driver_version || '—' }}</dd></div>
                <div><dt>计算能力</dt><dd>{{ gpu.device_info?.compute_capability || '—' }}</dd></div>
                <div><dt>VBIOS</dt><dd>{{ gpu.device_info?.vbios || '—' }}</dd></div>
                <div><dt>UUID</dt><dd>{{ gpu.device_info?.uuid || '—' }}</dd></div>
                <div><dt>BAR1 已用</dt><dd><RollingValue :value="bytes(gpu.bar1?.used)" :amount="gpu.bar1?.used" /></dd></div>
                <div><dt>性能状态</dt><dd>{{ gpu.performance_state || '—' }}</dd></div>
              </dl>
            </details>
          </article>
          <ProcessVramTable v-if="multiGpu" :gpu-id="gpu.id" :processes="processes" :gpus="gpus"
            :accounting="accounting" :sampled-at="snapshot?.processes_sampled_at" />
          </div>
        </div>
        <div v-if="displayState" class="display-panel">
          <div class="display-panel-head"><span class="label">当前显示配置</span><small><RollingValue :value="displayState.count" /> 个活动显示器 · 每 5 秒检查变化</small></div>
          <div v-if="!displayState.displays?.length" class="display-empty">当前没有活动显示路径</div>
          <div v-for="display in displayState.displays" :key="display.id" class="display-row">
            <strong>{{ display.name }}</strong>
            <span><RollingValue :value="displayLabel(display)" /></span>
          </div>
          <details v-if="displayEvents.length" class="display-log">
            <summary>显示状态记录 <span><RollingValue :value="displayEvents.length" /> 条最近事件</span></summary>
            <ol>
              <li v-for="event in displayEvents" :key="`${event.timestamp}:${event.kind}`">
                <time><RollingValue :value="time(event.timestamp)" :amount="event.timestamp" /></time>
                <span>{{ event.kind === 'baseline' ? '启动基线' : '配置变化' }}</span>
                <strong><template v-if="event.previous && event.previous.count !== event.current.count"><RollingValue :value="event.previous.count" /> → </template><RollingValue :value="event.current.count" /> 屏</strong>
                <small><RollingValue :value="event.current.displays.map(displayLabel).join(' / ') || '无活动显示器'" /></small>
                <small v-if="event.gpu_memory?.length">当时整卡已用 <RollingValue :value="event.gpu_memory.map(item => `GPU ${item.gpu_id} ${bytes(item.used)}`).join(' · ')" /></small>
              </li>
            </ol>
          </details>
        </div>
      </section>

      <ProcessVramTable v-if="!multiGpu" :processes="processes" :gpus="gpus" :accounting="accounting"
        :sampled-at="snapshot?.processes_sampled_at" />
      </div>
      <div v-if="tooltipDetail" class="vram-tooltip" role="tooltip" :style="{ left: `${tooltipDetail.left}px`, top: `${tooltipDetail.top}px` }">
        <div class="vram-tooltip-head"><i :style="{ background: tooltipDetail.color }"></i><strong><RollingValue :value="tooltipDetail.name" /></strong></div>
        <small><RollingValue :value="tooltipDetail.subtitle" /></small>
        <div class="vram-tooltip-reading"><strong><RollingValue :value="tooltipDetail.value" /></strong><span><RollingValue :value="tooltipDetail.share" /></span></div>
        <p>{{ tooltipDetail.note }}</p>
        <div v-if="tooltipDetail.members?.length" class="vram-tooltip-members">
          <div v-for="member in tooltipDetail.members" :key="member.pid"><span>{{ member.name }} · <RollingValue :value="member.pid" /></span><strong><RollingValue :value="bytes(member.value)" :amount="member.value" /></strong></div>
          <small v-if="tooltipDetail.remainingMembers">另有 <RollingValue :value="tooltipDetail.remainingMembers" /> 个进程</small>
        </div>
      </div>
    </template>
  </div>
</template>
