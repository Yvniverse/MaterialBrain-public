import {
  angleDelta,
  distance,
  robotRadius,
  obstaclesFor,
  pointClearance,
  segmentClearance,
  segmentSpeed,
  fingerprint,
  validateWorld,
} from './geometry.mjs'
const DIRS = [
  [1, 0],
  [1, 1],
  [0, 1],
  [-1, 1],
  [-1, 0],
  [-1, -1],
  [0, -1],
  [1, -1],
]
const stepAngle = Math.PI / 4
const heading = (y) => ((Math.round(y / stepAngle) % 8) + 8) % 8
const pose = (x, y, yaw = 0) => ({ x, y, yaw })
class Heap {
  constructor() {
    this.a = []
  }
  less(a, b) {
    return a[0] < b[0] || (a[0] === b[0] && (a[1] < b[1] || (a[1] === b[1] && a[2] < b[2])))
  }
  push(v) {
    const a = this.a
    let i = a.length
    a.push(v)
    while (i) {
      const p = (i - 1) >> 1
      if (!this.less(v, a[p])) break
      a[i] = a[p]
      i = p
    }
    a[i] = v
  }
  pop() {
    const a = this.a,
      r = a[0],
      v = a.pop()
    if (a.length) {
      let i = 0
      while (2 * i + 1 < a.length) {
        let j = 2 * i + 1
        if (j + 1 < a.length && this.less(a[j + 1], a[j])) j++
        if (!this.less(a[j], v)) break
        a[i] = a[j]
        i = j
      }
      a[i] = v
    }
    return r
  }
}
export class MetricPlanner {
  constructor(world, { profile = world.robot, obstacles = [], clearance_weight = 0.12 } = {}) {
    const errors = validateWorld(world)
    if (errors.length) throw new Error(errors.join('; '))
    if (
      ![profile.length, profile.width, profile.max_speed, profile.angular_speed].every(
        (x) => Number.isFinite(x) && x > 0,
      ) ||
      !Number.isFinite(profile.margin) ||
      profile.margin < 0
    )
      throw new Error('invalid robot profile')
    if (!Number.isFinite(clearance_weight) || clearance_weight < 0)
      throw new Error('INVALID_CLEARANCE_WEIGHT')
    this.world = world
    this.profile = { ...profile }
    this.extra = obstacles
    this.obstacles = obstaclesFor(world, obstacles)
    this.radius = robotRadius(profile)
    this.r = this.radius + profile.margin
    this.res = world.resolution
    this.weight = clearance_weight
    this.nx = Math.floor(world.width / this.res) + 1
    this.ny = Math.floor(world.height / this.res) + 1
    if (this.nx * this.ny > 250000) throw new Error('GRID_TOO_LARGE')
    this.n = this.nx * this.ny
    this.clear = new Float64Array(this.n)
    this.mask = new Uint8Array(this.n)
    this.edges = new Map()
    this.cache = new Map()
    this.revision =
      world.revision_sha256 + ':' + fingerprint({ obstacles, profile, clearance_weight })
    for (let i = 0; i < this.n; i++)
      this.clear[i] = pointClearance(world, this.point(i), this.obstacles)
    for (let i = 0; i < this.n; i++) {
      if (this.clear[i] <= this.r + 1e-9) continue
      const a = this.point(i),
        ix = i % this.nx,
        iy = Math.floor(i / this.nx)
      for (let h = 0; h < 8; h++) {
        const [dx, dy] = DIRS[h],
          x = ix + dx,
          y = iy + dy
        if (x < 0 || y < 0 || x >= this.nx || y >= this.ny) continue
        const j = y * this.nx + x
        if (this.clear[j] <= this.r + 1e-9) continue
        if (dx && dy && (this.clear[i + dx] <= this.r || this.clear[i + dy * this.nx] <= this.r))
          continue
        const b = this.point(j),
          d = distance(a, b)
        let c = Math.min(this.clear[i], this.clear[j]) - d
        if (c <= this.r) c = segmentClearance(world, a, b, this.obstacles)
        if (c <= this.r + 1e-9) continue
        this.mask[i] |= 1 << h
      }
    }
  }
  point(i, h = 0) {
    return pose((i % this.nx) * this.res, Math.floor(i / this.nx) * this.res, h * stepAngle)
  }
  connection(p) {
    if (![p.x, p.y, p.yaw || 0].every(Number.isFinite)) throw new Error('INVALID_POSE')
    if (pointClearance(this.world, p, this.obstacles) <= this.r + 1e-9)
      throw new Error('POSE_BLOCKED')
    const cx = Math.round(p.x / this.res),
      cy = Math.round(p.y / this.res),
      list = []
    for (let dx = -1; dx <= 1; dx++)
      for (let dy = -1; dy <= 1; dy++) {
        const x = cx + dx,
          y = cy + dy
        if (x < 0 || y < 0 || x >= this.nx || y >= this.ny) continue
        const i = y * this.nx + x,
          q = this.point(i)
        if (
          this.clear[i] > this.r &&
          segmentClearance(this.world, p, q, this.obstacles) > this.r + 1e-9
        )
          list.push([distance(p, q), i])
      }
    list.sort((a, b) => a[0] - b[0] || a[1] - b[1])
    if (!list.length) throw new Error('POSE_UNCONNECTED')
    return list[0][1]
  }
  edge(i, h) {
    const key = i * 8 + h
    if (this.edges.has(key)) return this.edges.get(key)
    const [dx, dy] = DIRS[h],
      j = i + dy * this.nx + dx,
      a = this.point(i),
      b = this.point(j),
      d = distance(a, b)
    const c = segmentClearance(this.world, a, b, this.obstacles),
      t = d / segmentSpeed(this.world, a, b, this.profile)
    const e = {
      j,
      d,
      time: t,
      cost: t + this.weight * d * Math.exp(-Math.max(0, c - this.r) / 0.65),
    }
    this.edges.set(key, e)
    return e
  }
  path(start, goal, { max_expansions = 500000, heuristic_enabled = true } = {}) {
    const key = JSON.stringify([start, goal, heuristic_enabled])
    if (this.cache.has(key)) return this.cache.get(key)
    const si = this.connection(start),
      gi = this.connection(goal),
      sh = heading(start.yaw || 0),
      gh = heading(goal.yaw || 0)
    const s = si * 8 + sh,
      target = gi * 8 + gh,
      dist = new Float64Array(this.n * 8)
    dist.fill(Infinity)
    dist[s] = 0
    const parent = new Int32Array(this.n * 8)
    parent.fill(-1)
    const heap = new Heap()
    const heuristic = (i) =>
      heuristic_enabled ? distance(this.point(i), this.point(gi)) / this.profile.max_speed : 0
    heap.push([heuristic(si), 0, s])
    let expanded = 0
    while (heap.a.length) {
      const [, g, state] = heap.pop()
      if (g > dist[state] + 1e-9) continue
      if (state === target) break
      if (++expanded > max_expansions) throw new Error('SEARCH_BUDGET_EXCEEDED')
      const i = state >> 3,
        h = state & 7
      const successors = [
        [i * 8 + ((h + 7) % 8), stepAngle / this.profile.angular_speed],
        [i * 8 + ((h + 1) % 8), stepAngle / this.profile.angular_speed],
      ]
      if (this.mask[i] & (1 << h)) {
        const e = this.edge(i, h)
        successors.push([e.j * 8 + h, e.cost])
      }
      for (const [n, c] of successors) {
        const ng = g + c
        if (ng + 1e-9 < dist[n]) {
          dist[n] = ng
          parent[n] = state
          heap.push([ng + heuristic(n >> 3), ng, n])
        }
      }
    }
    if (!Number.isFinite(dist[target])) throw new Error('NO_PATH')
    const states = []
    for (let s = target; s !== -1; s = parent[s]) states.push(s)
    states.reverse()
    let points = states.map((s) => this.point(s >> 3, s & 7))
    // Preserve exact endpoints; never silently move a requested docking pose.
    if (distance(start, points[0]) > 1e-8)
      points = this.connector(start, points[0]).concat(points.slice(1))
    else if (Math.abs(angleDelta(start.yaw || 0, points[0].yaw)) > 1e-8)
      points.unshift({ ...start, yaw: start.yaw || 0 })
    if (distance(goal, points.at(-1)) > 1e-8)
      points.push(...this.connector(points.at(-1), goal).slice(1))
    else if (Math.abs(angleDelta(points.at(-1).yaw, goal.yaw || 0)) > 1e-8)
      points.push({ ...goal, yaw: goal.yaw || 0 })
    let metres = 0,
      time = 0,
      cost = 0,
      minClearance = Infinity
    const actions = []
    for (let i = 1; i < points.length; i++) {
      const a = points[i - 1],
        b = points[i],
        d = distance(a, b),
        c = segmentClearance(this.world, a, b, this.obstacles)
      if (c <= this.r + 1e-9) throw new Error('COLLISION_IN_PATH')
      minClearance = Math.min(minClearance, c - this.radius)
      const t =
        d > 1e-9
          ? d / segmentSpeed(this.world, a, b, this.profile)
          : Math.abs(angleDelta(a.yaw, b.yaw)) / this.profile.angular_speed
      const penalty = d > 1e-9 ? this.weight * d * Math.exp(-Math.max(0, c - this.r) / 0.65) : 0
      metres += d
      time += t
      cost += t + penalty
      actions.push({
        type: d > 1e-9 ? 'drive' : 'rotate',
        from: a,
        to: b,
        distance_m: d,
        duration_s: t,
      })
    }
    const result = {
      poses: points,
      actions,
      distance_m: metres,
      motion_s: time,
      cost_s: cost,
      min_clearance_m: Number.isFinite(minClearance)
        ? minClearance
        : pointClearance(this.world, start, this.obstacles) - this.radius,
      expanded,
      revision: this.revision,
    }
    if (this.cache.size > 4096) this.cache.clear()
    this.cache.set(key, result)
    return result
  }
  connector(a, b) {
    const yaw = Math.atan2(b.y - a.y, b.x - a.x),
      pts = [{ ...a, yaw: a.yaw || 0 }]
    if (Math.abs(angleDelta(a.yaw || 0, yaw)) > 1e-8) pts.push({ ...a, yaw })
    pts.push({ ...b, yaw })
    if (Math.abs(angleDelta(yaw, b.yaw || 0)) > 1e-8) pts.push({ ...b, yaw: b.yaw || 0 })
    return pts
  }
  plan(
    goalIds,
    {
      start = this.world.home,
      end = this.world.home,
      optimize = true,
      battery_pct = this.profile.battery_pct,
      payload_kg = 0,
    } = {},
  ) {
    const now = performance.now(),
      ids = [...new Set(goalIds)]
    if (ids.length > 14) throw new Error('最多 14 个停靠点，请拆分批次')
    const goals = ids.map((id) => {
      const g = this.world.goals.find((x) => x.id === id)
      if (!g) throw new Error('UNKNOWN_GOAL:' + id)
      return g
    })
    const unsupported = goals.filter((g) =>
      (g.require_capabilities || []).some((c) => !this.profile.capabilities.includes(c)),
    )
    if (unsupported.length)
      return {
        status: 'CAPABILITY_MISMATCH',
        goals: unsupported.map((g) => g.id),
        revision: this.revision,
      }
    if (
      !Number.isFinite(battery_pct) ||
      battery_pct < 0 ||
      battery_pct > 100 ||
      !Number.isFinite(payload_kg) ||
      payload_kg < 0
    )
      throw new Error('INVALID_RESOURCE_STATE')
    const payload = payload_kg + goals.reduce((s, g) => s + g.payload_kg, 0)
    if (payload > this.profile.payload_kg)
      return {
        status: 'SPLIT_REQUIRED',
        payload_kg: payload,
        capacity_kg: this.profile.payload_kg,
        batches: splitBatches(goals, this.profile.payload_kg - payload_kg),
        revision: this.revision,
      }
    const nodes = [{ id: '__start', pose: start }, ...goals, { id: '__end', pose: end }]
    const n = goals.length,
      paths = Array.from({ length: n + 2 }, () => Array(n + 2))
    try {
      for (let i = 0; i <= n; i++)
        for (let j = 1; j < n + 2; j++)
          if (i !== j) paths[i][j] = this.path(nodes[i].pose, nodes[j].pose)
    } catch (e) {
      return { status: 'BLOCKED', reason: e.message, revision: this.revision, goal_ids: ids }
    }
    const cost = (order) => {
      let c = 0,
        last = 0
      for (const j of order) {
        c += paths[last][j].cost_s
        last = j
      }
      return c + (paths[last][n + 1]?.cost_s || 0)
    }
    const baseline = Array.from({ length: n }, (_, i) => i + 1)
    let order = [...baseline],
      method = 'input_order'
    if (optimize && n > 1) {
      if (n <= 8) {
        order = heldKarp(n, paths)
        method = 'held_karp_exact'
      } else {
        order = nearestTwoOpt(n, paths, cost)
        method = 'nearest_2opt'
      }
    }
    const segments = []
    let last = 0
    for (const i of [...order, n + 1]) {
      if (paths[last][i])
        segments.push({ from_id: nodes[last].id, to_id: nodes[i].id, ...paths[last][i] })
      last = i
    }
    const totals = segments.reduce(
      (v, s) => ({
        distance_m: v.distance_m + s.distance_m,
        motion_s: v.motion_s + s.motion_s,
        cost_s: v.cost_s + s.cost_s,
        min_clearance_m: Math.min(v.min_clearance_m, s.min_clearance_m),
        expanded: v.expanded + s.expanded,
      }),
      { distance_m: 0, motion_s: 0, cost_s: 0, min_clearance_m: Infinity, expanded: 0 },
    )
    const service_s = goals.reduce((v, g) => v + g.service_s, 0),
      eta_s = totals.motion_s + service_s
    const energy_wh =
      totals.distance_m * this.profile.wh_per_m + (this.profile.idle_w * eta_s) / 3600
    const battery_after_pct = battery_pct - (100 * energy_wh) / this.profile.battery_wh
    const status = battery_after_pct < this.profile.reserve_pct ? 'NEEDS_CHARGE' : 'READY'
    const baseCost = cost(baseline),
      baseDistance = sequenceDistance(baseline, paths, n)
    return {
      status,
      revision: this.revision,
      world_id: this.world.id,
      planner: 'heading_grid_astar',
      footprint_model: 'circumscribed_disc',
      order_method: method,
      goal_ids: order.map((i) => nodes[i].id),
      requested_goal_ids: ids,
      segments,
      ...totals,
      service_s,
      eta_s,
      energy_wh,
      battery_after_pct,
      payload_kg: payload,
      baseline: { cost_s: baseCost, distance_m: baseDistance },
      improvement_pct: baseCost > 0 ? ((baseCost - totals.cost_s) / baseCost) * 100 : 0,
      cpu_ms: performance.now() - now,
      calibration_status: this.world.provenance,
      reason: status === 'NEEDS_CHARGE' ? '低于返航电量阈值，先充电再执行' : null,
    }
  }
}
function sequenceDistance(order, paths, n) {
  let s = 0,
    p = 0
  for (const j of [...order, n + 1]) {
    s += paths[p][j]?.distance_m || 0
    p = j
  }
  return s
}
function heldKarp(n, paths) {
  const dp = new Map()
  for (let j = 1; j <= n; j++) dp.set((1 << (j - 1)) * n + j - 1, [paths[0][j].cost_s, [j]])
  for (let m = 1; m < 1 << n; m++)
    for (let j = 1; j <= n; j++) {
      const state = dp.get(m * n + j - 1)
      if (!state) continue
      for (let k = 1; k <= n; k++)
        if (!(m & (1 << (k - 1)))) {
          const key = (m | (1 << (k - 1))) * n + k - 1,
            c = state[0] + paths[j][k].cost_s,
            old = dp.get(key)
          if (!old || c < old[0] - 1e-9) dp.set(key, [c, [...state[1], k]])
        }
    }
  let best = null
  for (let j = 1; j <= n; j++) {
    const s = dp.get(((1 << n) - 1) * n + j - 1),
      c = s[0] + paths[j][n + 1].cost_s
    if (!best || c < best[0] - 1e-9) best = [c, s[1]]
  }
  return best[1]
}
function nearestTwoOpt(n, paths, cost) {
  const left = new Set(Array.from({ length: n }, (_, i) => i + 1)),
    order = []
  let p = 0
  while (left.size) {
    const j = [...left].sort((a, b) => paths[p][a].cost_s - paths[p][b].cost_s || a - b)[0]
    left.delete(j)
    order.push(j)
    p = j
  }
  let out = order,
    best = cost(out),
    changed = true,
    rounds = 0
  while (changed && rounds++ < 20) {
    changed = false
    for (let i = 0; i < n - 1; i++)
      for (let j = i + 1; j < n; j++) {
        const cand = [...out.slice(0, i), ...out.slice(i, j + 1).reverse(), ...out.slice(j + 1)],
          c = cost(cand)
        if (c < best - 1e-9) {
          out = cand
          best = c
          changed = true
        }
      }
  }
  return out
}
export function splitBatches(goals, capacity) {
  if (capacity <= 0 || goals.some((g) => g.payload_kg > capacity)) return null
  const batches = []
  for (const g of [...goals].sort(
    (a, b) => b.payload_kg - a.payload_kg || a.id.localeCompare(b.id),
  )) {
    let b = batches.find((b) => b.weight + g.payload_kg <= capacity)
    if (!b) {
      b = { ids: [], weight: 0 }
      batches.push(b)
    }
    b.ids.push(g.id)
    b.weight += g.payload_kg
  }
  return batches
}
