import { computed, onMounted, onUnmounted, ref, shallowRef } from 'vue'

// REST and WebSocket use the same snapshot envelope and enter through ingest().
export class TelemetryClient {
  constructor() {
    this.current = shallowRef(null)
    this.history = shallowRef([])
    this.hardware = shallowRef(null)
    this.hardwareDisplay = shallowRef(null)
    this.hardwareDisplayAt = 0
    this.hardwareHistory = shallowRef([])
    this.hardwareTimer = null
    this.hardwareAbort = null
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
      if (message.hardware_sampling) this.ingestHardware({ ...message.hardware_sampling,
        bandwidth_sampling: message.bandwidth_sampling })
    }
  }

  ingestHardware(state) {
    if (!state?.checked_at || state.checked_at <= (this.hardware.value?.checked_at ?? 0)) return
    const previous = this.hardware.value
    if (previous?.session_id !== state.session_id) this.hardwareHistory.value = []
    this.hardware.value = state
    if (state.sequence && (previous?.sequence !== state.sequence || previous?.session_id !== state.session_id)) {
      this.hardwareHistory.value = [...this.hardwareHistory.value, { hardware_sampling: state }].slice(-300)
    }
    this.publishHardwareDisplay()
  }

  publishHardwareDisplay(now = Date.now()) {
    const next = this.hardware.value
    if (!next) return
    const before = this.hardwareDisplay.value
    // Controls and errors stay responsive; only numerical readings are paced at 1 Hz.
    const controlChanged = !before || ['session_id', 'enabled', 'state', 'error'].some(key => before[key] !== next[key])
      || before.bandwidth_sampling?.enabled !== next.bandwidth_sampling?.enabled
    if (!controlChanged && now - this.hardwareDisplayAt < 1000) return
    this.hardwareDisplay.value = next
    this.hardwareDisplayAt = now
  }

  async pollHardware() {
    if (!this.listeners || this.hardwareAbort) return
    const controller = new AbortController()
    this.hardwareAbort = controller
    const deadline = setTimeout(() => controller.abort(), 3000)
    try {
      const response = await fetch('/api/hardware-sampling', { signal: controller.signal, cache: 'no-store' })
      if (response.ok && this.listeners) this.ingestHardware(await response.json())
    } catch (error) {
      // Keep the last timestamp; the UI marks stale samples rather than replacing with zero.
    } finally {
      clearTimeout(deadline)
      this.hardwareAbort = null
      if (this.listeners) this.hardwareTimer = setTimeout(() => this.pollHardware(),
        this.hardware.value?.enabled || this.hardware.value?.bandwidth_sampling?.enabled ? 250 : 1000)
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
    this.pollHardware()
    this.clock = setInterval(() => {
      this.now.value = Date.now()
      this.publishHardwareDisplay(this.now.value)
    }, 250)
  }

  detach() {
    this.listeners = Math.max(0, this.listeners - 1)
    if (this.listeners) return
    clearTimeout(this.reconnect)
    clearInterval(this.clock)
    clearTimeout(this.hardwareTimer)
    this.hardwareAbort?.abort()
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
    hardwareSampling: client.hardwareDisplay,
    hardwareHistory: client.hardwareHistory,
    now: client.now,
    isConnected: client.connected,
    isStale: client.stale,
  }
}

export function procKey(pid, gpuId) {
  return `${gpuId}:${pid}`
}
