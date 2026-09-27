<script setup>
import { computed, ref } from 'vue'
import { useMonitorData } from '../composables/useMonitorData'
import { bytes, number, percent, time } from '../format'
import { memoryAccounts } from '../accounting'

const { snapshot } = useMonitorData()
const system = computed(() => snapshot.value?.system)
const gpus = computed(() => snapshot.value?.gpus ?? [])
const processes = computed(() => snapshot.value?.processes ?? [])
const accounting = computed(() => memoryAccounts(snapshot.value))
const query = ref('')
const gpuFilter = ref('all')
const sort = ref('used_memory')
const descending = ref(true)
const showAll = ref(false)

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
            <div class="meter" role="meter" :aria-valuenow="gpu.memory?.percent ?? 0" aria-valuemin="0" aria-valuemax="100" aria-label="显存占用率">
              <span :style="{ width: `${Math.min(100, gpu.memory?.percent ?? 0)}%` }"></span>
            </div>
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
      </section>

      <section class="section-block process-section" aria-labelledby="process-heading">
        <div class="section-heading">
          <div><p class="eyebrow">02 / PROCESSES</p><h2 id="process-heading">进程显存</h2></div>
          <span class="section-aside">进程采样 {{ time(snapshot?.processes_sampled_at) }} · GPU / PID 去重</span>
        </div>
        <details v-for="account in accounting" :key="account.gpuId" class="accounting-disclosure">
          <summary>
            <span>GPU {{ account.gpuId }} · 显存口径核对</span>
            <strong>本地合计 {{ bytes(account.processLocal) }} · 专用记账 {{ bytes(account.processClaimed) }}</strong>
          </summary>
          <div class="accounting-content">
            <div class="accounting-values">
              <div><span>NVML 设备已用</span><strong>{{ bytes(account.nvmlUsed) }}</strong></div>
              <div><span>Windows 设备专用使用</span><strong>{{ bytes(account.adapterUsed) }}</strong></div>
              <div><span>{{ account.processCount }} 个进程本地显存合计</span><strong>{{ bytes(account.processLocal) }}</strong></div>
              <div><span>进程专用记账合计</span><strong>{{ bytes(account.processClaimed) }}</strong></div>
            </div>
            <p>本地显存表示当前位于 GPU 本地内存的用量；专用记账可能远大于实际驻留。DWM 和 csrss 可引用其他程序的窗口表面，因此两种进程数值都可能重复计算，无法据此拆出 DWM 独占的显存。</p>
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
              <th class="numeric"><button @click="sortBy('used_memory')">本地显存 {{ sort === 'used_memory' ? (descending ? '↓' : '↑') : '' }}</button></th>
              <th class="numeric"><button @click="sortBy('claimed_memory')">专用记账 {{ sort === 'claimed_memory' ? (descending ? '↓' : '↑') : '' }}</button></th>
            </tr></thead>
            <tbody>
              <tr v-for="proc in visibleProcesses" :key="`${proc.gpu_id}:${proc.pid}`">
                <td class="process-name">{{ proc.name }}</td>
                <td class="mono">{{ proc.pid }}</td>
                <td>GPU {{ proc.gpu_id }}</td>
                <td class="numeric strong">{{ bytes(proc.used_memory) }}</td>
                <td class="numeric">{{ bytes(proc.claimed_memory) }}</td>
              </tr>
              <tr v-if="!visibleProcesses.length"><td colspan="5" class="empty-cell">没有匹配的占用进程</td></tr>
            </tbody>
          </table>
        </div>
        <button v-if="!query && shownProcesses.length > 20" class="table-more" @click="showAll = !showAll">
          {{ showAll ? '收起进程列表' : `显示全部 ${shownProcesses.length} 个进程` }} <span aria-hidden="true">{{ showAll ? '↑' : '↓' }}</span>
        </button>
        <p class="data-note">本地显存与专用记账来自 Windows 不同计数器。进程列均可能包含跨进程共享资源，不能相加当作设备总量；核显未并入 NVIDIA 列表。</p>
      </section>
      </div>
    </template>
  </div>
</template>
