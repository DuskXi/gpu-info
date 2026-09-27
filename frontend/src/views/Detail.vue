<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { useMonitorData, procKey } from '../composables/useMonitorData'
import { Line } from 'vue-chartjs'
import {
  Chart as ChartJS, CategoryScale, LinearScale, PointElement,
  LineElement, Title, Tooltip, Legend, Filler
} from 'chart.js'

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Title, Tooltip, Legend, Filler)

const { system, gpus, processes, processUtilization, isConnected, isStale, lastUpdate } = useMonitorData()

// ===== Tabs =====
const activeTab = ref('process') // 'process' | 'gpu'

// ===== Available metrics =====
const PROC_METRICS = [
  { key: 'sm_util', label: 'SM Core Util (%)', color: '#58a6ff', source: 'util' },
  { key: 'mem_util', label: 'Mem Ctrl Util (%)', color: '#a371f7', source: 'util' },
  { key: 'enc_util', label: 'Encoder Util (%)', color: '#3fb950', source: 'util' },
  { key: 'dec_util', label: 'Decoder Util (%)', color: '#d29922', source: 'util' },
  { key: 'used_memory', label: 'Dedicated VRAM (MB)', color: '#f85149', source: 'mem' },
  { key: 'shared_memory', label: 'Shared VRAM (MB)', color: '#f0883e', source: 'mem' },
]

const GPU_METRICS = [
  { key: 'gpu_util', label: 'GPU Core Util (%)' },
  { key: 'mem_ctrl_util', label: 'Mem Controller Util (%)' },
  { key: 'temperature', label: 'Temperature (°C)' },
  { key: 'fan_speed', label: 'Fan Speed (%)' },
  { key: 'fan_rpm', label: 'Fan RPM' },
  { key: 'power_usage', label: 'Power Draw (W)' },
  { key: 'clock_graphics', label: 'Core Clock (MHz)' },
  { key: 'clock_memory', label: 'Memory Clock (MHz)' },
  { key: 'enc_util', label: 'Encoder Util (%)' },
  { key: 'dec_util', label: 'Decoder Util (%)' },
  { key: 'jpeg_util', label: 'JPEG Engine (%)' },
  { key: 'ofa_util', label: 'OFA Engine (%)' },
  { key: 'pcie_tx', label: 'PCIe Tx (MB/s)' },
  { key: 'pcie_rx', label: 'PCIe Rx (MB/s)' },
  { key: 'mem_used', label: 'VRAM Used (MB)' },
  { key: 'mem_percent', label: 'VRAM Usage (%)' },
  { key: 'bar1_used', label: 'BAR1 Used (MB)' },
]

// ===== Time-series =====
const MAX_HISTORY = 120
const procHistory = ref({})
const gpuHistory = ref({})
const timeLabels = ref([])

// ===== Process Panel System =====
const PROC_STORAGE = 'gpu-monitor-proc-panels'
const GPU_STORAGE = 'gpu-monitor-gpu-panels'

function createProcPanel() {
  return { id: Date.now() + Math.random(), title: 'Process Panel', pinnedProcs: [], selectedMetrics: ['sm_util'], editing: true }
}
function createGpuPanel() {
  return { id: Date.now() + Math.random(), title: 'GPU Metrics', selectedMetrics: ['gpu_util', 'temperature'], editing: true }
}

const procPanels = ref([])
const gpuPanels = ref([])

function loadPanels(key, arr, factory) {
  try {
    const saved = localStorage.getItem(key)
    if (saved) {
      const p = JSON.parse(saved)
      if (Array.isArray(p) && p.length) {
        arr.value = p.map(x => {
          const panel = { ...x, editing: false }
          if (key === PROC_STORAGE) {
            if (!panel.pinnedProcs && panel.pinnedPids) {
              panel.pinnedProcs = panel.pinnedPids.map(pid => procKey(pid, 0))
            }
            panel.pinnedProcs = panel.pinnedProcs || []
            delete panel.pinnedPids
          }
          return panel
        })
        return
      }
    }
  } catch { /* ignore */ }
  arr.value = [factory()]
}
function savePanels(key, arr) {
  const s = arr.value.map(({ id, title, pinnedProcs, selectedMetrics }) => ({
    id, title, pinnedProcs, selectedMetrics,
  }))
  localStorage.setItem(key, JSON.stringify(s))
}

