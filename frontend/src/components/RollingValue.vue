<script setup>
import { onMounted, onUnmounted, ref, shallowRef, watch } from 'vue'
import { advanceDigit, directionFor, magnitude, targetForDigit, tokensFor } from '../rollingDigits'

const props = defineProps({
  value: { type: [String, Number], default: '—' },
  amount: { type: Number, default: null },
})

const root = ref(null)
const tokens = shallowRef([])
const tick = ref(0)
const states = new Map()
let frame = 0
let lastFrame = 0
let visible = true
let observer
let motionQuery
let previousValue
let previousAmount

function settle() {
  cancelAnimationFrame(frame)
  frame = 0
  lastFrame = 0
  for (const state of states.values()) {
    state.position = state.target
    state.velocity = 0
  }
  tick.value++
}

function animate(now) {
  const seconds = lastFrame ? (now - lastFrame) / 1000 : 1 / 60
  lastFrame = now
  let active = false
  for (const state of states.values()) active = advanceDigit(state, seconds) || active
  tick.value++
  frame = active ? requestAnimationFrame(animate) : 0
  if (!active) lastFrame = 0
}

function reconcile() {
  const value = String(props.value ?? '—')
  const amount = Number.isFinite(props.amount) ? props.amount : magnitude(value)
  if (value === previousValue) {
    previousAmount = amount
    return
  }
  const first = previousValue === undefined
  const direction = directionFor(previousValue, value, previousAmount, amount)
  const next = tokensFor(value)
  const keys = new Set()
  for (const token of next) {
    if (token.digit === undefined) continue
    keys.add(token.key)
    const state = states.get(token.key)
    if (!state) {
      states.set(token.key, { position: token.digit, target: token.digit, velocity: 0, digit: token.digit })
    } else if (state.digit !== token.digit) {
      state.target = targetForDigit(state.position, token.digit, direction)
      state.digit = token.digit
    }
  }
  for (const key of states.keys()) if (!keys.has(key)) states.delete(key)
  tokens.value = next
  previousValue = value
  previousAmount = amount
  if (first || !visible || motionQuery?.matches || document.hidden) settle()
  else if (!frame) frame = requestAnimationFrame(animate)
}

function glyph(token) {
  tick.value
  const position = states.get(token.key)?.position ?? token.digit
  const base = Math.floor(position)
  const wrap = number => ((number % 10) + 10) % 10
  return { top: wrap(base), bottom: wrap(base + 1), offset: position - base }
}

watch(() => [props.value, props.amount], reconcile, { immediate: true })
onMounted(() => {
  motionQuery = window.matchMedia('(prefers-reduced-motion: reduce)')
  motionQuery.addEventListener('change', settle)
  if ('IntersectionObserver' in window) {
    observer = new IntersectionObserver(([entry]) => {
      visible = entry.isIntersecting
      if (!visible) settle()
    }, { rootMargin: '80px' })
    observer.observe(root.value)
  }
  document.addEventListener('visibilitychange', settle)
})
onUnmounted(() => {
  cancelAnimationFrame(frame)
  observer?.disconnect()
  motionQuery?.removeEventListener('change', settle)
  document.removeEventListener('visibilitychange', settle)
})
</script>

<template>
  <span ref="root" class="rolling-value">
    <span class="rolling-visual" aria-hidden="true">
      <template v-for="(token, index) in tokens" :key="index">
        <span v-if="token.digit !== undefined" class="rolling-digit">
          <span class="rolling-track" :style="{ transform: `translateY(-${glyph(token).offset}em)` }">
            <span>{{ glyph(token).top }}</span><span>{{ glyph(token).bottom }}</span>
          </span>
        </span>
        <span v-else>{{ token.char }}</span>
      </template>
    </span>
    <span class="sr-only">{{ value }}</span>
  </span>
</template>
