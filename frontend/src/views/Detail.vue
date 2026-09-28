<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { Line } from 'vue-chartjs'
import {
  Chart as ChartJS, CategoryScale, LinearScale, LogarithmicScale, PointElement,
  LineElement, Tooltip, Legend,
} from 'chart.js'
import { useMonitorData, procKey } from '../composables/useMonitorData'
import { useActivityProbe } from '../composables/useActivityProbe'
import { bytes, number, percent } from '../format'
import { topProcessTargets } from '../processComparison'
import ProcessPicker from '../components/ProcessPicker.vue'

ChartJS.register(CategoryScale, LinearScale, LogarithmicScale, PointElement, LineElement, Tooltip, Legend)

const { snapshot, history } = useMonitorData()
const mode = ref('gpu')
const selectedGpu = ref('all')
const selectedProcess = ref('')
const processManuallySelected = ref(false)
const gpuMetric = ref('vram')
const processMetric = ref('used_memory')
const probeGpu = ref('0')
const probeDetails = ref(null)
const compareTop = ref(false)
const topCount = ref(5)
const logMemoryScale = ref(true)
const {
  activityProbe, pipelineProbe, probeBusy, probeError, pipelineError,
  autoEnabled, autoGpuId, autoKind, toggleAuto, loadLatest,
} = useActivityProbe()
watch(autoGpuId, gpuId => {
  if (gpuId !== null) probeGpu.value = String(gpuId)
}, { immediate: true })
const activityGroups = [
  { label: '工作进入与并行度', metrics: [
    { key: 'gr_active', label: '图形 / 计算前端活跃' },
    { key: 'compute_sync', label: '同步计算派发' },
    { key: 'compute_async', label: '异步计算派发' },
    { key: 'compute_warps', label: '计算 warp 在飞' },
    { key: 'sm_active', label: 'SM 有 warp 驻留' },
    { key: 'unallocated_warps', label: '活跃 SM 未分配 warp 槽位' },
  ] },
  { label: '执行管线', metrics: [
    { key: 'sm_issue', label: 'SM 指令发射' },
    { key: 'tensor_active', label: 'Tensor 管线活跃' },
  ] },
  { label: '显存与图形', metrics: [
    { key: 'dram_read', label: '显存读取带宽' },
    { key: 'dram_write', label: '显存写入带宽' },
    { key: 'graphics_vertex', label: '图形 · 顶点 / 几何 warp' },
    { key: 'graphics_pixel', label: '图形 · 像素 warp' },
  ] },
]
const activityMetrics = activityGroups.flatMap(group =>
  group.metrics.map(metric => ({ ...metric, group: group.label })))
const pipelineMetrics = [
  { key: 'tensor_active', label: 'Tensor', group: '矩阵运算' },
  { key: 'fma_light', label: 'FMA Light', group: '标量算术' },
  { key: 'fma_heavy', label: 'FMA Heavy', group: '标量算术' },
  { key: 'alu', label: 'ALU', group: '整数 / 逻辑' },
  { key: 'sfu', label: 'SFU / XU', group: '特殊函数 / 类型转换' },
]

onMounted(loadLatest)

function runActivityProbe() {
  if (probeDetails.value) probeDetails.value.open = true
  toggleAuto(probeGpu.value)
}
function runPipelineProbe() {
  if (probeDetails.value) probeDetails.value.open = true
  toggleAuto(probeGpu.value, 'pipelines')
}

