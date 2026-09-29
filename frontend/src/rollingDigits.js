const DIGIT = /[0-9]/
export const DIGIT_TRANSITION_SECONDS = 1.15
export const DIGIT_BRAKE_SECONDS = 0.34

export function magnitude(text) {
  const value = String(text ?? '')
  const match = value.match(/-?[\d,]+(?:\.\d+)?/)
  if (!match) return null
  const number = Number(match[0].replaceAll(',', ''))
  if (!Number.isFinite(number)) return null
  const unit = value.slice(match.index + match[0].length).match(/^\s*(TB|GB|MB|KB|B)(?:\/s)?\b/i)?.[1]?.toUpperCase()
  const scale = { TB: 1024 ** 4, GB: 1024 ** 3, MB: 1024 ** 2, KB: 1024, B: 1 }
  return number * (scale[unit] ?? 1)
}

export function tokensFor(text) {
  let place = 0
  return [...String(text ?? '')].reverse().map(char =>
    DIGIT.test(char) ? { digit: Number(char), key: place++ } : { char }
  ).reverse()
}

export function directionFor(previousText, nextText, previousAmount, nextAmount) {
  if (Number.isFinite(previousAmount) && Number.isFinite(nextAmount) && nextAmount !== previousAmount) {
    return Math.sign(nextAmount - previousAmount)
  }
  const parts = text => [...String(text).matchAll(/-?[\d,]+(?:\.\d+)?/g)]
    .map(match => Number(match[0].replaceAll(',', '')))
  const before = parts(previousText)
  const after = parts(nextText)
  for (let index = 0; index < Math.min(before.length, after.length); index++) {
    if (before[index] !== after[index]) return Math.sign(after[index] - before[index])
  }
  return 1
}

export function targetForDigit(position, digit, direction) {
  const nearest = Math.round((position - digit) / 10) * 10 + digit
  if (direction > 0 && nearest < position) return nearest + 10
  if (direction < 0 && nearest > position) return nearest - 10
  return nearest
}

export function advanceDigit(state, seconds) {
  let dt = Math.min(Math.max(seconds, 0), 0.05)
  if (state.position === state.target && state.velocity === 0) {
    state.motion = null
    state.brakeRemaining = 0
    return false
  }
  if (!state.motion || state.motion.target !== state.target) {
    // Retarget from the current position AND velocity. The 1.15 s transition
    // intentionally overlaps the next 1 Hz reading; do not restart from rest.
    const reversing = state.velocity * (state.target - state.position) < 0
    state.brakeRemaining = reversing && Math.abs(state.velocity) > 0.02 ? DIGIT_BRAKE_SECONDS : 0
    state.motion = { target: state.target, start: state.position, initialVelocity: state.velocity,
      elapsed: 0, duration: DIGIT_TRANSITION_SECONDS - state.brakeRemaining }
  }
  if (state.brakeRemaining > 0) {
    const used = Math.min(dt, state.brakeRemaining)
    const velocity = state.velocity * (1 - used / state.brakeRemaining)
    state.position += (state.velocity + velocity) / 2 * used
    state.velocity = velocity
    state.brakeRemaining = Math.max(0, state.brakeRemaining - used)
    dt -= used
    if (state.brakeRemaining > 0) return true
    state.motion.start = state.position
    state.motion.initialVelocity = 0
  }
  const motion = state.motion
  motion.elapsed = Math.min(motion.duration, motion.elapsed + dt)
  const t = motion.elapsed / motion.duration
  const distance = motion.target - motion.start
  const tangent = motion.initialVelocity * motion.duration
  // Cubic Hermite curve: preserve incoming velocity and settle at zero velocity.
  state.position = motion.start + distance * (3*t*t - 2*t*t*t) + tangent * (t*t*t - 2*t*t + t)
  state.velocity = (distance * (6*t - 6*t*t) + tangent * (3*t*t - 4*t + 1)) / motion.duration
  if (t >= 1) {
    state.position = state.target
    state.velocity = 0
    state.motion = null
  }
  return state.velocity !== 0 || state.position !== state.target
}
