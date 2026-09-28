const DIGIT = /[0-9]/

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
  const dt = Math.min(Math.max(seconds, 0), 0.05)
  state.velocity += (state.target - state.position) * 100 * dt
  state.velocity *= Math.exp(-17 * dt)
  state.velocity = Math.max(-28, Math.min(28, state.velocity))
  state.position += state.velocity * dt
  if (Math.abs(state.target - state.position) < 0.012 && Math.abs(state.velocity) < 0.045) {
    state.position = state.target
    state.velocity = 0
  }
  return state.velocity !== 0 || state.position !== state.target
}
