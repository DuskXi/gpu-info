export function memoryAccounts(frame) {
  const adapters = new Map(
    (frame?.windows_adapter_memory ?? []).map(adapter => [adapter.gpu_id, adapter])
  )
  return (frame?.gpus ?? []).flatMap(gpu => {
    const adapterUsed = adapters.get(gpu.id)?.dedicated_usage
    if (!Number.isFinite(adapterUsed)) return []
    const rows = (frame?.processes ?? []).filter(process =>
      process.gpu_id === gpu.id
      && Number.isFinite(process.claimed_memory)
    )
    if (!rows.length) return []
    const processClaimed = rows.reduce((sum, process) => sum + process.claimed_memory, 0)
    const processLocal = rows.reduce((sum, process) => sum + (process.local_memory ?? 0), 0)
    return [{
      gpuId: gpu.id,
      nvmlUsed: gpu.memory?.used ?? null,
      adapterUsed,
      processClaimed,
      processLocal,
      excess: Math.max(0, processClaimed - adapterUsed),
      processCount: rows.length,
    }]
  })
}