onMounted(async () => { 
  loadPanels(PROC_STORAGE, procPanels, createProcPanel); 
  loadPanels(GPU_STORAGE, gpuPanels, createGpuPanel);
  
  try {
    const res = await fetch('/api/history')
    const hist = await res.json()
    if (Array.isArray(hist) && hist.length > 0) {
      timeLabels.value = []
      procHistory.value = {}
      gpuHistory.value = {}
      hist.forEach(tick => processTick(tick))
    }
  } catch (e) {
    console.error("Failed to load history:", e)
  }
})

watch(procPanels, () => savePanels(PROC_STORAGE, procPanels), { deep: true })
watch(gpuPanels, () => savePanels(GPU_STORAGE, gpuPanels), { deep: true })

function addProcPanel() { procPanels.value.push(createProcPanel()) }
function addGpuPanel() { gpuPanels.value.push(createGpuPanel()) }
function removeProcPanel(i) { delete procHistory.value[procPanels.value[i].id]; procPanels.value.splice(i, 1); if (!procPanels.value.length) procPanels.value.push(createProcPanel()) }
function removeGpuPanel(i) { delete gpuHistory.value[gpuPanels.value[i].id]; gpuPanels.value.splice(i, 1); if (!gpuPanels.value.length) gpuPanels.value.push(createGpuPanel()) }

// ===== Drag =====
const dragIdx = ref(null)
const dragType = ref(null)
function onDragStart(idx, type) { dragIdx.value = idx; dragType.value = type }
function onDragOver(e) { e.preventDefault() }
function onDrop(idx, type) {
  if (dragIdx.value === null || dragType.value !== type || dragIdx.value === idx) return
  const arr = type === 'proc' ? procPanels.value : gpuPanels.value
  const item = arr.splice(dragIdx.value, 1)[0]
  arr.splice(idx, 0, item)
  dragIdx.value = null
}

// ===== Process selector sorting =====
const procSortBy = ref('name') // 'name' | 'vram' | 'sm'
const procFilter = ref('')

const allProcs = computed(() => {
  const map = {}
  for (const p of processes.value) {
    const key = procKey(p.pid, p.gpu_id)
    map[key] = {
      key,
      pid: p.pid,
      gpu_id: p.gpu_id,
      name: p.name,
      label: `${p.name} (GPU ${p.gpu_id})`,
      vram: p.used_memory || 0,
      sm: 0,
    }
  }
  for (const p of processUtilization.value) {
    const key = procKey(p.pid, p.gpu_id)
    if (map[key]) {
      map[key].sm = p.sm_util || 0
    } else {
      map[key] = {
        key,
        pid: p.pid,
        gpu_id: p.gpu_id,
        name: p.name,
        label: `${p.name} (GPU ${p.gpu_id})`,
        vram: 0,
        sm: p.sm_util || 0,
      }
    }
  }
  let arr = Object.values(map)
  if (procSortBy.value === 'vram') arr.sort((a, b) => b.vram - a.vram)
  else if (procSortBy.value === 'sm') arr.sort((a, b) => b.sm - a.sm)
  else arr.sort((a, b) => a.label.localeCompare(b.label))
  return arr
})

function filteredProcs(panel) {
  const q = procFilter.value.toLowerCase()
  let arr = q
    ? allProcs.value.filter(p =>
        p.label.toLowerCase().includes(q) ||
        String(p.pid).includes(q) ||
        String(p.gpu_id).includes(q)
      )
    : [...allProcs.value]

  arr.sort((a, b) => {
    const aPinned = panel.pinnedProcs.includes(a.key)
    const bPinned = panel.pinnedProcs.includes(b.key)
    if (aPinned && !bPinned) return -1
    if (!aPinned && bPinned) return 1

    if (procSortBy.value === 'name') return a.label.localeCompare(b.label)
    const va = a[procSortBy.value] ?? 0
    const vb = b[procSortBy.value] ?? 0
    return vb - va
  })
  return arr
}

function toggleProc(panel, key) {
  const i = panel.pinnedProcs.indexOf(key)
  if (i >= 0) panel.pinnedProcs.splice(i, 1)
  else panel.pinnedProcs.push(key)
}
function toggleMetric(panel, key) { const i = panel.selectedMetrics.indexOf(key); if (i >= 0 && panel.selectedMetrics.length > 1) panel.selectedMetrics.splice(i, 1); else if (i < 0) panel.selectedMetrics.push(key) }

