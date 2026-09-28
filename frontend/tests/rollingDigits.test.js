import assert from 'node:assert/strict'
import test from 'node:test'
import { advanceDigit, directionFor, magnitude, targetForDigit, tokensFor } from '../src/rollingDigits.js'

test('digits retain their place across punctuation and unit changes', () => {
  assert.deepEqual(tokensFor('14.8 GB').filter(token => token.digit !== undefined), [
    { digit: 1, key: 2 }, { digit: 4, key: 1 }, { digit: 8, key: 0 },
  ])
  assert.equal(magnitude('1024 MB'), magnitude('1 GB'))
  assert.equal(directionFor('GPU 0 · 165.0 Hz', 'GPU 0 · 144.0 Hz', 0, 0), -1)
})

test('a reversed update brakes before the wheel turns around', () => {
  const state = { position: 0, target: 9, velocity: 0 }
  for (let i = 0; i < 10; i++) advanceDigit(state, 1 / 60)
  const reversalPosition = state.position
  assert.ok(state.velocity > 0)

  state.target = targetForDigit(state.position, 0, -1)
  advanceDigit(state, 1 / 60)
  assert.ok(state.position > reversalPosition, 'the wheel keeps moving while it slows')
  assert.ok(state.velocity > 0, 'velocity is retained instead of reversing instantly')

  for (let i = 0; i < 100; i++) advanceDigit(state, 1 / 60)
  assert.equal(state.position, state.target)
  assert.equal(state.velocity, 0)
  assert.equal(state.target, 0)
})
