export const hardwareGroups = [
  { key: 'activity', label: 'SM / Tensor', metrics: [
    { key: 'sm_active', label: 'SM 活跃' }, { key: 'tensor_active', label: 'Tensor 活跃' },
  ] },
  { key: 'cache', label: '缓存 / 并行度', metrics: [
    { key: 'l1_hit', label: 'L1 命中率' }, { key: 'l2_hit', label: 'L2 命中率' },
    { key: 'warps_active', label: 'Warp 驻留 · 全时段' },
  ] },
  { key: 'pipelines', label: '普通运算管线', metrics: [
    { key: 'fma_heavy', label: 'FMA Heavy', rate: 'fma_heavy_ips' },
    { key: 'fma_light', label: 'FMA Light', rate: 'fma_light_ips' },
    { key: 'alu', label: 'ALU', rate: 'alu_ips' },
    { key: 'sfu', label: 'SFU / XU', rate: 'sfu_ips' },
  ] },
  { key: 'tensor', label: 'Tensor 指令', metrics: [
    { key: 'tensor_ips', label: 'Tensor 指令速率', rateOnly: true },
  ] },
  { key: 'scalar', label: 'CUDA 普通浮点执行证据', metrics: [
    { key: 'fma_light_thread_ips', label: 'FMA Lite · 线程指令/s', rateOnly: true },
    { key: 'fma_light_warp_ips', label: 'FMA Lite · warp 指令/s', rateOnly: true },
    { key: 'fma_light_active_lanes', label: '每条指令的有效线程', lanes: true },
  ] },
]

// Break curves across time gaps and across helper restarts. Never carry forward a stale sample.
export function hardwareTrend(history, state, group, key, now) {
  const points = []
  const seen = new Set()
  for (const frame of history) {
    const source = frame.hardware_sampling
    const row = source?.groups?.[group]
    const value = row?.metrics?.[key]?.value
    if (source?.session_id !== state?.session_id || !Number.isFinite(value)
      || row.sampled_at < now - 60 || seen.has(row.sampled_at)) continue
    seen.add(row.sampled_at)
    points.push({ t: row.sampled_at, value })
  }
  const max = Math.max(1, ...points.map(p => p.value))
  return points.map((point, i) => {
    const command = i === 0 || point.t - points[i - 1].t > 3 ? 'M' : 'L'
    return `${command}${Math.max(0, Math.min(240, (point.t - now + 60) * 4)).toFixed(1)},${(35 - point.value / max * 31).toFixed(1)}`
  }).join(' ')
}
