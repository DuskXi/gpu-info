export function topProcessTargets(processes, metric, gpuFilter, count) {
  if (!metric) return []
  return processes
    .filter(process => gpuFilter === 'all' || process.gpu_id === Number(gpuFilter))
    .map(process => ({ process, current: metric.read(process, process) }))
    .filter(item => Number.isFinite(item.current))
    .sort((a, b) => b.current - a.current || a.process.key.localeCompare(b.process.key))
    .slice(0, count)
    .map(({ process }) => ({
      id: process.key,
      name: `${process.name || '未知进程'} · ${process.pid} / GPU ${process.gpu_id}`,
    }))
}
