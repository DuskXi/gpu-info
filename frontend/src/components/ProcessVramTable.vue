<script setup>
// Process VRAM table with its accounting check. With `gpuId` set it shows only that device
// (multi-GPU layout puts one under each device card); without it, all devices with a filter.
import { computed, ref } from 'vue'
import RollingValue from './RollingValue.vue'
import { bytes, time } from '../format'

const props = defineProps({
  processes: { type: Array, required: true },
  gpus: { type: Array, required: true },
  accounting: { type: Array, required: true },
  sampledAt: { type: Number, default: null },
  gpuId: { type: Number, default: null },
})

const query = ref('')
const gpuFilter = ref('all')
const sort = ref('used_memory')
const descending = ref(true)
const showAll = ref(false)

const scoped = computed(() => props.gpuId != null)
const gpuCapacity = computed(() => new Map(props.gpus.map(gpu => [gpu.id, gpu.memory?.total])))
const accounts = computed(() => scoped.value
  ? props.accounting.filter(account => account.gpuId === props.gpuId) : props.accounting)

const shownProcesses = computed(() => {
  const q = query.value.trim().toLowerCase()
  const device = scoped.value ? props.gpuId : gpuFilter.value === 'all' ? null : Number(gpuFilter.value)
  return props.processes
    .filter(p => device == null || p.gpu_id === device)
    .filter(p => !q || p.name?.toLowerCase().includes(q) || String(p.pid).includes(q))
    .sort((a, b) => {
      if (sort.value !== 'name') {
        const av = Number.isFinite(a[sort.value]) ? a[sort.value] : null
        const bv = Number.isFinite(b[sort.value]) ? b[sort.value] : null
        if (av == null || bv == null) return av == null ? (bv == null ? 0 : 1) : -1
      }
      const av = a[sort.value] ?? ''
      const bv = b[sort.value] ?? ''
      const order = typeof av === 'string' ? String(av).localeCompare(String(bv)) : av - bv
      return descending.value ? -order : order
    })
})
const visibleProcesses = computed(() =>
  showAll.value || query.value ? shownProcesses.value : shownProcesses.value.slice(0, 20)
)
const columns = computed(() => scoped.value ? 4 : 5)

function sortBy(key) {
  descending.value = sort.value === key ? !descending.value : key !== 'name'
  sort.value = key
}
function counterOverCapacity(proc) {
  const capacity = gpuCapacity.value.get(proc.gpu_id)
  return Number.isFinite(proc.claimed_memory) && Number.isFinite(capacity)
    && proc.claimed_memory > capacity
}
</script>

