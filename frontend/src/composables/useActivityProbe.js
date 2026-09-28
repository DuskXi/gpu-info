import { ref, shallowRef } from 'vue'
import { rollActivity } from '../activityRolling'

const result = shallowRef(null)
const pipelineResult = shallowRef(null)
const busy = ref(false)
const error = ref('')
const pipelineError = ref('')
const autoEnabled = ref(false)
const autoGpuId = ref(null)
const autoKind = ref(null)
const initialRequests = new Map()
const rollingHistories = new Map()
const AUTO_CAPTURE_SECONDS = 2
const AUTO_PAUSE_MS = 15_000
let autoTimer = null
let autoGeneration = 0

async function load(kind) {
  const target = kind === 'pipelines' ? pipelineResult : result
  if (target.value) return target.value
  if (!initialRequests.has(kind)) {
    const request = fetch(`/api/activity-probe?kind=${kind}`)
      .then(async response => {
        if (response.status === 404) return null
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        const payload = await response.json()
        if (!target.value) target.value = payload
        return target.value
      })
      .catch(() => null)
      .finally(() => { initialRequests.delete(kind) })
    initialRequests.set(kind, request)
  }
  return initialRequests.get(kind)
}

async function loadLatest() {
  await Promise.all([load('general'), load('pipelines')])
}

async function captureKind(gpuId, kind, duration = 5, rolling = false) {
  if (busy.value) return false
  busy.value = true
  const targetError = kind === 'pipelines' ? pipelineError : error
  targetError.value = ''
  try {
    const response = await fetch(`/api/activity-probe?gpu=${encodeURIComponent(gpuId)}&kind=${kind}&duration=${duration}`, { method: 'POST' })
    const payload = await response.json()
    if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`)
    let display = payload
    if (rolling) {
      const key = `${gpuId}:${kind}`
      const next = rollActivity(rollingHistories.get(key) ?? [], payload)
      rollingHistories.set(key, next.history)
      display = next.display
    }
    if (kind === 'pipelines') pipelineResult.value = display
    else result.value = display
    return true
  } catch (cause) {
    targetError.value = cause.message || '采样失败'
    return false
  } finally {
    busy.value = false
  }
}

function stopAuto() {
  autoGeneration += 1
  if (autoTimer !== null) clearTimeout(autoTimer)
  autoTimer = null
  autoEnabled.value = false
  autoGpuId.value = null
  autoKind.value = null
}

async function runAuto(generation) {
  if (!autoEnabled.value || generation !== autoGeneration) return
  const captured = await captureKind(
    autoGpuId.value, autoKind.value, AUTO_CAPTURE_SECONDS, true,
  )
  if (!autoEnabled.value || generation !== autoGeneration) return
  if (!captured) {
    stopAuto()
    return
  }
  autoTimer = setTimeout(() => runAuto(generation), AUTO_PAUSE_MS)
}

function toggleAuto(gpuId, kind = 'general') {
  if (autoEnabled.value) {
    stopAuto()
    return
  }
  if (busy.value) return
  autoGpuId.value = Number(gpuId)
  autoKind.value = kind
  rollingHistories.delete(`${autoGpuId.value}:${kind}`)
  autoEnabled.value = true
  autoGeneration += 1
  void runAuto(autoGeneration)
}

export function useActivityProbe() {
  return {
    activityProbe: result, pipelineProbe: pipelineResult,
    probeBusy: busy, probeError: error, pipelineError,
    autoEnabled, autoGpuId, autoKind, toggleAuto,
    loadLatest,
    capture: gpuId => captureKind(gpuId, 'general'),
    capturePipelines: gpuId => captureKind(gpuId, 'pipelines'),
  }
}
