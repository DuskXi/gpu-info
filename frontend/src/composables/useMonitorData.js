import { computed, onMounted, onUnmounted, ref, shallowRef } from 'vue'

// REST and WebSocket use the same snapshot envelope and enter through ingest().
export class TelemetryClient {
  constructor() {
    this.current = shallowRef(null)
    this.history = shallowRef([])
    this.connected = ref(false)
    this.now = ref(Date.now())
    this.listeners = 0
    this.socket = null
    this.reconnect = null
    this.clock = null
    this.abort = null
    this.retry = 1000
    this.stale = computed(() => {
      const timestamp = this.current.value?.timestamp
      return timestamp ? this.now.value / 1000 - timestamp > 5 : false
    })
  }

  ingest(message) {
    if (message?.type !== 'snapshot' || !Number.isInteger(message.sequence) || !message.stream_id) return
    if (this.current.value && message.stream_id !== this.current.value.stream_id) {
      if (message.timestamp < this.current.value.timestamp) return
      this.history.value = []
      this.current.value = null
    }
    if (this.history.value.some(item => item.sequence === message.sequence)) return
    const frames = [...this.history.value, message]
      .sort((a, b) => a.sequence - b.sequence)
      .slice(-120)
    this.history.value = frames
    if (!this.current.value || message.sequence > this.current.value.sequence) {
      this.current.value = message
    }
  }

  async bootstrap() {
    this.abort = new AbortController()
    try {
      const response = await fetch('/api/history?limit=120', { signal: this.abort.signal })
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      const frames = await response.json()
      if (Array.isArray(frames)) frames.forEach(frame => this.ingest(frame))
    } catch (error) {
      if (error.name !== 'AbortError') console.warn('History unavailable', error)
    }
  }

  connect() {
    if (!this.listeners || this.socket?.readyState <= WebSocket.OPEN) return
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const socket = new WebSocket(`${protocol}//${location.host}/api/ws`)
    this.socket = socket
    socket.onopen = () => {
      if (this.socket !== socket) return
      this.connected.value = true
      this.retry = 1000
    }
    socket.onmessage = event => {
      try { this.ingest(JSON.parse(event.data)) }
      catch (error) { console.warn('Invalid telemetry frame', error) }
    }
    socket.onerror = () => socket.close()
    socket.onclose = () => {
      if (this.socket !== socket) return
      this.socket = null
      this.connected.value = false
      if (this.listeners) {
        this.reconnect = setTimeout(() => this.connect(), this.retry)
        this.retry = Math.min(this.retry * 2, 10000)
      }
    }
  }

  attach() {
    this.listeners += 1
    if (this.listeners !== 1) return
    this.bootstrap()
    this.connect()
    this.clock = setInterval(() => { this.now.value = Date.now() }, 1000)
  }

  detach() {
    this.listeners = Math.max(0, this.listeners - 1)
    if (this.listeners) return
    clearTimeout(this.reconnect)
    clearInterval(this.clock)
    this.abort?.abort()
    this.socket?.close()
    this.socket = null
    this.connected.value = false
  }
}

const client = new TelemetryClient()

export function useMonitorData() {
  onMounted(() => client.attach())
  onUnmounted(() => client.detach())
  return {
    snapshot: client.current,
    history: client.history,
    isConnected: client.connected,
    isStale: client.stale,
  }
}

export function procKey(pid, gpuId) {
  return `${gpuId}:${pid}`
}
