<script setup>
import { computed, ref, watch } from 'vue'
import { Line } from 'vue-chartjs'
import {
  Chart as ChartJS, CategoryScale, LinearScale, PointElement,
  LineElement, Tooltip, Legend,
} from 'chart.js'
import { useMonitorData, procKey } from '../composables/useMonitorData'
import { bytes, number, percent } from '../format'

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Tooltip, Legend)

const { snapshot, history } = useMonitorData()
const mode = ref('gpu')
const selectedGpu = ref('all')
const selectedProcess = ref('')
const processSearch = ref('')
const gpuMetric = ref('vram')
const processMetric = ref('used_memory')

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
  { key: 'used_memory', label: '专用分配', group: '内存', unit: 'MB', read: (p) => p?.used_memory == null ? null : p.used_memory / 1048576 },
  { key: 'sm_util', label: 'SM 负载', group: '负载', unit: '%', read: (_, u) => u?.sm_util },
  { key: 'mem_util', label: '显存控制器', group: '负载', unit: '%', read: (_, u) => u?.mem_util },
  { key: 'enc_util', label: '编码器', group: '引擎', unit: '%', read: (_, u) => u?.enc_util },
  { key: 'dec_util', label: '解码器', group: '引擎', unit: '%', read: (_, u) => u?.dec_util },
]
const metrics = computed(() => mode.value === 'gpu' ? gpuMetrics : processMetrics)
const activeMetric = computed(() => metrics.value.find(m => m.key === (mode.value === 'gpu' ? gpuMetric.value : processMetric.value)))
const gpus = computed(() => snapshot.value?.gpus ?? [])
const processOptions = computed(() => {
  const options = new Map()
  for (const p of snapshot.value?.processes ?? []) {
    options.set(procKey(p.pid, p.gpu_id), p)
  }
  for (const p of snapshot.value?.process_utilization ?? []) {
    const key = procKey(p.pid, p.gpu_id)
    if (!options.has(key)) options.set(key, p)
  }
  return [...options.entries()].map(([key, p]) => ({ key, ...p }))
})
const filteredProcessOptions = computed(() => {
  const query = processSearch.value.trim().toLowerCase()
  return query
    ? processOptions.value.filter(p => p.name.toLowerCase().includes(query) || String(p.pid).includes(query))
    : processOptions.value
})
watch(processSearch, () => { selectedProcess.value = filteredProcessOptions.value[0]?.key || '' })
const processKey = computed(() => selectedProcess.value || filteredProcessOptions.value[0]?.key || '')
const selectedProcessInfo = computed(() => processOptions.value.find(p => p.key === processKey.value))
const palette = ['#79ded0', '#8dbbf5', '#efbd81', '#c9a6e5', '#ed958f']

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
    return processKey.value ? [{ id: processKey.value, name: selectedProcessInfo.value?.name || processKey.value }] : []
  }
  return gpus.value
    .filter(g => selectedGpu.value === 'all' || g.id === Number(selectedGpu.value))
    .map(g => ({ id: g.id, name: `GPU ${g.id}` }))
})
const chartData = computed(() => ({
  labels: history.value.map(frame => new Date(frame.timestamp * 1000).toLocaleTimeString('zh-CN', { hour12: false })),
  datasets: targets.value.map((target, i) => ({
    label: target.name,
    data: history.value.map(frame => valueAt(frame, target.id)),
    borderColor: palette[i % palette.length],
    backgroundColor: palette[i % palette.length],
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
      ticks: { maxTicksLimit: 8, maxRotation: 0, color: '#a0b8bf' },
      border: { color: '#38515c' },
    },
    y: {
      beginAtZero: activeMetric.value?.unit === '%',
      suggestedMax: activeMetric.value?.unit === '%' ? 100 : undefined,
      grid: { color: '#2a414b' },
      ticks: { color: '#a0b8bf', callback: value => `${value} ${activeMetric.value?.unit ?? ''}` },
      border: { display: false },
    },
  },
  plugins: {
    legend: { display: targets.value.length > 1, position: 'bottom', labels: { color: '#a0b8bf', usePointStyle: true, boxWidth: 8 } },
    tooltip: { backgroundColor: '#e9f2f1', titleColor: '#0a1720', bodyColor: '#0a1720', displayColors: targets.value.length > 1 },
  },
}))
const readings = computed(() => targets.value.map((target, i) => {
  const data = chartData.value.datasets[i]?.data ?? []
  const values = data.filter(v => v != null && Number.isFinite(v))
  return {
    ...target,
    current: data.at(-1) ?? null,
    min: values.length ? Math.min(...values) : null,
    max: values.length ? Math.max(...values) : null,
    color: palette[i % palette.length],
  }
}))
function display(value) {
  return number(value, activeMetric.value?.unit === 'GB' ? 2 : 0, ` ${activeMetric.value?.unit || ''}`)
}
function chooseMetric(key) {
  if (mode.value === 'gpu') gpuMetric.value = key
  else processMetric.value = key
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
      <div class="intro-count"><strong>{{ history.length }}</strong><span>采样点</span></div>
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
            <label :for="mode === 'gpu' ? 'gpu-select' : 'process-select'">观察对象</label>
            <select v-if="mode === 'gpu'" id="gpu-select" v-model="selectedGpu">
              <option value="all">对比全部 GPU</option>
              <option v-for="gpu in gpus" :key="gpu.id" :value="String(gpu.id)">GPU {{ gpu.id }} · {{ gpu.name }}</option>
            </select>
            <template v-else>
              <input v-model="processSearch" type="search" placeholder="搜索进程名或 PID" aria-label="搜索观察进程" />
              <select id="process-select" v-model="selectedProcess">
                <option v-for="proc in filteredProcessOptions" :key="proc.key" :value="proc.key">{{ proc.name }} · {{ proc.pid }} / GPU {{ proc.gpu_id }}</option>
              </select>
            </template>
          </div>
        </div>

        <div v-if="!targets.length" class="empty-state trend-empty">
          {{ mode === 'gpu' ? '没有可观察的 NVIDIA GPU' : '当前没有可观察的 GPU 进程' }}
        </div>
        <template v-else>
          <div class="chart-frame"><Line :data="chartData" :options="chartOptions" /></div>
          <div class="reading-grid">
            <div v-for="reading in readings" :key="reading.id" class="reading" :style="{ '--line-color': reading.color }">
              <span class="reading-name">{{ reading.name }}</span>
              <strong>{{ display(reading.current) }}</strong>
              <span class="reading-range">区间 {{ display(reading.min) }} – {{ display(reading.max) }}</span>
            </div>
          </div>
        </template>
        <p class="data-note">曲线来自服务端最近 120 帧历史。指标单位单独显示，进程退出后的采样不会伪装为 0。</p>
      </section>
    </div>

    <section v-if="mode === 'gpu' && gpus.length" class="detail-summary" aria-label="当前 GPU 数据">
      <div class="section-heading"><div><p class="eyebrow">CURRENT / SNAPSHOT</p><h2>此刻</h2></div></div>
      <div class="snapshot-grid">
        <article v-for="gpu in gpus" :key="gpu.id">
          <span>GPU {{ gpu.id }} · {{ gpu.name }}</span>
          <strong>{{ bytes(gpu.memory?.used) }}</strong>
          <small>{{ percent(gpu.utilization?.gpu) }} 负载 <span> / </span> {{ number(gpu.temperature, 0, '°C') }}</small>
        </article>
      </div>
    </section>
  </div>
</template>
