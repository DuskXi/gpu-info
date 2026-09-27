const colors = ['#007c96', '#334fc1', '#bd6425', '#8e45ab', '#23775b', '#667b8e']

export function processVramComposition(frame, gpuId, visibleCount = 5) {
  const deviceUsed = frame?.gpus?.find(gpu => gpu.id === gpuId)?.memory?.used ?? null
  const rows = (frame?.processes ?? [])
    .filter(row => row.gpu_id === gpuId)
    .map(row => ({
      pid: row.pid,
      name: row.name || `PID ${row.pid}`,
      // WDDM Local Usage is the closest available per-process local VRAM
      // reading. NVML process usage is retained where Windows has no reading.
      value: Number.isFinite(row.local_memory) ? row.local_memory
        : row.memory_source === 'NVML' ? row.used_memory : null,
    }))
    .filter(row => Number.isFinite(row.value) && row.value > 0)
    .sort((a, b) => b.value - a.value)
  const total = rows.reduce((sum, row) => sum + row.value, 0)
  if (!total) return { total: 0, deviceUsed, slices: [], processCount: 0 }

  const kept = Math.max(1, visibleCount)
  const top = rows.slice(0, kept)
  const rest = rows.slice(kept)
  if (rest.length) {
    top.push({ pid: null, name: `其他 ${rest.length} 个进程`,
      value: rest.reduce((sum, row) => sum + row.value, 0) })
  }
  const slices = top.map((row, index) => ({
    ...row,
    color: colors[index % colors.length],
    percent: row.value / total * 100,
  }))
  const remaining = total - slices[0].value
  const zoom = slices[0].percent >= 70 && slices.length > 2 && remaining > 0
    ? {
        excluded: slices[0].name,
        total: remaining,
        slices: slices.slice(1).map(slice => ({
          ...slice,
          percent: slice.value / remaining * 100,
        })),
      }
    : null
  return {
    total,
    deviceUsed,
    processCount: rows.length,
    slices,
    zoom,
  }
}

export function processPieGradient(slices) {
  let start = 0
  return `conic-gradient(${slices.map(slice => {
    const stop = start + slice.percent
    const part = `${slice.color} ${start}% ${stop}%`
    start = stop
    return part
  }).join(', ')})`
}