const gpuMetrics = [
  { key: 'vram', label: '专用显存', group: '内存', unit: 'GB', read: g => g.memory?.used == null ? null : g.memory.used / 1073741824 },
  { key: 'vram_percent', label: '显存占用率', group: '内存', unit: '%', read: g => g.memory?.percent },
  { key: 'gpu_load', label: 'GPU 负载', group: '负载', unit: '%', read: g => g.utilization?.gpu },
  { key: 'memory_load', label: '显存控制器', group: '负载', unit: '%', read: g => g.utilization?.memory },
  { key: 'temperature', label: '温度', group: '状态', unit: '°C', read: g => g.temperature },
  { key: 'power', label: '功耗', group: '状态', unit: 'W', read: g => g.power?.usage },
  { key: 'clock', label: '核心频率', group: '频率', unit: 'MHz', read: g => g.clocks?.graphics },
  { key: 'memory_clock', label: '显存频率', group: '频率', unit: 'MHz', read: g => g.clocks?.memory },
  { key: 'encoder', label: '编码器', group: '引擎', unit: '%', read: g => g.engines?.encoder },
  { key: 'decoder', label: '解码器', group: '引擎', unit: '%', read: g => g.engines?.decoder },
]
const processMetrics = [
  { key: 'used_memory', label: '本地用量', group: '内存', unit: 'MB', read: (p) => p?.used_memory == null ? null : p.used_memory / 1048576 },
  { key: 'claimed_memory', label: '专用计数', group: '内存', unit: 'MB', read: (p) => p?.claimed_memory == null ? null : p.claimed_memory / 1048576 },
  { key: 'committed_memory', label: 'Windows 提交量', group: '内存', unit: 'MB', read: (p) => p?.committed_memory == null ? null : p.committed_memory / 1048576 },
  { key: 'sm_util', label: 'SM 负载', group: '负载', unit: '%', read: (_, u) => u?.sm_util },
  { key: 'mem_util', label: '显存控制器', group: '负载', unit: '%', read: (_, u) => u?.mem_util },
  { key: 'wddm_3d', label: 'WDDM 3D 队列', group: '引擎', unit: '%', read: p => p?.engine_utilization?.['3d'] },
  { key: 'wddm_compute', label: 'WDDM Compute 队列', group: '引擎', unit: '%', read: p => p?.engine_utilization?.compute },
  { key: 'wddm_copy', label: 'WDDM Copy 引擎', group: '引擎', unit: '%', read: p => p?.engine_utilization?.copy },
  { key: 'enc_util', label: '编码器', group: '引擎', unit: '%', read: (_, u) => u?.enc_util },
  { key: 'dec_util', label: '解码器', group: '引擎', unit: '%', read: (_, u) => u?.dec_util },
]
const metrics = computed(() => mode.value === 'gpu' ? gpuMetrics : processMetrics)
const activeMetric = computed(() => metrics.value.find(m => m.key === (mode.value === 'gpu' ? gpuMetric.value : processMetric.value)))
const logarithmic = computed(() => mode.value === 'process' && compareTop.value
  && activeMetric.value?.group === '内存' && logMemoryScale.value)
const gpus = computed(() => snapshot.value?.gpus ?? [])
const processOptions = computed(() => {
  const options = new Map()
  for (const p of snapshot.value?.processes ?? []) {
    options.set(procKey(p.pid, p.gpu_id), { ...p })
  }
  for (const p of snapshot.value?.process_utilization ?? []) {
    const key = procKey(p.pid, p.gpu_id)
    const existing = options.get(key)
    options.set(key, {
      ...p,
      ...existing,
      sm_util: p.sm_util,
      gpu_percent: existing?.gpu_percent ?? p.gpu_percent ?? p.sm_util,
    })
  }
  return [...options.entries()].map(([key, p]) => ({ key, ...p }))
})
watch(processOptions, options => {
  const current = options.find(option => option.key === selectedProcess.value)
  if (!current) processManuallySelected.value = false
  if (!current || (!processManuallySelected.value && current.used_memory == null)) {
    const withMemory = options
      .filter(option => Number.isFinite(option.used_memory))
      .sort((a, b) => b.used_memory - a.used_memory)[0]
    selectedProcess.value = (withMemory || current || options[0])?.key || ''
  }
}, { immediate: true })
const processKey = computed(() => selectedProcess.value || processOptions.value[0]?.key || '')
const selectedProcessInfo = computed(() => processOptions.value.find(p => p.key === processKey.value))
const selectedProcessGpu = computed(() => gpus.value.find(g => g.id === selectedProcessInfo.value?.gpu_id))
const counterExceedsCapacity = computed(() =>
  Number.isFinite(selectedProcessInfo.value?.claimed_memory)
  && Number.isFinite(selectedProcessGpu.value?.memory?.total)
  && selectedProcessInfo.value.claimed_memory > selectedProcessGpu.value.memory.total
)
const palette = [
  '#007c96', '#425ec4', '#b36116', '#7953aa', '#b34e66',
  '#2d7862', '#a33c38', '#4c7291', '#67731f', '#6c5289',
]
const processColors = new Map()