<template>
  <section class="section-block process-section" :class="{ 'process-section-scoped': scoped }"
    :aria-label="scoped ? `GPU ${gpuId} 进程显存` : undefined" :aria-labelledby="scoped ? undefined : 'process-heading'">
    <div class="section-heading">
      <div v-if="scoped"><p class="eyebrow">GPU {{ String(gpuId).padStart(2, '0') }} / PROCESSES</p><h2>进程显存</h2></div>
      <div v-else><p class="eyebrow">02 / PROCESSES</p><h2 id="process-heading">进程显存</h2></div>
      <span class="section-aside">进程采样 <RollingValue :value="time(sampledAt)" :amount="sampledAt" /> · GPU / PID 去重</span>
    </div>
    <details v-for="account in accounts" :key="account.gpuId" class="accounting-disclosure">
      <summary>
        <span>GPU <RollingValue :value="account.gpuId" /> · 显存口径核对</span>
        <strong>设备已用 <RollingValue :value="bytes(account.nvmlUsed)" :amount="account.nvmlUsed" /> <span class="accounting-summary-source">NVML</span></strong>
      </summary>
      <div class="accounting-content">
        <div class="accounting-values">
          <div><span>NVML 设备已用</span><strong><RollingValue :value="bytes(account.nvmlUsed)" :amount="account.nvmlUsed" /></strong></div>
          <div><span>Windows 设备专用使用</span><strong><RollingValue :value="bytes(account.adapterUsed)" :amount="account.adapterUsed" /></strong></div>
          <div><span>Σ <RollingValue :value="account.localCount" /> 个进程本地用量 · 含重复</span><strong><RollingValue :value="bytes(account.processLocal)" :amount="account.processLocal" /></strong></div>
          <div><span>Σ <RollingValue :value="account.processCount" /> 个进程专用计数 · 含重复</span><strong><RollingValue :value="bytes(account.processClaimed)" :amount="account.processClaimed" /></strong></div>
        </div>
        <p>设备已用以 NVML 读数为准。Σ 仅用于排查计数差异，不能当作设备占用或剩余显存。本地用量可包含共享窗口表面；Windows 专用计数还可能异常膨胀。DWM 的独占显存不能从这些计数直接拆出。</p>
        <a href="https://devblogs.microsoft.com/directx/gpus-in-the-task-manager/" target="_blank" rel="noopener noreferrer">微软的计数器说明 ↗</a>
      </div>
    </details>
    <div class="table-toolbar">
      <input v-model="query" type="search" placeholder="搜索进程名或 PID" :aria-label="scoped ? `搜索 GPU ${gpuId} 进程` : '搜索进程'" />
      <select v-if="!scoped" v-model="gpuFilter" aria-label="筛选 GPU">
        <option value="all">全部 GPU</option>
        <option v-for="gpu in gpus" :key="gpu.id" :value="String(gpu.id)">GPU {{ gpu.id }}</option>
      </select>
      <span><RollingValue :value="shownProcesses.length" /> 个进程</span>
    </div>
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th><button @click="sortBy('name')">进程 {{ sort === 'name' ? (descending ? '↓' : '↑') : '' }}</button></th>
          <th><button @click="sortBy('pid')">PID {{ sort === 'pid' ? (descending ? '↓' : '↑') : '' }}</button></th>
          <th v-if="!scoped"><button @click="sortBy('gpu_id')">设备 {{ sort === 'gpu_id' ? (descending ? '↓' : '↑') : '' }}</button></th>
          <th class="numeric"><button @click="sortBy('used_memory')" title="Windows Local Usage；可包含跨进程共享资源">本地用量 {{ sort === 'used_memory' ? (descending ? '↓' : '↑') : '' }}</button></th>
          <th class="numeric"><button @click="sortBy('claimed_memory')" title="Windows Dedicated Usage；可能包含共享资源，也可能异常膨胀">专用计数 {{ sort === 'claimed_memory' ? (descending ? '↓' : '↑') : '' }}</button></th>
        </tr></thead>
        <tbody>
          <tr v-for="proc in visibleProcesses" :key="`${proc.gpu_id}:${proc.pid}`">
            <td class="process-name">{{ proc.name }}</td>
            <td class="mono"><RollingValue :value="proc.pid" /></td>
            <td v-if="!scoped">GPU <RollingValue :value="proc.gpu_id" /></td>
            <td class="numeric strong"><RollingValue :value="bytes(proc.used_memory)" :amount="proc.used_memory" /></td>
            <td class="numeric" :class="{ 'counter-over-capacity': counterOverCapacity(proc) }">
              <RollingValue :value="bytes(proc.claimed_memory)" :amount="proc.claimed_memory" />
              <small v-if="counterOverCapacity(proc)">超物理容量</small>
            </td>
          </tr>
          <tr v-if="!visibleProcesses.length"><td :colspan="columns" class="empty-cell">没有匹配的占用进程</td></tr>
        </tbody>
      </table>
    </div>
    <button v-if="!query && shownProcesses.length > 20" class="table-more" @click="showAll = !showAll">
      <template v-if="showAll">收起进程列表</template><template v-else>显示全部 <RollingValue :value="shownProcesses.length" /> 个进程</template> <span aria-hidden="true">{{ showAll ? '↑' : '↓' }}</span>
    </button>
    <p class="data-note">进程本地用量与专用计数均可能包含跨进程共享资源，不能相加。专用计数不是进程独占 VRAM；核显未并入 NVIDIA 列表。</p>
  </section>
</template>
