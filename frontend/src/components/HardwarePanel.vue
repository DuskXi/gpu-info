<script setup>
import { computed, ref, watch } from 'vue'
import { bytes, number, time } from '../format'
import { hardwareGroups, hardwareTrend } from '../hardwareMetrics'

const props = defineProps({ gpu: Object, state: Object, history: { type: Array, default: () => [] },
  now: Number, detailed: Boolean })
const mode = ref('all')
const pending = ref(false)
const error = ref('')
const own = computed(() => props.state?.gpu_id === props.gpu.id)
const active = computed(() => own.value && props.state?.enabled)
const health = computed(() => props.gpu.training_health ?? {})
const groups = computed(() => own.value ? props.state.groups ?? {} : {})
const selectedGroups = computed(() => hardwareGroups.filter(group =>
  mode.value === 'all' || mode.value === group.key || groups.value[group.key]))
watch(() => props.state, value => { if (value?.enabled && own.value) mode.value = value.mode }, { immediate: true })
function fresh(group) {
  return active.value && Number.isFinite(groups.value[group]?.sampled_at)
    && (props.now ?? Date.now() / 1000) - groups.value[group].sampled_at <= 3
}
function value(group, key) { return groups.value[group]?.metrics?.[key]?.value }
function rate(group, key) {
  const raw = value(group, key)
  return Number.isFinite(raw) ? number(raw / 1e9, 2, ' Ginst/s') : '—'
}
const evidence = computed(() => {
  if (fresh('scalar') && value('scalar', 'fma_light_thread_ips') > 0) return 'CUDA 普通浮点管线有执行证据 · 已计入实际启用线程'
  if (!fresh('pipelines')) return '普通运算管线：等待新样本'
  if (value('pipelines', 'fma_light_ips') > 0) return 'CUDA 普通浮点管线有执行证据 · FMA Lite'
  return ['fma_heavy_ips', 'fma_light_ips', 'alu_ips'].some(key => value('pipelines', key) > 0)
    ? '普通运算管线正在执行指令' : '本窗口未检出普通运算指令'
})
const clockReasons = computed(() => {
  const h = health.value
  if (h.clock_reasons_raw == null) return '不可用'
  const reasons = [...(h.clock_reasons ?? [])]
  if (h.unknown_clock_reasons) reasons.push(`未知位 0x${h.unknown_clock_reasons.toString(16)}`)
  return reasons.join(' · ') || '未报告限制原因'
})
async function toggle() {
  pending.value = true
  error.value = ''
  try {
    const url = `/api/hardware-sampling?enabled=${!active.value}&gpu=${props.gpu.id}&mode=${mode.value}`
    const response = await fetch(url, { method: 'POST' })
    const result = await response.json()
    if (!response.ok) throw new Error(result.detail || `HTTP ${response.status}`)
  } catch (cause) { error.value = cause.message }
  finally { pending.value = false }
}
</script>

