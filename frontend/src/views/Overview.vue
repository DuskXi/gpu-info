<script setup>
import { computed, ref, watch } from 'vue'
import { useMonitorData } from '../composables/useMonitorData'

const { system, gpus, processes, cudaInfo, isConnected, isStale } = useMonitorData()

const formatBytesAuto = (bytes) => {
  if (!bytes && bytes !== 0) return '-'
  const mb = bytes / (1024 * 1024)
  if (mb < 1024) return mb.toFixed(0) + ' MB'
  return (mb / 1024).toFixed(2) + ' GB'
}
const formatGB = (bytes) => (bytes / (1024 * 1024 * 1024)).toFixed(2) + ' GB'
const formatGpuLabel = (proc) => {
  if (proc.gpu_name) return `GPU ${proc.gpu_id}`
  return `GPU ${proc.gpu_id}`
}

const statusText = computed(() => {
  if (!isConnected.value) return 'Reconnecting...'
  if (isStale.value) return 'Data stale'
  return 'Live Connection'
})

const statusColor = computed(() => {
  if (!isConnected.value) return 'var(--danger-color)'
  if (isStale.value) return 'var(--warn-color, #d29922)'
  return 'var(--success-color)'
})
const getColorClass = (p) => p < 60 ? 'color-safe' : p < 85 ? 'color-warn' : 'color-danger'
const getTextColorClass = (p) => p < 60 ? 'text-safe' : p < 85 ? 'text-warn' : 'text-danger'

const searchQuery = ref('')
const sortKey = ref('used_memory')
const sortDesc = ref(true)
const currentPage = ref(1)
const itemsPerPage = ref(15)

const toggleSort = (key) => {
  if (sortKey.value === key) { sortDesc.value = !sortDesc.value }
  else { sortKey.value = key; sortDesc.value = true }
  currentPage.value = 1
}

const filteredProcesses = computed(() => {
  if (!searchQuery.value) return processes.value
  const q = searchQuery.value.toLowerCase()
  return processes.value.filter(p => p.name.toLowerCase().includes(q) || String(p.pid).includes(q))
})

const sortedProcesses = computed(() => {
  return [...filteredProcesses.value].sort((a, b) => {
    let va = a[sortKey.value] ?? 0, vb = b[sortKey.value] ?? 0
    if (va < vb) return sortDesc.value ? 1 : -1
    if (va > vb) return sortDesc.value ? -1 : 1
    return 0
  })
})

const totalPages = computed(() => Math.ceil(sortedProcesses.value.length / itemsPerPage.value) || 1)
const paginatedProcesses = computed(() => {
  const s = (currentPage.value - 1) * itemsPerPage.value
  return sortedProcesses.value.slice(s, s + itemsPerPage.value)
})
const prevPage = () => { if (currentPage.value > 1) currentPage.value-- }
const nextPage = () => { if (currentPage.value < totalPages.value) currentPage.value++ }
watch(searchQuery, () => { currentPage.value = 1 })
</script>