// ===== GPU metric extractor =====
function extractGpuMetric(gpu, key) {
  const m = {
    gpu_util: gpu.utilization?.gpu, mem_ctrl_util: gpu.utilization?.memory,
    temperature: gpu.temperature, fan_speed: gpu.fan_speed, fan_rpm: gpu.fan_rpm,
    power_usage: gpu.power?.usage, clock_graphics: gpu.clocks?.graphics, clock_memory: gpu.clocks?.memory,
    enc_util: gpu.engines?.encoder, dec_util: gpu.engines?.decoder, jpeg_util: gpu.engines?.jpeg, ofa_util: gpu.engines?.ofa,
    pcie_tx: (gpu.pcie?.tx || 0) / 1024, pcie_rx: (gpu.pcie?.rx || 0) / 1024,
    mem_used: (gpu.memory?.used || 0) / (1024*1024), mem_percent: gpu.memory?.percent, bar1_used: (gpu.bar1?.used || 0) / (1024*1024),
  }
  return m[key] ?? 0
}

// ===== Update history every tick =====
function processTick(data) {
  if (!data) return
  const now = new Date(data.timestamp ? data.timestamp * 1000 : Date.now())
  const label = now.toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' })
  timeLabels.value.push(label)
  if (timeLabels.value.length > MAX_HISTORY) timeLabels.value.shift()

  const procs = data.processes || []
  const procUtil = data.process_utilization || []
  const gs = data.gpus || []

  // Process history
  const memMap = {}
  for (const p of procs) memMap[procKey(p.pid, p.gpu_id)] = p
  const utilMap = {}
  for (const p of procUtil) utilMap[procKey(p.pid, p.gpu_id)] = p

  for (const panel of procPanels.value) {
    if (!procHistory.value[panel.id]) procHistory.value[panel.id] = {}
    const ph = procHistory.value[panel.id]
    for (const key of panel.pinnedProcs) {
      if (!ph[key]) ph[key] = {}
      for (const mk of panel.selectedMetrics) {
        if (!ph[key][mk]) ph[key][mk] = []
        const metric = PROC_METRICS.find(x => x.key === mk)
        let val = 0
        if (metric?.source === 'util' && utilMap[key]) val = utilMap[key][mk] ?? 0
        else if (metric?.source === 'mem' && memMap[key]) val = (memMap[key][mk] ?? 0) / (1024 * 1024)
        ph[key][mk].push(val)
        if (ph[key][mk].length > MAX_HISTORY) ph[key][mk].shift()
      }
    }
  }

  // GPU history
  for (const panel of gpuPanels.value) {
    if (!gpuHistory.value[panel.id]) gpuHistory.value[panel.id] = {}
    const gh = gpuHistory.value[panel.id]
    for (const gpu of gs) {
      const gid = `gpu${gpu.id}`
      if (!gh[gid]) gh[gid] = {}
      for (const mk of panel.selectedMetrics) {
        if (!gh[gid][mk]) gh[gid][mk] = []
        gh[gid][mk].push(extractGpuMetric(gpu, mk))
        if (gh[gid][mk].length > MAX_HISTORY) gh[gid][mk].shift()
      }
    }
  }
}

watch(lastUpdate, (val) => {
  if (val) processTick(val)
}, { deep: true })

// ===== Chart builders =====
const COLORS = ['#58a6ff','#a371f7','#3fb950','#d29922','#f85149','#f0883e','#79c0ff','#d2a8ff','#7ee787','#e3b341','#ff7b72','#ffa657']

function getProcChartData(panel) {
  const ph = procHistory.value[panel.id]; if (!ph) return { labels: [], datasets: [] }
  const datasets = []; let ci = 0
  for (const key of panel.pinnedProcs) {
    if (!ph[key]) continue
    const pn = allProcs.value.find(p => p.key === key)?.label || key
    for (const mk of panel.selectedMetrics) {
      if (!ph[key][mk]) continue
      const ml = PROC_METRICS.find(x => x.key === mk)?.label || mk
      const c = COLORS[ci++ % COLORS.length]
      datasets.push({ label: `${pn} ${ml}`, data: [...ph[key][mk]], borderColor: c, backgroundColor: c + '15', fill: true, tension: 0.3, pointRadius: 0, borderWidth: 1.5 })
    }
  }
  return { labels: [...timeLabels.value], datasets }
}

