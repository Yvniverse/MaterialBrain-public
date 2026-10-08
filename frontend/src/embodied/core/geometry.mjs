/** Shared metric geometry. Map: x east, y north; Three.js: (x, height, -y).
 * The robot uses its circumscribed disc (conservative for all yaw angles).
 * No visual smoothing is allowed to bypass segment collision checks.
 */
export const TAU = Math.PI * 2
export const clamp = (x, a, b) => Math.max(a, Math.min(b, x))
export const angleDelta = (a, b) => Math.atan2(Math.sin(b - a), Math.cos(b - a))
export const distance = (a, b) => Math.hypot(a.x - b.x, a.y - b.y)
export const robotRadius = (p) => Math.hypot(p.width, p.length) / 2
export function localPoint(p, r) {
  const a = ((r.yaw_deg || 0) * Math.PI) / 180,
    c = Math.cos(a),
    s = Math.sin(a),
    x = p.x - r.x,
    y = p.y - r.y
  return { x: x * c + y * s, y: -x * s + y * c }
}
export function pointRectDistance(p, r) {
  const q = localPoint(p, r)
  return Math.hypot(
    Math.max(Math.abs(q.x) - r.width / 2, 0),
    Math.max(Math.abs(q.y) - r.depth / 2, 0),
  )
}
function pointSegment(p, a, b) {
  const dx = b.x - a.x,
    dy = b.y - a.y,
    l = dx * dx + dy * dy
  const t = l ? clamp(((p.x - a.x) * dx + (p.y - a.y) * dy) / l, 0, 1) : 0
  return Math.hypot(p.x - a.x - t * dx, p.y - a.y - t * dy)
}
function hitRect(a, b, w, h) {
  let lo = 0,
    hi = 1
  const dx = b.x - a.x,
    dy = b.y - a.y
  for (const [p, q] of [
    [-dx, a.x + w],
    [dx, w - a.x],
    [-dy, a.y + h],
    [dy, h - a.y],
  ]) {
    if (Math.abs(p) < 1e-12) {
      if (q < 0) return false
      continue
    }
    const t = q / p
    if (p < 0) lo = Math.max(lo, t)
    else hi = Math.min(hi, t)
    if (lo > hi) return false
  }
  return true
}
export function segmentRectDistance(a, b, r) {
  a = localPoint(a, r)
  b = localPoint(b, r)
  const w = r.width / 2,
    h = r.depth / 2
  if (hitRect(a, b, w, h)) return 0
  const corners = [
    { x: -w, y: -h },
    { x: w, y: -h },
    { x: w, y: h },
    { x: -w, y: h },
  ]
  let best = Infinity
  for (let i = 0; i < 4; i++) {
    const c = corners[i],
      d = corners[(i + 1) % 4]
    best = Math.min(
      best,
      pointSegment(a, c, d),
      pointSegment(b, c, d),
      pointSegment(c, a, b),
      pointSegment(d, a, b),
    )
  }
  return best
}
export function obstaclesFor(world, extra = []) {
  return [
    ...world.assets.filter((a) => a.collidable !== false),
    ...world.zones.filter((z) => z.kind === 'keepout'),
    ...extra,
  ]
}
export function pointClearance(world, p, obstacles = obstaclesFor(world)) {
  return Math.min(
    p.x,
    p.y,
    world.width - p.x,
    world.height - p.y,
    ...obstacles.map((r) => pointRectDistance(p, r)),
  )
}
export function segmentClearance(world, a, b, obstacles = obstaclesFor(world)) {
  let c = Math.min(
    a.x,
    a.y,
    world.width - a.x,
    world.height - a.y,
    b.x,
    b.y,
    world.width - b.x,
    world.height - b.y,
  )
  for (const r of obstacles) c = Math.min(c, segmentRectDistance(a, b, r))
  return c
}
export function speedAt(world, p, profile) {
  let v = profile.max_speed
  for (const z of world.zones)
    if (z.kind === 'slow' && pointRectDistance(p, z) < 1e-10) v = Math.min(v, z.speed)
  return v
}
export function segmentSpeed(world, a, b, profile) {
  let v = profile.max_speed
  for (const z of world.zones)
    if (z.kind === 'slow' && segmentRectDistance(a, b, z) < 1e-10) v = Math.min(v, z.speed)
  return v
}
export function stableKey(value) {
  if (value === null || typeof value !== 'object') return JSON.stringify(value)
  if (Array.isArray(value)) return '[' + value.map(stableKey).join(',') + ']'
  return (
    '{' +
    Object.keys(value)
      .sort()
      .map((k) => JSON.stringify(k) + ':' + stableKey(value[k]))
      .join(',') +
    '}'
  )
}
export function fingerprint(value) {
  // Cache/replay identifier, not an authentication signature. Server uses SHA-256.
  let h = 2166136261
  for (const c of stableKey(value)) {
    h ^= c.charCodeAt(0)
    h = Math.imul(h, 16777619)
  }
  return (h >>> 0).toString(16).padStart(8, '0')
}
export function validateWorld(world) {
  const errors = [],
    ids = new Set()
  for (const k of ['width', 'height', 'resolution'])
    if (!Number.isFinite(world[k]) || world[k] <= 0) errors.push('invalid ' + k)
  for (const a of world.assets || []) {
    if (ids.has(a.id)) errors.push('duplicate ' + a.id)
    ids.add(a.id)
    if (
      ![a.x, a.y, a.width, a.depth, a.height, a.yaw_deg || 0].every(Number.isFinite) ||
      a.width <= 0 ||
      a.depth <= 0 ||
      a.height <= 0
    )
      errors.push('invalid geometry ' + a.id)
    const c = Math.cos(((a.yaw_deg || 0) * Math.PI) / 180),
      s = Math.sin(((a.yaw_deg || 0) * Math.PI) / 180)
    const ex = (Math.abs(c) * a.width + Math.abs(s) * a.depth) / 2,
      ey = (Math.abs(s) * a.width + Math.abs(c) * a.depth) / 2
    if (a.x - ex < 0 || a.y - ey < 0 || a.x + ex > world.width || a.y + ey > world.height)
      errors.push('outside ' + a.id)
  }
  for (const g of world.goals || [])
    if (!ids.has(g.asset_id)) errors.push('unknown goal asset ' + g.id)
  return errors
}