function coloredTargets(items) {
  const used = new Set()
  return items.map((item, index) => {
    if (mode.value === 'gpu') return { ...item, color: palette[index % palette.length] }
    let color = processColors.get(item.id)
    if (!color || used.has(color)) {
      color = palette.find(candidate => !used.has(candidate)) || palette[index % palette.length]
      processColors.set(item.id, color)
    }
    used.add(color)
    return { ...item, color }
  })
}

function valueAt(frame, id) {
  if (mode.value === 'gpu') {
    const gpu = frame.gpus?.find(item => item.id === id)
    return gpu ? activeMetric.value?.read(gpu) ?? null : null
  }
  const p = frame.processes?.find(item => procKey(item.pid, item.gpu_id) === id)
  const u = frame.process_utilization?.find(item => procKey(item.pid, item.gpu_id) === id)
  return activeMetric.value?.read(p, u) ?? null
}

const targets = computed(() => {
  if (mode.value === 'process') {
    if (compareTop.value) {
      return coloredTargets(topProcessTargets(
        processOptions.value, activeMetric.value, selectedGpu.value, topCount.value,
      ))
    }
    return processKey.value ? coloredTargets([{
      id: processKey.value,
      name: `${selectedProcessInfo.value?.name || '未知进程'} · ${selectedProcessInfo.value?.pid ?? ''}`,
    }]) : []
  }
  return coloredTargets(gpus.value
    .filter(g => selectedGpu.value === 'all' || g.id === Number(selectedGpu.value))
    .map(g => ({ id: g.id, name: `GPU ${g.id}` })))
})
const chartData = computed(() => ({
  labels: history.value.map(frame => new Date(frame.timestamp * 1000).toLocaleTimeString('zh-CN', { hour12: false })),
  datasets: targets.value.map(target => ({
    label: target.name,
    data: history.value.map(frame => {
      const value = valueAt(frame, target.id)
      return logarithmic.value && value != null && value <= 0 ? null : value
    }),
    borderColor: target.color,
    backgroundColor: target.color,
    pointRadius: 0,
    pointHoverRadius: 4,
    borderWidth: 2,
    tension: 0.18,
    spanGaps: false,
  })),
}))
const chartOptions = computed(() => ({
  responsive: true,
  maintainAspectRatio: false,
  animation: false,
  interaction: { intersect: false, mode: 'index' },
  scales: {
    x: {
      grid: { display: false },
      ticks: { maxTicksLimit: 8, maxRotation: 0, color: '#526e7a' },
      border: { color: '#b7cdd5' },
    },
    y: {
      type: logarithmic.value ? 'logarithmic' : 'linear',
      beginAtZero: !logarithmic.value && activeMetric.value?.unit === '%',
      suggestedMax: activeMetric.value?.unit === '%' ? 100 : undefined,
      grid: { color: '#d8e6eb' },
      ticks: {
        color: '#526e7a', maxTicksLimit: 10,
        callback: value => {
          if (logarithmic.value) {
            const numeric = Number(value)
            const mantissa = numeric / 10 ** Math.floor(Math.log10(numeric))
            if (![1, 2, 5].some(mark => Math.abs(mantissa - mark) < 0.001)) return ''
          }
          return `${value} ${activeMetric.value?.unit ?? ''}`
        },
      },
      border: { display: false },
    },
  },
  plugins: {
    legend: { display: targets.value.length > 1 && !(mode.value === 'process' && compareTop.value), position: 'bottom', labels: { color: '#526e7a', usePointStyle: true, boxWidth: 8 } },
    tooltip: { backgroundColor: '#122d3b', titleColor: '#fbfeff', bodyColor: '#fbfeff', displayColors: targets.value.length > 1 },
  },
}))
const readings = computed(() => targets.value.map(target => {
  const data = history.value.map(frame => valueAt(frame, target.id))
  const values = data.filter(v => v != null && Number.isFinite(v))
  return {
    ...target,
    current: data.at(-1) ?? null,
    min: values.length ? Math.min(...values) : null,
    max: values.length ? Math.max(...values) : null,
    color: target.color,
  }
}))
function display(value) {
  return number(value, activeMetric.value?.unit === 'GB' ? 2 : 0, ` ${activeMetric.value?.unit || ''}`)
}
function chooseMetric(key) {
  if (mode.value === 'gpu') gpuMetric.value = key
  else processMetric.value = key
}
function chooseProcess(key) {
  selectedProcess.value = key
  processManuallySelected.value = true
}
</script>