<template>
  <section class="hardware-panel" :class="{ 'hardware-panel-wide': detailed }" :aria-label="`GPU ${gpu.id} 训练硬件观测`">
    <div class="hardware-heading">
      <div><span class="label">GPU {{ gpu.id }} · 训练硬件观测</span><small>整卡性能计数器</small></div>
      <div class="hardware-controls">
        <select v-model="mode" :disabled="state?.enabled || pending" :aria-label="`GPU ${gpu.id} 硬件指标组`">
          <option value="all">轮换全部指标</option>
          <option v-for="group in hardwareGroups" :key="group.key" :value="group.key">连续 · {{ group.label }}</option>
        </select>
        <button type="button" role="switch" :aria-checked="Boolean(active)"
          :disabled="pending || (state?.enabled && !own)" @click="toggle">
          {{ pending ? '正在切换…' : active ? '停止硬件采样' : '开启硬件采样' }}
        </button>
      </div>
    </div>
    <p v-if="error || (own && state?.error)" class="notice" role="alert">{{ error || state.error }}</p>
    <p class="hardware-status">{{ active ? state.state === 'starting' ? '正在准备计数器…' : '采样中' : '硬件采样已关闭' }} · {{ evidence }}</p>
    <div v-if="active || Object.keys(groups).length" class="hardware-groups">
      <section v-for="group in selectedGroups" :key="group.key" class="hardware-group" :class="{ 'hardware-cached': !fresh(group.key) }">
        <header><strong>{{ group.label }}</strong><small>{{ !groups[group.key] ? '尚未采到' : fresh(group.key) ? '实时' : active ? state.mode === 'all' ? '轮换间隔 · 保留上次值' : '暂未更新 · 保留上次值' : '已停止 · 最后读数' }}<template v-if="groups[group.key]"> · {{ time(groups[group.key].sampled_at) }}</template></small></header>
        <div class="hardware-readings">
          <article v-for="metric in group.metrics" :key="metric.key" :title="groups[group.key]?.metrics?.[metric.key]?.counter">
            <span>{{ metric.label }}</span>
            <strong><RollingValue :value="metric.rateOnly ? rate(group.key, metric.key) : number(value(group.key, metric.key), 1, metric.lanes ? ' /32' : '%')" :amount="value(group.key, metric.key)" /></strong>
            <small v-if="metric.lanes">指令内部有效线程，非核心占用率</small>
            <small v-if="metric.rate"><RollingValue :value="rate(group.key, metric.rate)" :amount="value(group.key, metric.rate)" /></small>
            <svg v-if="detailed" viewBox="0 0 240 38" preserveAspectRatio="none" role="img" :aria-label="`${metric.label} 最近一分钟趋势，空白为未采样`"><path :d="hardwareTrend(history, state, group.key, metric.key, now ?? Date.now() / 1000)" /></svg>
          </article>
        </div>
        <small v-if="detailed && groups[group.key]" class="hardware-window">窗口内有效观测 <RollingValue :value="number(groups[group.key].observed_seconds, 2, ' s')" :amount="groups[group.key].observed_seconds" /> · <RollingValue :value="String(groups[group.key].samples)" :amount="groups[group.key].samples" /> 样本 · 未完成样本 <RollingValue :value="String(groups[group.key].incomplete_samples)" :amount="groups[group.key].incomplete_samples" /></small>
      </section>
    </div>
    <p class="data-note hardware-note">10 Hz 采样 · 每秒更新 · 最近 3 秒的时间加权均值。轮换模式每组约 3 秒，其他组保留带时间的旧值；固定组可连续观察。Ginst/s 为十亿条指令/秒，除明确标注的线程指令外均为 warp 指令。不是 TFLOPS。各管线百分比不能相加，也不能归属到某个进程。</p>
    <div class="hardware-health">
      <div><span>瞬时功率</span><strong><RollingValue :value="number(health.instant_power_w, 1, ' W')" :amount="health.instant_power_w" /></strong></div>
      <div><span>能量差分功率</span><strong><RollingValue :value="number(health.energy_window_w, 1, ' W')" :amount="health.energy_window_w" /></strong></div>
      <div><span>温度裕量</span><strong><RollingValue :value="number(health.temperature_margin_c, 0, ' °C')" :amount="health.temperature_margin_c" /></strong></div>
      <div><span>功率限制时间占比</span><strong><RollingValue :value="number(health.power_limit_time_percent, 1, '%')" :amount="health.power_limit_time_percent" /></strong></div>
    </div>
    <details class="hardware-extra">
      <summary>能量、限制原因与采样口径</summary>
      <p>整卡累计能量 <RollingValue :value="number(health.energy_total_j == null ? null : health.energy_total_j / 1e6, 3, ' MJ')" :amount="health.energy_total_j" />（驱动重载归零）；驱动保留显存 <RollingValue :value="bytes(health.driver_reserved_bytes)" :amount="health.driver_reserved_bytes" />。</p>
      <p>时钟原因：{{ clockReasons }}。软件 / 硬件温度限制时间占比：<RollingValue :value="number(health.software_thermal_time_percent, 1, '%')" :amount="health.software_thermal_time_percent" /> / <RollingValue :value="number(health.hardware_thermal_time_percent, 1, '%')" :amount="health.hardware_thermal_time_percent" />。</p>
      <p>PCIe Gen <RollingValue :value="String(health.pcie_generation ?? '—')" :amount="health.pcie_generation" /> × <RollingValue :value="String(health.pcie_width ?? '—')" :amount="health.pcie_width" /> · 重传累计 <RollingValue :value="String(health.pcie_replay_count ?? '—')" :amount="health.pcie_replay_count" />。</p>
      <p>NVML 状态持续更新，不依赖上方开关。CUDA 普通算术/整数管线有指令，能够证明它们被使用；不等于准确的 FP32 核心利用率。L1 低、PCIe 或显存带宽未满，都不能单独排除延迟、同步和指令依赖瓶颈。采样缓冲为 8 MiB，额外总显存仍需按实际负载观测。</p>
    </details>
  </section>
