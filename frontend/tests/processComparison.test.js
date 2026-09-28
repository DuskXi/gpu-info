import test from 'node:test'
import assert from 'node:assert/strict'
import { topProcessTargets } from '../src/processComparison.js'

const metric = { read: process => process.sm_util }
const processes = [
  { key: '0:10', name: 'train', pid: 10, gpu_id: 0, sm_util: 88 },
  { key: '0:20', name: 'idle', pid: 20, gpu_id: 0, sm_util: 0 },
  { key: '1:30', name: 'render', pid: 30, gpu_id: 1, sm_util: 95 },
  { key: '0:40', name: 'unknown', pid: 40, gpu_id: 0, sm_util: null },
]

test('top process comparison ranks valid current values within the selected GPU', () => {
  assert.deepEqual(topProcessTargets(processes, metric, '0', 3), [
    { id: '0:10', name: 'train · 10 / GPU 0' },
    { id: '0:20', name: 'idle · 20 / GPU 0' },
  ])
  assert.deepEqual(topProcessTargets(processes, metric, 'all', 2).map(row => row.id),
    ['1:30', '0:10'])
})
