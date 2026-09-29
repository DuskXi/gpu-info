import assert from 'node:assert/strict'
import { test } from 'node:test'
import { TelemetryClient } from '../src/composables/useMonitorData.js'

const frame = (stream_id, sequence, timestamp) =>
  ({ type: 'snapshot', stream_id, sequence, timestamp, gpus: [] })

test('history and live frames share sequence deduplication', () => {
  const client = new TelemetryClient()
  client.ingest(frame('first', 2, 20))
  client.ingest(frame('first', 1, 10))
  client.ingest(frame('first', 2, 20))

  assert.deepEqual(client.history.value.map(item => item.sequence), [1, 2])
  assert.equal(client.current.value.sequence, 2)
})

test('a server restart replaces old stream history', () => {
  const client = new TelemetryClient()
  client.ingest(frame('first', 120, 100))
  client.ingest(frame('second', 1, 101))
  client.ingest(frame('first', 121, 99))

  assert.equal(client.current.value.stream_id, 'second')
  assert.deepEqual(client.history.value.map(item => item.sequence), [1])
})

test('fast hardware data rejects old frames, deduplicates history, and resets sessions', () => {
  const client = new TelemetryClient()
  client.ingestHardware({ session_id: 'a', sequence: 3, checked_at: 30 })
  client.ingestHardware({ session_id: 'a', sequence: 2, checked_at: 29 })
  client.ingestHardware({ session_id: 'a', sequence: 3, checked_at: 31, bandwidth_sampling: { enabled: true } })
  assert.equal(client.hardwareHistory.value.length, 1)
  assert.equal(client.hardware.value.bandwidth_sampling.enabled, true)
  client.ingestHardware({ session_id: 'b', sequence: 1, checked_at: 32 })
  assert.equal(client.hardwareHistory.value.length, 1)
  assert.equal(client.hardware.value.session_id, 'b')
})

test('numerical display is paced at 1 Hz while history and stop state stay current', () => {
  const client = new TelemetryClient()
  client.ingestHardware({ session_id: 'a', enabled: true, state: 'running', sequence: 1, checked_at: 30 })
  const firstAt = client.hardwareDisplayAt
  client.ingestHardware({ session_id: 'a', enabled: true, state: 'running', sequence: 2, checked_at: 30.25 })
  assert.equal(client.hardwareDisplay.value.sequence, 1)
  assert.equal(client.hardwareHistory.value.length, 2)
  client.publishHardwareDisplay(firstAt + 1000)
  assert.equal(client.hardwareDisplay.value.sequence, 2)
  client.ingestHardware({ session_id: 'a', enabled: false, state: 'off', sequence: 2, checked_at: 30.5 })
  assert.equal(client.hardwareDisplay.value.enabled, false)
})
