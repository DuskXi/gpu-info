import assert from 'node:assert/strict'
import test from 'node:test'
import { processVramComposition } from '../src/processVram.js'

test('pie groups process local readings without claiming a physical device partition', () => {
  const frame = {
    gpus: [{ id: 0, memory: { used: 100 } }],
    processes: [
      { gpu_id: 0, pid: 1, name: 'compute', local_memory: 80, used_memory: 80 },
      { gpu_id: 0, pid: 2, name: 'dwm', local_memory: 40, used_memory: 40 },
      { gpu_id: 0, pid: 3, name: 'other', local_memory: 10, used_memory: 10 },
      { gpu_id: 0, pid: 4, name: 'unknown', local_memory: null, used_memory: 100 },
      { gpu_id: 1, pid: 5, name: 'another GPU', local_memory: 20 },
    ],
  }
  const chart = processVramComposition(frame, 0, 2)
  assert.equal(chart.total, 130)
  assert.equal(chart.deviceUsed, 100)
  assert.deepEqual(chart.slices.map(slice => [slice.name, slice.value]),
    [['compute', 80], ['dwm', 40], ['其他 1 个进程', 10]])
  assert.ok(Math.abs(chart.slices.reduce((sum, slice) => sum + slice.percent, 0) - 100) < 1e-9)
})

test('NVML process usage is shown when no Windows local reading exists', () => {
  const chart = processVramComposition({
    gpus: [{ id: 1, memory: { used: 80 } }],
    processes: [
      { gpu_id: 1, pid: 7, name: 'compute', memory_source: 'NVML',
        local_memory: null, used_memory: 50 },
      { gpu_id: 1, pid: 8, name: 'unknown', memory_source: 'Windows GPU counters',
        local_memory: null, used_memory: 60 },
    ],
  }, 1)
  assert.equal(chart.total, 50)
  assert.deepEqual(chart.slices.map(slice => slice.pid), [7])
})
