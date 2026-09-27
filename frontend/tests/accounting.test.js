import assert from 'node:assert/strict'
import test from 'node:test'
import { memoryAccounts } from '../src/accounting.js'

test('compares matched Windows process claims with the same adapter counter', () => {
  const frame = {
    gpus: [{ id: 0, memory: { used: 13 } }, { id: 1, memory: { used: 4 } }],
    windows_adapter_memory: [
      { gpu_id: 0, dedicated_usage: 12 },
      { gpu_id: 1, dedicated_usage: 3 },
    ],
    processes: [
      { gpu_id: 0, used_memory: 3, local_memory: 3, claimed_memory: 8, memory_source: 'Windows GPU counters' },
      { gpu_id: 0, used_memory: 4, local_memory: 4, claimed_memory: 5, memory_source: 'Windows GPU counters' },
      { gpu_id: 0, used_memory: 9, memory_source: 'NVML' },
      { gpu_id: 1, used_memory: 1, local_memory: 1, claimed_memory: 2, memory_source: 'Windows GPU counters' },
    ],
  }

  assert.deepEqual(memoryAccounts(frame), [
    { gpuId: 0, nvmlUsed: 13, adapterUsed: 12, processClaimed: 13, processLocal: 7, excess: 1, processCount: 2, localCount: 2 },
    { gpuId: 1, nvmlUsed: 4, adapterUsed: 3, processClaimed: 2, processLocal: 1, excess: 0, processCount: 1, localCount: 1 },
  ])
})

test('missing local readings remain unavailable instead of appearing as zero', () => {
  const frame = {
    gpus: [{ id: 0, memory: { used: 12 } }],
    windows_adapter_memory: [{ gpu_id: 0, dedicated_usage: 11 }],
    processes: [{ gpu_id: 0, claimed_memory: 20, local_memory: null }],
  }
  assert.deepEqual(memoryAccounts(frame), [{
    gpuId: 0, nvmlUsed: 12, adapterUsed: 11, processClaimed: 20,
    processLocal: null, excess: 9, processCount: 1, localCount: 0,
  }])
})