<template>
  <div>
    <div class="status-header">
      <span class="live-indicator" :style="{ backgroundColor: statusColor }"></span>
      {{ statusText }}
    </div>

    <div v-if="!system" style="text-align:center; color: var(--text-secondary); padding: 4rem 0;">Waiting for data stream...</div>

    <div v-else class="dashboard">
      <div class="glass-card sys-stats">
        <div class="stat-item">
          <span class="stat-label">CPU Usage</span>
          <span class="stat-value" :class="getTextColorClass(system.cpu_percent)">{{ system.cpu_percent.toFixed(1) }}%</span>
        </div>
        <div class="stat-item">
          <span class="stat-label">System Memory</span>
          <span class="stat-value" :class="getTextColorClass(system.memory.percent)">{{ system.memory.percent.toFixed(1) }}%</span>
          <span style="font-size: 0.8rem; color: var(--text-secondary)">{{ formatGB(system.memory.used) }} / {{ formatGB(system.memory.total) }}</span>
        </div>
      </div>

      <div v-for="gpu in gpus" :key="gpu.id" class="gpu-section">
        <div class="glass-card gpu-header-card">
          <div class="gpu-title-row">
            <h2>GPU {{ gpu.id }}: {{ gpu.name }}</h2>
            <div class="gpu-badges">
              <span class="badge badge-accent">{{ gpu.performance_state }}</span>
              <span class="badge badge-dim">Driver {{ gpu.driver_version }}</span>
              <span class="badge badge-dim">{{ gpu.device_info.architecture }}</span>
              <span class="badge badge-dim">CUDA {{ gpu.device_info.cuda_capability }}</span>
            </div>
          </div>
        </div>
        <div class="gpu-detail-grid">
          <div class="glass-card">
            <h3>Utilization & Clocks</h3>
            <div class="metric-item"><div class="metric-header"><span>GPU Core</span><span>{{ gpu.utilization.gpu }}%</span></div><div class="progress-container"><div class="progress-bar" :class="getColorClass(gpu.utilization.gpu)" :style="{ width: gpu.utilization.gpu + '%' }"></div></div></div>
            <div class="metric-item"><div class="metric-header"><span>Memory Controller</span><span>{{ gpu.utilization.memory }}%</span></div><div class="progress-container"><div class="progress-bar" :class="getColorClass(gpu.utilization.memory)" :style="{ width: gpu.utilization.memory + '%' }"></div></div></div>
            <div class="metric-item"><div class="metric-header"><span>Encoder</span><span>{{ gpu.engines.encoder }}%</span></div><div class="progress-container"><div class="progress-bar" :class="getColorClass(gpu.engines.encoder)" :style="{ width: gpu.engines.encoder + '%' }"></div></div></div>
            <div class="metric-item"><div class="metric-header"><span>Decoder</span><span>{{ gpu.engines.decoder }}%</span></div><div class="progress-container"><div class="progress-bar" :class="getColorClass(gpu.engines.decoder)" :style="{ width: gpu.engines.decoder + '%' }"></div></div></div>
            <div class="metric-item"><div class="metric-header"><span>JPEG Engine</span><span>{{ gpu.engines.jpeg }}%</span></div><div class="progress-container"><div class="progress-bar" :class="getColorClass(gpu.engines.jpeg)" :style="{ width: gpu.engines.jpeg + '%' }"></div></div></div>
            <div class="metric-item"><div class="metric-header"><span>OFA Engine</span><span>{{ gpu.engines.ofa }}%</span></div><div class="progress-container"><div class="progress-bar" :class="getColorClass(gpu.engines.ofa)" :style="{ width: gpu.engines.ofa + '%' }"></div></div></div>
            <div class="kv-row"><span>Core Clock</span><span>{{ gpu.clocks.graphics }} MHz</span></div>
            <div class="kv-row"><span>Memory Clock</span><span>{{ gpu.clocks.memory }} MHz</span></div>
            <div class="kv-row"><span>Throttle Reason</span><span>{{ gpu.throttle_reasons.join(', ') }}</span></div>
          </div>
          <div class="glass-card">
            <h3>Memory & Thermal & Power</h3>
            <div class="metric-item"><div class="metric-header"><span>VRAM</span><span>{{ gpu.memory.percent.toFixed(1) }}%</span></div><div class="progress-container"><div class="progress-bar" :class="getColorClass(gpu.memory.percent)" :style="{ width: gpu.memory.percent + '%' }"></div></div><div class="metric-sub">{{ formatBytesAuto(gpu.memory.used) }} / {{ formatBytesAuto(gpu.memory.total) }} ({{ formatBytesAuto(gpu.memory.free) }} free)</div></div>
            <div class="metric-item"><div class="metric-header"><span>BAR1 Memory</span><span>{{ formatBytesAuto(gpu.bar1.used) }} / {{ formatBytesAuto(gpu.bar1.total) }}</span></div><div class="progress-container"><div class="progress-bar color-safe" :style="{ width: (gpu.bar1.total > 0 ? gpu.bar1.used / gpu.bar1.total * 100 : 0) + '%' }"></div></div></div>
            <div class="metric-item"><div class="metric-header"><span>Temperature</span><span :class="getTextColorClass(gpu.temperature)">{{ gpu.temperature }}°C</span></div><div class="progress-container"><div class="progress-bar" :class="getColorClass(gpu.temperature)" :style="{ width: Math.min(gpu.temperature, 100) + '%' }"></div></div><div class="metric-sub">Margin to throttle: {{ gpu.temperature_margin }}°C</div></div>
            <div class="metric-item"><div class="metric-header"><span>Fan</span><span>{{ gpu.fan_speed }}% ({{ gpu.fan_rpm }} RPM)</span></div><div class="progress-container"><div class="progress-bar" :class="getColorClass(gpu.fan_speed)" :style="{ width: gpu.fan_speed + '%' }"></div></div><div class="metric-sub">{{ gpu.num_fans }} fan(s)</div></div>
            <div class="metric-item"><div class="metric-header"><span>Power</span><span>{{ gpu.power.usage.toFixed(0) }}W / {{ gpu.power.limit.toFixed(0) }}W</span></div><div class="progress-container"><div class="progress-bar" :class="getColorClass(gpu.power.limit > 0 ? gpu.power.usage / gpu.power.limit * 100 : 0)" :style="{ width: (gpu.power.limit > 0 ? gpu.power.usage / gpu.power.limit * 100 : 0) + '%' }"></div></div><div class="metric-sub">Range: {{ gpu.power.min_limit.toFixed(0) }} - {{ gpu.power.max_limit.toFixed(0) }}W | Default: {{ gpu.power.default_limit.toFixed(0) }}W</div></div>
            <div class="kv-row"><span>Total Energy</span><span>{{ gpu.total_energy_kwh }} kWh</span></div>
          </div>
          <div class="glass-card">
            <h3>Device & PCIe Info</h3>
            <div class="kv-row"><span>Architecture</span><span>{{ gpu.device_info.architecture }}</span></div>
            <div class="kv-row"><span>CUDA Capability</span><span>{{ gpu.device_info.cuda_capability }}</span></div>
            <div class="kv-row"><span>CUDA Cores</span><span>{{ gpu.device_info.num_cores.toLocaleString() }}</span></div>
            <div class="kv-row"><span>Memory Bus</span><span>{{ gpu.device_info.memory_bus_width }}-bit</span></div>
            <div class="kv-row"><span>VBIOS</span><span>{{ gpu.device_info.vbios_version }}</span></div>
            <div class="kv-row"><span>UUID</span><span class="uuid-text">{{ gpu.device_info.uuid }}</span></div>
            <div style="border-top: 1px solid var(--border-color); margin: 1rem 0;"></div>
            <div class="kv-row"><span>PCIe Link</span><span>Gen{{ gpu.pcie.gen }} x{{ gpu.pcie.width }} (Max: Gen{{ gpu.pcie.max_gen }} x{{ gpu.pcie.max_width }})</span></div>
            <div class="kv-row"><span>PCIe Tx</span><span>{{ formatBytesAuto(gpu.pcie.tx * 1024) }}/s</span></div>
            <div class="kv-row"><span>PCIe Rx</span><span>{{ formatBytesAuto(gpu.pcie.rx * 1024) }}/s</span></div>
            <div style="border-top: 1px solid var(--border-color); margin: 1rem 0;"></div>
            <div class="kv-row"><span>Supported Mem Clocks</span><span>{{ gpu.device_info.supported_mem_clocks.join(', ') }} MHz</span></div>
          </div>
        </div>

        <!-- CUDA Runtime Info -->
        <div v-if="cudaInfo && cudaInfo.available && cudaInfo.devices[gpu.id]" class="glass-card" style="margin-top: 0.5rem;">
          <h3>CUDA Runtime Details</h3>
          <div class="cuda-grid">
            <div class="cuda-col">
              <div class="kv-row"><span>CUDA Runtime</span><span>v{{ cudaInfo.versions.runtime }}</span></div>
              <div class="kv-row"><span>SM Count</span><span>{{ cudaInfo.devices[gpu.id].sm_count }}</span></div>
              <div class="kv-row"><span>Max Threads/SM</span><span>{{ cudaInfo.devices[gpu.id].max_threads_per_sm.toLocaleString() }}</span></div>
              <div class="kv-row"><span>Max Warps/SM</span><span>{{ cudaInfo.devices[gpu.id].max_warps_per_sm }}</span></div>
              <div class="kv-row"><span>Max Blocks/SM</span><span>{{ cudaInfo.devices[gpu.id].max_blocks_per_sm }}</span></div>
              <div class="kv-row"><span>Max Threads/Block</span><span>{{ cudaInfo.devices[gpu.id].max_threads_per_block }}</span></div>
              <div class="kv-row"><span>Warp Size</span><span>{{ cudaInfo.devices[gpu.id].warp_size }}</span></div>
            </div>
            <div class="cuda-col">
              <div class="kv-row"><span>Shared Mem/Block</span><span>{{ (cudaInfo.devices[gpu.id].shared_mem_per_block / 1024).toFixed(0) }} KB</span></div>
              <div class="kv-row"><span>Shared Mem/SM</span><span>{{ (cudaInfo.devices[gpu.id].shared_mem_per_sm / 1024).toFixed(0) }} KB</span></div>
              <div class="kv-row"><span>Shared Mem Optin</span><span>{{ (cudaInfo.devices[gpu.id].shared_mem_per_block_optin / 1024).toFixed(0) }} KB</span></div>
              <div class="kv-row"><span>Registers/Block</span><span>{{ cudaInfo.devices[gpu.id].regs_per_block.toLocaleString() }}</span></div>
              <div class="kv-row"><span>Registers/SM</span><span>{{ cudaInfo.devices[gpu.id].regs_per_sm.toLocaleString() }}</span></div>
              <div class="kv-row"><span>L2 Cache</span><span>{{ (cudaInfo.devices[gpu.id].l2_cache_size / 1024 / 1024).toFixed(0) }} MB</span></div>
              <div class="kv-row"><span>Persisting L2</span><span>{{ (cudaInfo.devices[gpu.id].persisting_l2_max / 1024 / 1024).toFixed(0) }} MB</span></div>
            </div>
            <div class="cuda-col">
              <div class="kv-row"><span>Const Memory</span><span>{{ (cudaInfo.devices[gpu.id].total_const_mem / 1024).toFixed(0) }} KB</span></div>
              <div class="kv-row"><span>ECC</span><span>{{ cudaInfo.devices[gpu.id].ecc_enabled ? 'Enabled' : 'Disabled' }}</span></div>
              <div class="kv-row"><span>Concurrent Kernels</span><span>{{ cudaInfo.devices[gpu.id].concurrent_kernels ? 'Yes' : 'No' }}</span></div>
              <div class="kv-row"><span>Async Engines</span><span>{{ cudaInfo.devices[gpu.id].async_engine_count }}</span></div>
              <div class="kv-row"><span>Compute Preemption</span><span>{{ cudaInfo.devices[gpu.id].compute_preemption ? 'Yes' : 'No' }}</span></div>
              <div class="kv-row"><span>Cooperative Launch</span><span>{{ cudaInfo.devices[gpu.id].cooperative_launch ? 'Yes' : 'No' }}</span></div>
              <div class="kv-row"><span>MPS</span><span>{{ cudaInfo.devices[gpu.id].mps_enabled ? 'Enabled' : 'Disabled' }}</span></div>
            </div>
          </div>
          <div v-if="cudaInfo.memory[gpu.id]" style="margin-top:0.75rem;">
            <div class="metric-item">
              <div class="metric-header"><span>CUDA Runtime Memory</span><span>{{ cudaInfo.memory[gpu.id].percent.toFixed(1) }}%</span></div>
              <div class="progress-container"><div class="progress-bar" :class="getColorClass(cudaInfo.memory[gpu.id].percent)" :style="{ width: cudaInfo.memory[gpu.id].percent + '%' }"></div></div>
              <div class="metric-sub">{{ formatBytesAuto(cudaInfo.memory[gpu.id].used) }} / {{ formatBytesAuto(cudaInfo.memory[gpu.id].total) }} ({{ formatBytesAuto(cudaInfo.memory[gpu.id].free) }} free)</div>
            </div>
          </div>
        </div>
      </div>

      <div class="glass-card table-container">
        <div class="table-controls"><h2 style="margin-bottom:0">Process VRAM Usage</h2><input type="text" v-model="searchQuery" placeholder="Search by name or PID..." class="search-box" /></div>
        <table>
          <thead><tr>
            <th class="sortable" @click="toggleSort('pid')">PID <span class="sort-icon" :class="{ active: sortKey === 'pid' }">{{ sortKey === 'pid' ? (sortDesc ? '▼' : '▲') : '▼' }}</span></th>
            <th class="sortable" @click="toggleSort('name')">Process <span class="sort-icon" :class="{ active: sortKey === 'name' }">{{ sortKey === 'name' ? (sortDesc ? '▼' : '▲') : '▼' }}</span></th>
            <th class="sortable" @click="toggleSort('gpu_id')">GPU <span class="sort-icon" :class="{ active: sortKey === 'gpu_id' }">{{ sortKey === 'gpu_id' ? (sortDesc ? '▼' : '▲') : '▼' }}</span></th>
            <th class="sortable" @click="toggleSort('used_memory')">Dedicated <span class="sort-icon" :class="{ active: sortKey === 'used_memory' }">{{ sortKey === 'used_memory' ? (sortDesc ? '▼' : '▲') : '▼' }}</span></th>
            <th class="sortable" @click="toggleSort('shared_memory')">Shared <span class="sort-icon" :class="{ active: sortKey === 'shared_memory' }">{{ sortKey === 'shared_memory' ? (sortDesc ? '▼' : '▲') : '▼' }}</span></th>
          </tr></thead>
          <tbody>
            <tr v-for="proc in paginatedProcesses" :key="`${proc.pid}-${proc.gpu_id}`"><td>{{ proc.pid }}</td><td><strong>{{ proc.name }}</strong></td><td>{{ formatGpuLabel(proc) }}</td><td class="td-memory">{{ formatBytesAuto(proc.used_memory) }}</td><td class="td-memory" style="color:var(--text-secondary)">{{ proc.shared_memory ? formatBytesAuto(proc.shared_memory) : '-' }}</td></tr>
            <tr v-if="paginatedProcesses.length === 0"><td colspan="5" style="text-align:center; padding: 2rem;">No matching processes.</td></tr>
          </tbody>
        </table>
        <div class="table-controls" style="margin-top:1rem; justify-content:flex-end;"><div class="pagination"><button class="page-btn" :disabled="currentPage === 1" @click="prevPage">Prev</button><span class="page-info">{{ currentPage }} / {{ totalPages }} ({{ filteredProcesses.length }} items)</span><button class="page-btn" :disabled="currentPage >= totalPages" @click="nextPage">Next</button></div></div>
      </div>
    </div>
  </div>
</template>