</template>

<style scoped>
.hardware-panel { padding: 22px 0; border-top: 1px solid var(--line); }
.hardware-heading, .hardware-controls, .hardware-group header { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.hardware-heading small { display: block; color: var(--soft); margin-top: 4px; }
.hardware-controls select, .hardware-controls button { border: 1px solid var(--line); padding: 8px 10px; background: var(--panel); color: var(--text); font-size: 12px; }
.hardware-controls button[aria-checked="true"] { background: var(--accent); color: white; }
.hardware-controls button:disabled { opacity: .5; cursor: default; }
.hardware-status { color: var(--accent); font-size: 12px; margin: 14px 0; }
.hardware-groups { display: grid; grid-template-columns: 1fr; gap: 16px; }
.hardware-group header { font-size: 12px; margin-bottom: 10px; }
.hardware-group header small, .hardware-window { color: var(--soft); font-size: 10px; }
.hardware-cached header small { color: var(--muted); }
.hardware-readings { display: grid; grid-template-columns: repeat(auto-fit, minmax(106px, 1fr)); gap: 10px; }
.hardware-readings article { border-left: 2px solid var(--accent); padding-left: 10px; min-width: 0; }
.hardware-cached article { border-left-color: var(--line); }
.hardware-readings article > span, .hardware-health > div > span { display: block; font-size: 11px; color: var(--muted); }
.hardware-readings strong { display: block; font-size: 23px; font-weight: 500; margin: 5px 0; }
.hardware-readings small { font-size: 11px; color: var(--accent); }
.hardware-readings svg { width: 100%; height: 38px; display: block; }
.hardware-readings path { fill: none; stroke: var(--accent); stroke-width: 1.5; vector-effect: non-scaling-stroke; }
.hardware-note { margin: 16px 0; line-height: 1.7; }
.hardware-health { display: grid; grid-template-columns: repeat(2, 1fr); gap: 14px; border-top: 1px solid var(--line); padding-top: 14px; }
.hardware-health strong { display: block; margin-top: 5px; font-size: 18px; font-weight: 500; }
.hardware-extra { font-size: 11px; color: var(--muted); line-height: 1.8; margin-top: 16px; }
.hardware-extra summary { cursor: pointer; }
.hardware-extra p { margin: 10px 0 0; }
.hardware-panel-wide .hardware-groups { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 24px; }
.hardware-panel-wide .hardware-group:only-child { grid-column: 1 / -1; }
.hardware-panel-wide .hardware-health { grid-template-columns: repeat(4, 1fr); }
@media(max-width: 900px) { .hardware-panel-wide .hardware-groups { grid-template-columns: 1fr; } .hardware-panel-wide .hardware-health { grid-template-columns: repeat(2, 1fr); } }
</style>
