export function bytes(value, digits = 1) {
  if (value == null || !Number.isFinite(value)) return '—'
  if (value > 0 && value < 1048576) return '<1 MB'
  const gb = value / 1073741824
  return gb >= 1 ? `${gb.toFixed(digits)} GB` : `${(value / 1048576).toFixed(0)} MB`
}

export function number(value, digits = 0, unit = '') {
  return value == null || !Number.isFinite(value) ? '—' : `${Number(value).toFixed(digits)}${unit}`
}

export function percent(value) {
  return number(value, 0, '%')
}

export function throughput(value) {
  if (value == null || !Number.isFinite(value)) return '—'
  if (value < 1024) return `${Math.round(value)} B/s`
  if (value < 1048576) return `${(value / 1024).toFixed(1)} KB/s`
  if (value < 1073741824) return `${(value / 1048576).toFixed(1)} MB/s`
  return `${(value / 1073741824).toFixed(1)} GB/s`
}

export function time(timestamp) {
  return timestamp ? new Date(timestamp * 1000).toLocaleTimeString() : '—'
}
