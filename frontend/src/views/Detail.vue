<script setup>
import { computed, ref, watch } from 'vue'
import { Line } from 'vue-chartjs'
import {
  Chart as ChartJS, CategoryScale, LinearScale, LogarithmicScale, PointElement,
  LineElement, Tooltip, Legend,
} from 'chart.js'
import { useMonitorData, procKey } from '../composables/useMonitorData'
import HardwarePanel from '../components/HardwarePanel.vue'
import LegacyActivity from '../components/LegacyActivity.vue'
import { bytes, number, percent } from '../format'
import { topProcessTargets } from '../processComparison'
import ProcessPicker from '../components/ProcessPicker.vue'

ChartJS.register(CategoryScale, LinearScale, LogarithmicScale, PointElement, LineElement, Tooltip, Legend)

const { snapshot, history, now, hardwareSampling, hardwareHistory } = useMonitorData()
const mode = ref('gpu')
const selectedGpu = ref('all')
const selectedProcess = ref('')
const processManuallySelected = ref(false)
const gpuMetric = ref('vram')
const processMetric = ref('used_memory')
const compareTop = ref(false)
const topCount = ref(5)
const logMemoryScale = ref(true)
const gpuMetrics = [
  { key: 'vram', label: '专用显存', group: '内存', unit: 'GB', read: g => g.memory?.used == null ? null : g.memory.used / 1073741824 },
  { key: 'vram_percent', label: '显存占用率', group: '内存', unit: '%', read: g => g.memory?.percent },
  { key: 'gpu_load', label: 'GPU 负载', group: '负载', unit: '%', read: g => g.utilization?.gpu },
  { key: 'memory_load', label: '显存控制器', group: '负载', unit: '%', read: g => g.utilization?.memory },
  { key: 'temperature', label: '温度', group: '状态', unit: '°C', read: g => g.temperature },
  { key: 'power', label: '功耗', group: '状态', unit: 'W', read: g => g.power?.usage },
  { key: 'instant_power', label: '瞬时功率', group: '状态', unit: 'W', read: g => g.training_health?.instant_power_w },
  { key: 'energy_power', label: '能量差分功率', group: '状态', unit: 'W', read: g => g.training_health?.energy_window_w },
  { key: 'thermal_margin', label: '温度裕量', group: '状态', unit: '°C', read: g => g.training_health?.temperature_margin_c },
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
          return `${Number(Number(value).toFixed(3))} ${activeMetric.value?.unit ?? ''}`
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

    <section class="section-block">
      <div class="section-heading"><div><p class="eyebrow">TRAINING / HARDWARE COUNTERS</p><h2>计算单元与训练效率</h2></div></div>
      <HardwarePanel v-for="gpu in gpus" :key="gpu.id" :gpu="gpu" :state="hardwareSampling" :history="hardwareHistory" :now="now / 1000" detailed />
      <LegacyActivity :gpus="gpus" :sampling="hardwareSampling" />
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
