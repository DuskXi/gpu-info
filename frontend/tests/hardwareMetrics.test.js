import test from 'node:test'
import assert from 'node:assert/strict'
import { hardwareTrend } from '../src/hardwareMetrics.js'

test('hardware trends deduplicate retained values and break across unobserved intervals', () => {
  const frame = (t, session = 'a') => ({ hardware_sampling: { session_id: session,
    groups: { activity: { sampled_at: t, metrics: { sm_active: { value: 30 } } } } } })
  const path = hardwareTrend([frame(70), frame(70), frame(71), frame(80), frame(81, 'old')],
    { session_id: 'a' }, 'activity', 'sm_active', 100)
  assert.equal((path.match(/M/g) ?? []).length, 2)
  assert.equal((path.match(/L/g) ?? []).length, 1)
})

test('fast rotation breaks the curve even when the gap is less than three seconds', () => {
  const frame = (t, epoch) => ({ hardware_sampling: { session_id: 'a',
    groups: { activity: { sampled_at: t, epoch, metrics: { sm_active: { value: 30 } } } } } })
  const path = hardwareTrend([frame(70, 1), frame(70.25, 1), frame(72, 5)],
    { session_id: 'a' }, 'activity', 'sm_active', 100)
  assert.equal((path.match(/M/g) ?? []).length, 2)
  assert.equal((path.match(/L/g) ?? []).length, 1)
})