function getGpuChartData(panel) {
  const gh = gpuHistory.value[panel.id]; if (!gh) return { labels: [], datasets: [] }
  const datasets = []; let ci = 0
  for (const gpu of gpus.value) {
    const gid = `gpu${gpu.id}`; if (!gh[gid]) continue
    for (const mk of panel.selectedMetrics) {
      if (!gh[gid][mk]) continue
      const ml = GPU_METRICS.find(x => x.key === mk)?.label || mk
      const c = COLORS[ci++ % COLORS.length]
      datasets.push({ label: `GPU${gpu.id} ${ml}`, data: [...gh[gid][mk]], borderColor: c, backgroundColor: c + '15', fill: true, tension: 0.3, pointRadius: 0, borderWidth: 1.5 })
    }
  }
  return { labels: [...timeLabels.value], datasets }
}

const chartOptions = {
  responsive: true, maintainAspectRatio: false, animation: { duration: 200 },
  interaction: { intersect: false, mode: 'index' },
  scales: {
    x: { ticks: { color: '#8b949e', maxTicksLimit: 10, font: { size: 10 } }, grid: { color: 'rgba(255,255,255,0.04)' } },
    y: { beginAtZero: true, ticks: { color: '#8b949e', font: { size: 10 } }, grid: { color: 'rgba(255,255,255,0.04)' } },
  },
  plugins: {
    legend: { labels: { color: '#c9d1d9', boxWidth: 10, font: { size: 10 } }, position: 'bottom' },
    tooltip: { backgroundColor: 'rgba(13,17,23,0.92)', borderColor: 'rgba(255,255,255,0.1)', borderWidth: 1 },
  },
}

const detailStatusText = computed(() => {
  if (!isConnected.value) return 'Reconnecting...'
  if (isStale.value) return 'Data stale'
  return 'Live'
})

const detailStatusColor = computed(() => {
  if (!isConnected.value) return 'var(--danger-color)'
  if (isStale.value) return 'var(--warn-color, #d29922)'
  return 'var(--success-color)'
})

const formatMB = (b) => {
  const mb = b / (1024 * 1024)
  return mb < 1024 ? mb.toFixed(0) + ' MB' : (mb / 1024).toFixed(2) + ' GB'
}
</script>

