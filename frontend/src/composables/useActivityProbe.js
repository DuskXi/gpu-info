import { ref, shallowRef } from 'vue'

const result = shallowRef(null)
const pipelineResult = shallowRef(null)
const busy = ref(false)
const error = ref('')
const pipelineError = ref('')
const initialRequests = new Map()

async function load(kind) {
  const target = kind === 'pipelines' ? pipelineResult : result
  if (target.value) return target.value
  if (!initialRequests.has(kind)) {
    const request = fetch(`/api/activity-probe?kind=${kind}`)
      .then(async response => {
        if (response.status === 404) return null
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        target.value = await response.json()
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

async function captureKind(gpuId, kind) {
  if (busy.value) return
  busy.value = true
  const targetError = kind === 'pipelines' ? pipelineError : error
  targetError.value = ''
  try {
    const response = await fetch(`/api/activity-probe?gpu=${encodeURIComponent(gpuId)}&kind=${kind}`, { method: 'POST' })
    const payload = await response.json()
    if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`)
    if (kind === 'pipelines') pipelineResult.value = payload
    else result.value = payload
  } catch (cause) {
    targetError.value = cause.message || '采样失败'
  } finally {
    busy.value = false
  }
}

export function useActivityProbe() {
  return {
    activityProbe: result, pipelineProbe: pipelineResult,
    probeBusy: busy, probeError: error, pipelineError,
    loadLatest,
    capture: gpuId => captureKind(gpuId, 'general'),
    capturePipelines: gpuId => captureKind(gpuId, 'pipelines'),
  }
}
