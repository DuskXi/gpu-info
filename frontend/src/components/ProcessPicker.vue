<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { bytes, throughput } from '../format'

const props = defineProps({
  processes: { type: Array, required: true },
  selectedKey: { type: String, default: '' },
})
const emit = defineEmits(['select'])
const picker = ref(null)
const list = ref(null)
const query = ref('')
const open = ref(false)
const sortKey = ref('used_memory')
const descending = ref(true)
const fields = [
  { key: 'name', label: '进程' },
  { key: 'used_memory', label: '本地用量' },
  { key: 'cpu_percent', label: 'CPU' },
  { key: 'disk_bytes_per_sec', label: '磁盘 I/O' },
  { key: 'gpu_percent', label: 'GPU' },
]

const selected = computed(() => props.processes.find(p => p.key === props.selectedKey))
const hasWindowsMemory = computed(() => props.processes.some(p => p.memory_source === 'Windows GPU counters'))
function activityPercent(value) {
  if (value == null || !Number.isFinite(value)) return '—'
  if (value > 0 && value < 0.1) return '<0.1%'
  return `${value.toFixed(value > 0 && value < 10 ? 1 : 0)}%`
}
const rows = computed(() => {
  const search = query.value.trim().toLowerCase()
  return props.processes
    .filter(p => !search || p.name?.toLowerCase().includes(search) || String(p.pid).includes(search))
    .sort((a, b) => {
      if (sortKey.value === 'name') {
        const names = String(a.name).localeCompare(String(b.name))
        return (descending.value ? -names : names) || a.pid - b.pid
      }
      const av = Number.isFinite(a[sortKey.value]) ? a[sortKey.value] : null
      const bv = Number.isFinite(b[sortKey.value]) ? b[sortKey.value] : null
      if (av == null) return bv == null ? a.pid - b.pid : 1
      if (bv == null) return -1
      return (descending.value ? bv - av : av - bv) || a.pid - b.pid
    })
})

function changeSort(key) {
  if (sortKey.value === key) descending.value = !descending.value
  else {
    sortKey.value = key
    descending.value = key !== 'name'
  }
}
function showList(focusOption = false) {
  open.value = true
  if (focusOption) {
    nextTick(() => {
      const options = list.value?.querySelectorAll('.process-option')
      const current = [...(options ?? [])].find(option => option.getAttribute('aria-selected') === 'true')
      const target = current || options?.[0]
      target?.focus()
    })
  }
}
function choose(process) {
  emit('select', process.key)
  open.value = false
}
function moveFocus(direction) {
  const options = [...(list.value?.querySelectorAll('.process-option') ?? [])]
  const index = options.indexOf(document.activeElement)
  options[Math.max(0, Math.min(options.length - 1, index + direction))]?.focus()
}
function handleOutside(event) {
  if (!picker.value?.contains(event.target)) open.value = false
}
onMounted(() => document.addEventListener('pointerdown', handleOutside))
onUnmounted(() => document.removeEventListener('pointerdown', handleOutside))
</script>

<template>
  <div ref="picker" class="process-picker" @keydown.esc="open = false">
    <label for="process-search">观察对象</label>
    <input id="process-search" v-model="query" type="search" placeholder="搜索进程名或 PID"
      aria-label="搜索观察进程" @input="showList()" @keydown.down.prevent="showList(true)" />
    <button type="button" class="process-picker-trigger" aria-haspopup="listbox" :aria-expanded="open"
      aria-controls="process-picker-list" @click="open ? open = false : showList(true)"
      @keydown.down.prevent="showList(true)">
      <span>{{ selected ? `${selected.name} · ${selected.pid} / GPU ${selected.gpu_id}` : '选择进程' }}</span>
      <span aria-hidden="true">{{ open ? '⌃' : '⌄' }}</span>
    </button>
    <div v-if="open" class="process-picker-panel">
      <div class="process-picker-sort" role="group" aria-label="按指标排序">
        <button v-for="field in fields" :key="field.key" type="button"
          :class="{ active: sortKey === field.key }" @click="changeSort(field.key)">
          {{ field.label }} <span v-if="sortKey === field.key" aria-hidden="true">{{ descending ? '↓' : '↑' }}</span>
        </button>
      </div>
      <div id="process-picker-list" ref="list" class="process-picker-list" role="listbox"
        aria-label="观察进程" @keydown.down.prevent="moveFocus(1)"
        @keydown.up.prevent="moveFocus(-1)">
        <button v-for="process in rows" :key="process.key" type="button" role="option"
          class="process-option" :aria-selected="process.key === selectedKey"
          :aria-label="`${process.name}，PID ${process.pid}，本地用量 ${bytes(process.used_memory)}，CPU ${activityPercent(process.cpu_percent)}，磁盘 I/O ${throughput(process.disk_bytes_per_sec)}，GPU ${activityPercent(process.gpu_percent)}`"
          @click="choose(process)">
          <span class="process-option-name"><strong>{{ process.name }}</strong><small>PID {{ process.pid }} · GPU {{ process.gpu_id }}</small></span>
          <span data-label="本地用量">{{ bytes(process.used_memory) }}</span>
          <span data-label="CPU">{{ activityPercent(process.cpu_percent) }}</span>
          <span data-label="磁盘">{{ throughput(process.disk_bytes_per_sec) }}</span>
          <span data-label="GPU">{{ activityPercent(process.gpu_percent) }}</span>
        </button>
        <div v-if="!rows.length" class="process-picker-empty">没有匹配的进程</div>
      </div>
      <p class="process-picker-note">
        <template v-if="hasWindowsMemory">本地用量来自 Windows Local Usage；可包含共享窗口表面，进程数值不能相加 · </template>
        CPU 为整机占比 · 磁盘为读写速率 · GPU 为最忙引擎，必要时采用 NVML SM 负载
      </p>
    </div>
  </div>
</template>