<template>
  <div class="detail-page">
    <!-- Tab bar -->
    <div class="detail-header">
      <div class="tab-bar">
        <button :class="['tab-btn', { active: activeTab === 'process' }]" @click="activeTab = 'process'">Process Metrics</button>
        <button :class="['tab-btn', { active: activeTab === 'gpu' }]" @click="activeTab = 'gpu'">GPU Metrics</button>
      </div>
      <div class="status-header" style="margin:0">
        <span class="live-indicator" :style="{ backgroundColor: detailStatusColor }"></span>
        {{ detailStatusText }}
      </div>
    </div>

    <!-- ===== Process Metrics Tab ===== -->
    <div v-show="activeTab === 'process'">
      <div style="display:flex; justify-content:flex-end; margin-bottom:1rem;">
        <button class="btn-add" @click="addProcPanel">+ Add Process Panel</button>
      </div>

      <div class="panels-container">
        <div v-for="(panel, idx) in procPanels" :key="panel.id" class="glass-card panel-card"
             draggable="true" @dragstart="onDragStart(idx, 'proc')" @dragover="onDragOver" @drop="onDrop(idx, 'proc')">
          <div class="panel-title-bar">
            <div class="panel-drag-handle">⠿</div>
            <input v-if="panel.editing" v-model="panel.title" class="panel-title-input" @keyup.enter="panel.editing = false" @blur="panel.editing = false" />
            <span v-else class="panel-title" @dblclick="panel.editing = true">{{ panel.title }}</span>
            <div class="panel-actions">
              <button class="panel-btn" @click="panel.editing = !panel.editing">✎</button>
              <button class="panel-btn panel-btn-danger" @click="removeProcPanel(idx)">✕</button>
            </div>
          </div>

          <div v-if="panel.editing" class="panel-config">
            <div class="config-columns">
              <div class="config-col-left">
                <h4>Select Metrics</h4>
                <div class="chip-list">
                  <button v-for="m in PROC_METRICS" :key="m.key"
                    :class="['chip', { 'chip-active': panel.selectedMetrics.includes(m.key) }]"
                    :style="panel.selectedMetrics.includes(m.key) ? { borderColor: m.color, color: m.color } : {}"
                    @click="toggleMetric(panel, m.key)">{{ m.label }}</button>
                </div>
              </div>
              
              <div class="config-col-right">
                <div style="display:flex; justify-content:space-between; align-items:flex-end; margin-bottom: 0.5rem;">
                  <h4>Target Processes</h4>
                  <div class="proc-controls">
                    <input type="text" v-model="procFilter" placeholder="Filter processes..." class="search-box" style="flex:1" />
                    <select v-model="procSortBy" class="sort-select">
                      <option value="name">Sort: Name</option>
                      <option value="vram">Sort: VRAM ↓</option>
                      <option value="sm">Sort: SM Util ↓</option>
                    </select>
                  </div>
                </div>
                <div class="proc-list">
                  <label v-for="p in filteredProcs(panel)" :key="p.key" :class="['proc-item', { 'proc-pinned': panel.pinnedProcs.includes(p.key) }]">
                    <input type="checkbox" :checked="panel.pinnedProcs.includes(p.key)" @change="toggleProc(panel, p.key)" />
                    <span class="proc-name">{{ p.label }} <span v-if="panel.pinnedProcs.includes(p.key)" class="pinned-badge">Pinned</span></span>
                    <span class="proc-detail">{{ formatMB(p.vram) }} | SM {{ p.sm }}%</span>
                    <span class="proc-pid">{{ p.pid }}</span>
                    <button v-if="panel.pinnedProcs.includes(p.key)" class="btn-remove-proc" @click.stop.prevent="toggleProc(panel, p.key)">✕</button>
                  </label>
                </div>
              </div>
            </div>
            <div style="text-align:right; margin-top:1rem;">
              <button class="btn-done" @click="panel.editing = false">Save & Close</button>
            </div>
          </div>

          <div v-if="panel.pinnedProcs.length === 0 && !panel.editing" class="panel-empty">
            <p>No processes pinned. Click ✎ to configure.</p>
          </div>
          <div v-else-if="!panel.editing" class="chart-container">
            <Line :data="getProcChartData(panel)" :options="chartOptions" />
          </div>
        </div>
      </div>
    </div>

    <!-- ===== GPU Metrics Tab ===== -->
    <div v-show="activeTab === 'gpu'">
      <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:1rem;">
        <p style="color:var(--text-secondary); margin:0; font-size:0.9rem;">
          Track global GPU-level metrics over time. Charts show data for all GPUs.
        </p>
        <button class="btn-add" @click="addGpuPanel">+ Add GPU Panel</button>
      </div>

      <div class="panels-container">
        <div v-for="(panel, idx) in gpuPanels" :key="panel.id" class="glass-card panel-card"
             draggable="true" @dragstart="onDragStart(idx, 'gpu')" @dragover="onDragOver" @drop="onDrop(idx, 'gpu')">
          <div class="panel-title-bar">
            <div class="panel-drag-handle">⠿</div>
            <input v-if="panel.editing" v-model="panel.title" class="panel-title-input" @keyup.enter="panel.editing = false" @blur="panel.editing = false" />
            <span v-else class="panel-title" @dblclick="panel.editing = true">{{ panel.title }}</span>
            <div class="panel-actions">
              <button class="panel-btn" @click="panel.editing = !panel.editing">✎</button>
              <button class="panel-btn panel-btn-danger" @click="removeGpuPanel(idx)">✕</button>
            </div>
          </div>

          <div v-if="panel.editing" class="panel-config">
            <div class="config-columns" style="align-items:flex-start">
              <div class="config-col-left" style="flex:1">
                <h4>Select Metrics ({{ panel.selectedMetrics.length }} selected)</h4>
                <div class="chip-list">
                  <button v-for="m in GPU_METRICS" :key="m.key"
                    :class="['chip', { 'chip-active': panel.selectedMetrics.includes(m.key) }]"
                    @click="toggleMetric(panel, m.key)">{{ m.label }}</button>
                </div>
              </div>
            </div>
            <div style="text-align:right; margin-top:1rem;">
              <button class="btn-done" @click="panel.editing = false">Save & Close</button>
            </div>
          </div>

          <div v-if="panel.selectedMetrics.length === 0 && !panel.editing" class="panel-empty">
            <p>No metrics selected. Click ✎ to configure.</p>
          </div>
          <div v-else-if="!panel.editing" class="chart-container">
            <Line :data="getGpuChartData(panel)" :options="chartOptions" />
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
