const MAX_WINDOWS = 3
const MAX_AGE_SECONDS = 90

export function rollActivity(history, latest) {
  const end = latest.window_ended_at ?? latest.captured_at
  const recent = [...history, latest]
    .filter(window => window.gpu_id === latest.gpu_id && window.kind === latest.kind)
    .filter(window => end - (window.window_ended_at ?? window.captured_at) <= MAX_AGE_SECONDS)
    .slice(-MAX_WINDOWS)
  const metrics = Object.fromEntries(Object.entries(latest.metrics ?? {}).map(([key, current]) => {
    const readings = recent.map(window => window.metrics?.[key]).filter(value => value?.samples > 0)
    const sampleCount = readings.reduce((sum, value) => sum + value.samples, 0)
    const mean = sampleCount
      ? readings.reduce((sum, value) => sum + value.mean_percent * value.samples, 0) / sampleCount
      : current.mean_percent
    return [key, { ...current, mean_percent: Math.round(mean * 10) / 10 }]
  }))
  const first = recent[0]
  const observedSeconds = recent.reduce((sum, window) => {
    const measured = window.window_ended_at - window.window_started_at
    return sum + (Number.isFinite(measured) && measured > 0
      ? measured : window.duration_seconds ?? 0)
  }, 0)
  return {
    history: recent,
    display: {
      ...latest,
      metrics,
      rolling: {
        windows: recent.length,
        sampled_seconds: Math.round(observedSeconds * 10) / 10,
        span_seconds: Math.round(end - (first.window_started_at ?? first.captured_at)),
      },
    },
  }
}
