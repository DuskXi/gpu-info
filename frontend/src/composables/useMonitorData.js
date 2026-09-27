import { ref, computed, onMounted, onUnmounted } from 'vue'

// Shared WebSocket state across pages
const system = ref(null)
const gpus = ref([])
const processes = ref([])
const processUtilization = ref([])
const cudaInfo = ref(null)
const isConnected = ref(false)
const lastUpdate = ref(null)
const now = ref(Date.now())

let ws = null
let refCount = 0
let reconnectTimer = null
let staleTimer = null

const isStale = computed(() => {
  if (!lastUpdate.value?.timestamp) return false
  return (now.value / 1000 - lastUpdate.value.timestamp) > 5
})

function clearReconnectTimer() {
  if (reconnectTimer !== null) {
    clearTimeout(reconnectTimer)
    reconnectTimer = null
  }
}

function scheduleReconnect() {
  clearReconnectTimer()
  if (refCount <= 0) return
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null
    if (refCount > 0) connect()
  }, 3000)
}

function connect() {
  if (ws && ws.readyState <= 1) return // already connected or connecting

  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const wsUrl = `${protocol}//${window.location.host}/api/ws`
  ws = new WebSocket(wsUrl)

  ws.onopen = () => { isConnected.value = true }

  ws.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data)
      system.value = data.system
      gpus.value = data.gpus
      processes.value = data.processes
      processUtilization.value = data.process_utilization || []
      cudaInfo.value = data.cuda || null
      lastUpdate.value = data
    } catch (e) {
      console.error('Failed to parse WebSocket message:', e)
    }
  }

  ws.onclose = () => {
    isConnected.value = false
    scheduleReconnect()
  }

  ws.onerror = () => { ws.close() }
}

function startStaleTimer() {
  if (staleTimer !== null) return
  staleTimer = setInterval(() => {
    now.value = Date.now()
  }, 1000)
}

function stopStaleTimer() {
  if (staleTimer !== null) {
    clearInterval(staleTimer)
    staleTimer = null
  }
}

export function useMonitorData() {
  onMounted(() => {
    refCount++
    startStaleTimer()
    connect()
  })

  onUnmounted(() => {
    refCount--
    if (refCount <= 0) {
      clearReconnectTimer()
      stopStaleTimer()
      if (ws) {
        ws.close()
        ws = null
      }
    }
  })

  return {
    system,
    gpus,
    processes,
    processUtilization,
    cudaInfo,
    isConnected,
    isStale,
    lastUpdate,
  }
}

export function procKey(pid, gpuId) {
  return `${pid}:${gpuId}`
}