<template>
  <div class="page detail-page">
    <section class="page-intro">
      <div>
        <p class="eyebrow">ANALYSIS / LAST 120 SAMPLES</p>
        <h1>趋势分析<span class="title-period">.</span></h1>
        <p class="intro-note">一次观察一个指标。缺失采样保留为空白，不补成零。</p>
      </div>
      <div class="intro-count"><strong><RollingValue :value="history.length" /></strong><span>采样点</span></div>
    </section>

    <section class="activity-probe section-block" aria-labelledby="activity-heading">
      <div class="section-heading">
        <div><p class="eyebrow">SHORT WINDOWS / HARDWARE COUNTERS</p><h2 id="activity-heading">计算单元活动</h2></div>
        <div class="activity-probe-controls">
          <label for="probe-gpu">采样设备</label>
          <select id="probe-gpu" v-model="probeGpu" :disabled="probeBusy || autoEnabled">
            <option v-for="gpu in gpus" :key="gpu.id" :value="String(gpu.id)">GPU {{ gpu.id }}</option>
          </select>
          <button type="button" role="switch" :aria-checked="autoEnabled && autoKind === 'general'" :disabled="(probeBusy && !autoEnabled) || (autoEnabled && autoKind !== 'general') || !gpus.length" @click="runActivityProbe">
            {{ autoEnabled && autoKind === 'general' ? '停止概况采样' : '持续采样概况' }}
          </button>
          <button type="button" role="switch" :aria-checked="autoEnabled && autoKind === 'pipelines'" :disabled="(probeBusy && !autoEnabled) || (autoEnabled && autoKind !== 'pipelines') || !gpus.length" @click="runPipelineProbe">
            {{ autoEnabled && autoKind === 'pipelines' ? '停止管线采样' : '持续采样管线' }}
          </button>
        </div>
      </div>
      <details ref="probeDetails" class="activity-probe-details">
        <summary>查看设备级采样细节 <span v-if="activityProbe">SM {{ number(activityProbe.metrics?.sm_active?.mean_percent, 1, '%') }} · Tensor {{ number(activityProbe.metrics?.tensor_active?.mean_percent, 1, '%') }}</span><span v-if="pipelineProbe">ALU {{ number(pipelineProbe.metrics?.alu?.mean_percent, 1, '%') }} · FMA Heavy {{ number(pipelineProbe.metrics?.fma_heavy?.mean_percent, 1, '%') }}</span><span v-if="probeError || pipelineError">采样失败 · 展开查看</span></summary>
      <p class="activity-probe-intro">两种持续模式互斥，每轮采集整卡 2 秒：概况 100 Hz、SM 管线 10 Hz，完成后休息至少 15 秒。最近 3 轮按采样数计算滚动均值，轮间未采样。关掉开关后，正在进行的一轮仍会完成。</p>
      <p v-if="probeError" class="notice" role="alert">{{ probeError }}</p>
      <div v-if="activityProbe" class="activity-grid activity-grid-detailed">
        <article v-for="metric in activityMetrics" :key="metric.key" class="activity-reading" :class="{ 'activity-reading-tensor': metric.key === 'tensor_active' }">
          <small class="activity-reading-group">{{ metric.group }}</small>
          <span>{{ metric.label }}</span>
          <strong><RollingValue :value="number(activityProbe.metrics?.[metric.key]?.mean_percent, 1, '%')" :amount="activityProbe.metrics?.[metric.key]?.mean_percent" /></strong>
          <small>{{ activityProbe.rolling ? '滚动均值' : '窗口平均' }} <template v-if="activityProbe.metrics?.[metric.key]?.p90_percent !== undefined">· 本轮 P90 <RollingValue :value="number(activityProbe.metrics?.[metric.key]?.p90_percent, 1, '%')" :amount="activityProbe.metrics?.[metric.key]?.p90_percent" /> </template>· 本轮峰值 <RollingValue :value="number(activityProbe.metrics?.[metric.key]?.peak_percent, 1, '%')" :amount="activityProbe.metrics?.[metric.key]?.peak_percent" /></small>
          <small>有活动的采样 <RollingValue :value="activityProbe.metrics?.[metric.key]?.active_samples ?? '—'" /> / <RollingValue :value="activityProbe.metrics?.[metric.key]?.samples ?? '—'" /></small>
        </article>
      </div>
      <div v-if="activityProbe?.process_context?.length" class="activity-process-context">
        <span>同期进程 SM 负载 <small>独立读数 · 无法将上方 Tensor 活动归属到某个进程</small></span>
        <div v-for="process in activityProbe.process_context" :key="process.pid">
          <strong>{{ process.name }} <small>PID <RollingValue :value="process.pid" /></small></strong>
          <span>平均 <RollingValue :value="number(process.sm_mean_percent, 1, '%')" :amount="process.sm_mean_percent" /> · 峰值 <RollingValue :value="number(process.sm_peak_percent, 1, '%')" :amount="process.sm_peak_percent" /> · VRAM <RollingValue :value="bytes(process.used_memory)" :amount="process.used_memory" /></span>
        </div>
      </div>
      <p v-if="!activityProbe && !probeBusy" class="activity-probe-empty">概况尚无采样。</p>
      <p class="data-note">{{ activityProbe ? `GPU ${activityProbe.gpu_id} · ${new Date(activityProbe.captured_at * 1000).toLocaleTimeString('zh-CN', { hour12: false })} 的短时采样。` : '' }}{{ activityProbe?.rolling ? `均值汇总最近 ${activityProbe.rolling.windows} 轮、有效窗口约 ${activityProbe.rolling.sampled_seconds} 秒；轮间没有观测。` : '' }}P90、峰值和有活动的采样数只描述最新一轮。不同百分比不能相加，也不能换算为实际 TFLOPS。计算派发和计算 warp 证明有计算任务，却不等于 FP32/整数 CUDA Core 利用率；Tensor 低也不能单独证明 CUDA Core 高。探针不会把其他程序的活动归给训练进程。</p>
      <div class="pipeline-probe">
        <div class="pipeline-probe-heading"><strong>AD10x 指令管线</strong><span>短时持续采样 · GPU 整卡</span></div>
        <p class="activity-probe-intro">从实际执行的指令管线观察 Tensor、FMA、ALU 和 SFU/XU。FMA 也可执行部分整数乘法，XU 也处理类型转换；这些值不是精确的 FP32 核心利用率。各管线峰值基准不同，百分比不能相加。</p>
        <p v-if="pipelineError" class="notice" role="alert">{{ pipelineError }}</p>
        <div v-if="pipelineProbe" class="activity-grid activity-grid-pipelines">
          <article v-for="metric in pipelineMetrics" :key="metric.key" class="activity-reading" :class="{ 'activity-reading-tensor': metric.key === 'tensor_active' }">
            <small class="activity-reading-group">{{ metric.group }}</small>
            <span>{{ metric.label }}</span>
            <strong><RollingValue :value="number(pipelineProbe.metrics?.[metric.key]?.mean_percent, 1, '%')" :amount="pipelineProbe.metrics?.[metric.key]?.mean_percent" /></strong>
            <small>{{ pipelineProbe.rolling ? '滚动均值' : '窗口平均' }} · 本轮峰值 <RollingValue :value="number(pipelineProbe.metrics?.[metric.key]?.peak_percent, 1, '%')" :amount="pipelineProbe.metrics?.[metric.key]?.peak_percent" /></small>
            <small>有活动的采样 <RollingValue :value="pipelineProbe.metrics?.[metric.key]?.active_samples ?? '—'" /> / <RollingValue :value="pipelineProbe.metrics?.[metric.key]?.samples ?? '—'" /></small>
          </article>
        </div>
        <div v-if="pipelineProbe?.process_context?.length" class="activity-process-context">
          <span>同期进程 SM 负载 <small>独立读数 · 不能据此分配整卡指令管线值</small></span>
          <div v-for="process in pipelineProbe.process_context" :key="process.pid">
            <strong>{{ process.name }} <small>PID <RollingValue :value="process.pid" /></small></strong>
            <span>平均 <RollingValue :value="number(process.sm_mean_percent, 1, '%')" :amount="process.sm_mean_percent" /> · 峰值 <RollingValue :value="number(process.sm_peak_percent, 1, '%')" :amount="process.sm_peak_percent" /> · VRAM <RollingValue :value="bytes(process.used_memory)" :amount="process.used_memory" /></span>
          </div>
        </div>
        <p v-if="!pipelineProbe && !probeBusy" class="activity-probe-empty">尚无 SM 管线采样。使用上方“持续采样管线”开关，支持 AD10x（RTX 40 系列）设备。</p>
        <p v-if="pipelineProbe" class="data-note">GPU {{ pipelineProbe.gpu_id }} · {{ new Date(pipelineProbe.captured_at * 1000).toLocaleTimeString('zh-CN', { hour12: false }) }}。{{ pipelineProbe.rolling ? `均值汇总最近 ${pipelineProbe.rolling.windows} 轮、有效窗口约 ${pipelineProbe.rolling.sampled_seconds} 秒；轮间没有观测。` : `最新一轮 ${pipelineProbe.duration_seconds} 秒、10 Hz 的粗粒度窗口。` }}峰值和有活动的采样数只描述最新一轮。当前计数器不提供进程归属；训练进程的 SM 负载可作同期线索，不能把整卡 FMA 或 Tensor 值直接分配给它。指令条数在本机导出时曾溢出，故仅展示管线吞吐率。</p>
      </div>
      </details>
    </section>

    <div class="mode-tabs" role="tablist" aria-label="分析对象">
      <button role="tab" :aria-selected="mode === 'gpu'" :class="{ active: mode === 'gpu' }" @click="mode = 'gpu'">GPU 指标</button>
      <button role="tab" :aria-selected="mode === 'process'" :class="{ active: mode === 'process' }" @click="mode = 'process'">进程指标</button>
    </div>

    <div class="trend-layout">
      <aside class="metric-nav" aria-label="选择指标">
        <p class="eyebrow">METRICS</p>
        <div v-for="group in [...new Set(metrics.map(item => item.group))]" :key="group" class="metric-group">
          <h2>{{ group }}</h2>
          <button v-for="metric in metrics.filter(item => item.group === group)" :key="metric.key"
            :class="{ active: activeMetric?.key === metric.key }" @click="chooseMetric(metric.key)">
            {{ metric.label }} <span aria-hidden="true">↗</span>
          </button>
        </div>
      </aside>

      <section class="trend-main" aria-label="时间走势">
        <div class="trend-topline">
          <div>
            <p class="eyebrow">LIVE TREND / {{ activeMetric?.unit }}</p>
            <h2>{{ activeMetric?.label }}</h2>
          </div>
          <div class="target-control">
            <label v-if="mode === 'process'" class="compare-switch"><input v-model="compareTop" type="checkbox"><span>对比排名靠前的进程</span></label>
            <div v-if="mode === 'process' && compareTop" class="top-process-controls">
              <label for="top-count">前</label>
              <select id="top-count" v-model.number="topCount">
                <option :value="3">3 个</option><option :value="5">5 个</option><option :value="8">8 个</option>
              </select>
              <label for="top-gpu">设备</label>
              <select id="top-gpu" v-model="selectedGpu">
                <option value="all">全部 GPU</option>
                <option v-for="gpu in gpus" :key="gpu.id" :value="String(gpu.id)">GPU {{ gpu.id }}</option>
              </select>
            </div>
            <label v-if="mode === 'process' && compareTop && activeMetric?.group === '内存'" class="compare-switch"><input v-model="logMemoryScale" type="checkbox"><span>对数纵轴 · 保留真实读数</span></label>
            <label v-if="mode === 'gpu'" for="gpu-select">观察对象</label>
            <select v-if="mode === 'gpu'" id="gpu-select" v-model="selectedGpu">
              <option value="all">对比全部 GPU</option>
              <option v-for="gpu in gpus" :key="gpu.id" :value="String(gpu.id)">GPU {{ gpu.id }} · {{ gpu.name }}</option>
            </select>
            <ProcessPicker v-else-if="!compareTop" :processes="processOptions" :selected-key="selectedProcess"
              @select="chooseProcess" />
          </div>
        </div>

        <div v-if="!targets.length" class="empty-state trend-empty">
          {{ mode === 'gpu' ? '没有可观察的 NVIDIA GPU' : '当前没有可观察的 GPU 进程' }}
        </div>
        <template v-else>
          <div class="chart-frame"><Line :data="chartData" :options="chartOptions" /></div>
          <div v-if="mode === 'process' && compareTop" class="trend-legend" aria-label="进程曲线图例">
            <div v-for="reading in readings" :key="reading.id">
              <i :style="{ background: reading.color }"></i>
              <span>{{ reading.name }}</span>
              <strong><RollingValue :value="display(reading.current)" :amount="reading.current" /></strong>
            </div>
          </div>
        </template>
        <p class="data-note">曲线来自服务端最近 120 帧历史。{{ mode === 'process' && compareTop ? '排名按当前选中指标的最新有效值确定；缺失值不参与排名，进程退出后的历史保留为空白。' : '进程退出后的采样保留为空白。' }}{{ logarithmic ? '对数纵轴便于同时看大、小进程；真实值见图例，0 无法绘在对数轴上。' : '' }}{{ mode === 'process' ? 'WDDM 分类约每两秒刷新；本机 CUDA 训练可能计入名为 3D 的队列，Compute 队列缺失不代表 CUDA 空闲。队列读数不能推算 RT 或 Tensor Core 占用。进程本地用量可包含共享资源；专用计数和提交量均不等于独占 VRAM。' : '' }}</p>
      </section>

      <aside class="trend-side" aria-label="当前读数">
        <section v-if="targets.length" class="reading-section">
          <div class="section-heading"><div><p class="eyebrow">LIVE / SELECTED METRIC</p><h2>当前读数</h2></div></div>
          <div class="reading-grid">
            <div v-for="reading in readings" :key="reading.id" class="reading" :style="{ '--line-color': reading.color }">
              <span class="reading-name"><RollingValue :value="reading.name" /></span>
              <strong><RollingValue :value="display(reading.current)" :amount="reading.current" /></strong>
              <span class="reading-range">区间 <RollingValue :value="display(reading.min)" :amount="reading.min" /> – <RollingValue :value="display(reading.max)" :amount="reading.max" /></span>
            </div>
          </div>
        </section>

        <section v-if="mode === 'gpu' && gpus.length" class="detail-summary" aria-label="当前 GPU 数据">
          <div class="section-heading"><div><p class="eyebrow">CURRENT / SNAPSHOT</p><h2>设备状态</h2></div></div>
          <div class="snapshot-grid">
            <article v-for="gpu in gpus" :key="gpu.id">
              <span>GPU {{ gpu.id }} · {{ gpu.name }}</span>
              <div class="snapshot-secondary">
                <div><span>GPU 负载</span><b><RollingValue :value="percent(gpu.utilization?.gpu)" :amount="gpu.utilization?.gpu" /></b></div>
                <div><span>温度</span><b><RollingValue :value="number(gpu.temperature, 0, '°C')" :amount="gpu.temperature" /></b></div>
                <div><span>功耗</span><b><RollingValue :value="number(gpu.power?.usage, 0, ' W')" :amount="gpu.power?.usage" /></b></div>
              </div>
            </article>
          </div>
        </section>

        <section v-if="mode === 'process' && !compareTop && selectedProcessInfo" class="detail-summary process-memory-summary" aria-label="进程显存口径">
          <div class="section-heading"><div><p class="eyebrow">MEMORY / ACCOUNTING</p><h2>显存口径</h2></div></div>
          <div class="memory-check-grid">
            <div><span>{{ selectedProcessInfo.memory_source === 'Windows GPU counters' ? '进程本地用量 · 含共享' : '进程用量 · NVML' }}</span><strong><RollingValue :value="bytes(selectedProcessInfo.used_memory)" :amount="selectedProcessInfo.used_memory" /></strong></div>
            <div><span>进程专用计数 · Windows</span><strong><RollingValue :value="bytes(selectedProcessInfo.claimed_memory)" :amount="selectedProcessInfo.claimed_memory" /></strong></div>
            <div><span>进程提交量 · Windows</span><strong><RollingValue :value="bytes(selectedProcessInfo.committed_memory)" :amount="selectedProcessInfo.committed_memory" /></strong></div>
            <div><span>GPU <RollingValue :value="selectedProcessInfo.gpu_id" /> 设备已用 · NVML</span><strong><RollingValue :value="bytes(selectedProcessGpu?.memory?.used)" :amount="selectedProcessGpu?.memory?.used" /></strong></div>
          </div>
          <p v-if="counterExceedsCapacity" class="memory-counter-alert">专用计数已超过这张 GPU 的物理显存容量，不能解释为该进程实际占用。</p>
          <p class="memory-check-note">进程计数含共享资源，不能相加，也无法仅凭这些读数拆出 DWM 独占量。提交量不是当前驻留量。</p>
        </section>
      </aside>
    </div>

  </div>
</template>
