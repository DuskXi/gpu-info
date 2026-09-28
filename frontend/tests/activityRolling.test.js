import test from 'node:test'
import assert from 'node:assert/strict'
import { rollActivity } from '../src/activityRolling.js'

function capture(gpu, kind, end, samples, mean, peak = mean) {
  return {
    gpu_id: gpu, kind, captured_at: end + 4,
    window_started_at: end - 2, window_ended_at: end,
    duration_seconds: 2,
    metrics: { tensor_active: {
      mean_percent: mean, p90_percent: peak, peak_percent: peak,
      active_samples: samples, samples,
    } },
  }
}

test('rolling mean weights actual samples while latest P90 and peak stay local', () => {
  const first = rollActivity([], capture(0, 'pipelines', 10, 10, 20, 30))
  const second = rollActivity(first.history, capture(0, 'pipelines', 30, 20, 50, 70))
  assert.equal(second.display.metrics.tensor_active.mean_percent, 40)
  assert.equal(second.display.metrics.tensor_active.p90_percent, 70)
  assert.equal(second.display.metrics.tensor_active.peak_percent, 70)
  assert.equal(second.display.rolling.windows, 2)
  assert.equal(second.display.rolling.sampled_seconds, 4)
  assert.equal(second.display.rolling.span_seconds, 22)
})

test('rolling window retains only three recent captures for one GPU and mode', () => {
  let state = rollActivity([], capture(0, 'general', 10, 10, 10))
  state = rollActivity(state.history, capture(1, 'general', 20, 10, 100))
  assert.equal(state.display.rolling.windows, 1)
  state = rollActivity([], capture(0, 'general', 10, 10, 10))
  state = rollActivity(state.history, capture(0, 'general', 30, 10, 20))
  state = rollActivity(state.history, capture(0, 'general', 50, 10, 30))
  state = rollActivity(state.history, capture(0, 'general', 70, 10, 40))
  assert.equal(state.display.metrics.tensor_active.mean_percent, 30)
  assert.equal(state.display.rolling.windows, 3)
  state = rollActivity(state.history, capture(0, 'general', 200, 10, 50))
  assert.equal(state.display.rolling.windows, 1)
})
