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
