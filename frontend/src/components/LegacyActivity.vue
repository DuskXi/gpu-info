<script setup>
import { computed, onMounted, ref } from 'vue'
import { useActivityProbe } from '../composables/useActivityProbe'
import { number, time } from '../format'
const props = defineProps({ gpus: Array, sampling: Object })
const gpu = ref('0')
const { activityProbe, probeBusy, probeError, capture, loadLatest } = useActivityProbe()
onMounted(loadLatest)
const metrics = [
  ['gr_active', '图形 / 计算前端'], ['compute_sync', '同步计算派发'], ['compute_async', '异步计算派发'],
  ['compute_warps', '计算 warp'], ['sm_active', 'SM 活跃'], ['sm_issue', 'SM 指令发射'],
  ['tensor_active', 'Tensor 活跃'], ['unallocated_warps', '未分配 warp 槽位'],
  ['dram_read', '显存读取带宽'], ['dram_write', '显存写入带宽'],
  ['graphics_vertex', '顶点 / 几何 warp'], ['graphics_pixel', '像素 warp'],
]
const blocked = computed(() => probeBusy.value || props.sampling?.enabled)
</script>
<template>
  <details class="legacy-hardware">
    <summary>扩展引擎与显存带宽采样 · Nsight</summary>
    <div class="activity-probe-controls">
      <select v-model="gpu" aria-label="扩展采样 GPU" :disabled="blocked"><option v-for="device in gpus" :key="device.id" :value="String(device.id)">GPU {{ device.id }}</option></select>
      <button :disabled="blocked" @click="capture(Number(gpu))">{{ probeBusy ? '正在采样…' : '采集 5 秒' }}</button>
    </div>
    <p class="data-note">保留原有的计算派发、图形 warp 和显存读写指标。需要先停止连续硬件采样；本报告是独立短窗口，不能与其他组当作同步读数。WDDM / 派发活动不等于 CUDA 核心使用率。</p>
    <p v-if="probeError" class="notice">{{ probeError }}</p>
    <div v-if="activityProbe" class="activity-grid activity-grid-detailed">
      <article v-for="[key, label] in metrics" :key="key" class="activity-reading"><span>{{ label }}</span><strong><RollingValue :value="number(activityProbe.metrics?.[key]?.mean_percent, 1, '%')" /></strong></article>
    </div>
    <p v-if="activityProbe" class="data-note">GPU {{ activityProbe.gpu_id }} · {{ time(activityProbe.captured_at) }} 的历史短窗口</p>
  </details>
</template>
<style scoped>
.legacy-hardware { margin: 20px 0; font-size: 12px; color: var(--muted); }
summary { cursor: pointer; margin-bottom: 12px; }
.activity-probe-controls { margin-bottom: 12px; }
</style>
