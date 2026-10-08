import { test } from 'vitest'
import assert from 'node:assert/strict'
import world from '../src/embodied/scene/world.v3.json'
import { MetricPlanner, splitBatches } from '../src/embodied/core/planner.mjs'
import { MissionRunner } from '../src/embodied/core/mission.mjs'
import {
  validateWorld,
  robotRadius,
  pointRectDistance,
  segmentRectDistance,
  segmentClearance,
  pointClearance,
  angleDelta,
  segmentSpeed,
} from '../src/embodied/core/geometry.mjs'
const planner = new MetricPlanner(world),
  goals = world.default_goal_ids,
  base = planner.plan(goals)
const scenario = (id) => world.scenarios.find((s) => s.id === id)
const blocked = new MetricPlanner(world, { obstacles: scenario('blocked-crossing').obstacles })
const closed = blocked.plan(goals)
const isolated = new MetricPlanner(world, { obstacles: scenario('isolated-dock').obstacles }).plan(
  goals,
)
const low = planner.plan(goals, { battery_pct: 12 })
const approx = (a, b, eps = 1e-7) => assert.ok(Math.abs(a - b) < eps, `${a} != ${b}`)
const record = (p) => ({
  status: p.status,
  goal_ids: p.goal_ids,
  distance_m: p.distance_m,
  cost_s: p.cost_s,
  motion_s: p.motion_s,
  eta_s: p.eta_s,
  min_clearance_m: p.min_clearance_m,
  baseline: p.baseline,
  improvement_pct: p.improvement_pct,
  reason: p.reason,
})
void JSON.stringify(
  {
    world_id: world.id,
    world_revision: world.revision_sha256,
    seed: world.seed,
    baseline: record(base),
    blocked_crossing: record(closed),
    isolated_dock: record(isolated),
    low_battery: record(low),
    hardware_run: false,
  },
  null,
  2,
)

test('01 world: 30 unique metric assets and 9 docking poses', () => {
  assert.deepEqual(validateWorld(world), [])
  assert.equal(world.assets.length, 30)
  assert.equal(world.goals.length, 9)
  assert.equal(world.units, 'm')
})
test('02 footprint: circumscribed disc covers all rectangle yaw', () => {
  approx(robotRadius(world.robot), Math.hypot(0.76, 0.58) / 2)
})
test('03 rotated OBB: distance and intersection', () => {
  const r = { x: 3, y: 3, width: 4, depth: 1, yaw_deg: 90 }
  approx(pointRectDistance({ x: 3, y: 5 }, r), 0)
  approx(pointRectDistance({ x: 5, y: 3 }, r), 1.5)
  approx(segmentRectDistance({ x: 1, y: 3 }, { x: 5, y: 3 }, r), 0)
})
test('04 no corner-cutting edge is enabled', () => {
  for (let i = 0; i < planner.n; i++)
    for (const h of [1, 3, 5, 7])
      if (planner.mask[i] & (1 << h)) {
        const d = [
          [1, 1],
          [-1, 1],
          [-1, -1],
          [1, -1],
        ][(h - 1) / 2]
        assert.ok(planner.clear[i + d[0]] > planner.r)
        assert.ok(planner.clear[i + d[1] * planner.nx] > planner.r)
      }
})
test('05 all 9 docks have valid clearance', () => {
  for (const g of world.goals) assert.ok(pointClearance(world, g.pose) > planner.r)
})
test('06 unknown goal is rejected', () =>
  assert.throws(() => planner.plan(['fabricated-location']), /UNKNOWN_GOAL/))
test('07 invalid profile rejected', () =>
  assert.throws(() => new MetricPlanner(world, { profile: { ...world.robot, width: -1 } })))
test('08 negative penalty rejected (A* assumption)', () =>
  assert.throws(() => new MetricPlanner(world, { clearance_weight: -0.1 })))
test('09 blocked endpoint is not silently relocated', () =>
  assert.throws(() => planner.path(world.home, { x: 1.15, y: 3.75, yaw: 0 }), /POSE_BLOCKED/))
test('10 exact endpoints and docking yaw preserved', () => {
  for (const s of base.segments) {
    const expected =
      s.to_id === '__end' ? world.home : world.goals.find((g) => g.id === s.to_id).pose
    const end = s.poses.at(-1)
    approx(end.x, expected.x)
    approx(end.y, expected.y)
    approx(angleDelta(end.yaw, expected.yaw), 0)
  }
})
test('11 every returned segment collision-checked incl. rotate', () => {
  for (const s of base.segments)
    for (const a of s.actions) assert.ok(segmentClearance(world, a.from, a.to) > planner.r)
})
test('12 drive is along heading; rotation is in-place', () => {
  for (const s of base.segments)
    for (const a of s.actions) {
      if (a.type === 'drive') {
        approx(angleDelta(a.from.yaw, Math.atan2(a.to.y - a.from.y, a.to.x - a.from.x)), 0)
        approx(angleDelta(a.from.yaw, a.to.yaw), 0)
      } else {
        approx(a.distance_m, 0)
        assert.ok(a.duration_s > 0)
      }
    }
})
test('13 docking route includes return home', () => {
  assert.equal(base.segments.at(-1).to_id, '__end')
})
test('14 slow-zone travel speed honored', () => {
  const a = { x: 21, y: 6 },
    b = { x: 21, y: 7 }
  assert.ok(segmentSpeed(world, a, b, world.robot) < world.robot.max_speed)
})
test('15 A* cost equals Dijkstra on same heading graph', () => {
  const g = world.goals[0].pose
  approx(
    planner.path(world.home, g).cost_s,
    planner.path(world.home, g, { heuristic_enabled: false }).cost_s,
  )
})
test('16 optimized multi-stop costs no more than input order', () => {
  assert.equal(base.status, 'READY')
  assert.equal(base.order_method, 'held_karp_exact')
  assert.ok(base.cost_s < base.baseline.cost_s)
})
test('17 Held-Karp equals brute force for four stops', () => {
  const ids = goals.slice(0, 4)
  const result = planner.plan(ids)
  function perm(xs) {
    return xs.length
      ? xs.flatMap((x, i) => perm(xs.filter((_, j) => j !== i)).map((p) => [x, ...p]))
      : [[]]
  }
  const costs = perm(ids).map((order) => planner.plan(order, { optimize: false }).cost_s)
  approx(result.cost_s, Math.min(...costs))
})
test('18 deterministic repeated plan', () => {
  assert.deepEqual(base.goal_ids, planner.plan(goals).goal_ids)
  approx(base.distance_m, planner.plan(goals).distance_m)
})
test('19 temporary crossing obstruction reroutes, rather than ignoring geometry', () => {
  assert.equal(closed.status, 'READY')
  assert.notEqual(base.revision, closed.revision)
  assert.notEqual(base.distance_m, closed.distance_m)
  for (const s of closed.segments)
    for (const a of s.actions)
      assert.ok(segmentClearance(world, a.from, a.to, blocked.obstacles) > blocked.r)
})
test('20 isolated dock becomes BLOCKED', () => assert.equal(isolated.status, 'BLOCKED'))
test('21 low battery produces NEEDS_CHARGE', () => assert.equal(low.status, 'NEEDS_CHARGE'))
test('22 overload produces batch split; no overloaded mission executable', () => {
  const p = planner.plan(goals, { payload_kg: 16 })
  assert.equal(p.status, 'SPLIT_REQUIRED')
  if (p.batches) for (const b of p.batches) assert.ok(b.weight <= 2)
})
test('23 capability mismatch prevents unsupported skill', () => {
  const w = structuredClone(world)
  w.goals[0].require_capabilities = ['open_drawer_autonomously']
  assert.equal(new MetricPlanner(w).plan([w.goals[0].id]).status, 'CAPABILITY_MISMATCH')
})
test('24 invalid resource state rejected', () => {
  assert.throws(() => planner.plan(goals, { battery_pct: NaN }))
  assert.throws(() => planner.plan(goals, { payload_kg: -1 }))
})
test('25 split batch unserviceable item returns null', () =>
  assert.equal(splitBatches([{ id: 'heavy', payload_kg: 20 }], 18), null))
test('26 zero-stop plan remains valid, finite and stationary', () => {
  const p = planner.plan([])
  assert.equal(p.status, 'READY')
  approx(p.distance_m, 0)
  assert.ok(Number.isFinite(p.cost_s))
})
test('27 pause freezes pose', () => {
  const r = new MissionRunner(world)
  r.load(base)
  r.start()
  r.tick(3, planner)
  r.pause()
  const p = { ...r.pose }
  r.tick(10, planner)
  assert.deepEqual(r.pose, p)
})
test('28 wrong scan never acknowledges inventory or completes stop', () => {
  const r = new MissionRunner(world)
  r.load(base)
  r.start()
  for (let k = 0; k < 500 && r.state === 'RUNNING'; k++) r.tick(1, planner)
  assert.equal(r.state, 'WAITING_HANDOFF')
  assert.equal(r.confirmHandoff({ scanCode: 'WRONG' }), false)
  assert.equal(r.completed.length, 0)
})
test('29 simulated auto handoff completes six stops + return without collision', () => {
  const r = new MissionRunner(world)
  r.load(base)
  r.autoHandoff = true
  r.start()
  for (let k = 0; k < 2000 && r.state !== 'COMPLETED'; k++) r.tick(0.5, planner)
  assert.equal(r.state, 'COMPLETED')
  assert.equal(r.completed.length, 6)
  approx(r.pose.x, world.home.x)
  approx(r.pose.y, world.home.y)
  assert.ok(!r.events.some((e) => e.type === 'COLLISION_STOP'))
  assert.equal(r.exportReplay().inventory_written, false)
  void JSON.stringify(r.exportReplay(), null, 2)
})
test('30 replan starts at actual pose and preserves completed work', () => {
  const r = new MissionRunner(world)
  r.load(base)
  r.start()
  for (let k = 0; k < 500 && r.state === 'RUNNING'; k++) r.tick(1, planner)
  const g = world.goals.find((g) => g.id === r.currentGoal())
  assert.equal(r.confirmHandoff({ scanCode: g.slot }), true)
  r.tick(0.3, planner)
  const at = { ...r.pose },
    cargo = r.cargo,
    done = [...r.completed]
  r.beginReplan(blocked.extra)
  const p = blocked.plan(r.remainingGoals(), {
    start: at,
    payload_kg: cargo,
    battery_pct: r.batteryPct,
  })
  assert.equal(p.status, 'READY')
  r.load(p, { preserveProgress: true, obstacles: blocked.extra })
  assert.deepEqual(r.pose, at)
  assert.deepEqual(r.completed, done)
  approx(r.cargo, cargo)
})
test('31 injecting obstacle under robot stops immediately', () => {
  const r = new MissionRunner(world)
  r.load(base)
  r.start()
  const obs = { id: 'oops', x: r.pose.x, y: r.pose.y, width: 1, depth: 1, height: 1, yaw_deg: 0 }
  const p = { ...planner, obstacles: [...planner.obstacles, obs], r: planner.r }
  r.tick(0.5, p)
  assert.equal(r.state, 'BLOCKED')
})
test('32 cancel is final until a new plan is explicitly loaded', () => {
  const r = new MissionRunner(world)
  r.load(base)
  r.start()
  r.cancel()
  assert.equal(r.start(), false)
  assert.equal(r.state, 'CANCELLED')
})
test('33 mismatched mission start rejected', () => {
  const r = new MissionRunner(world)
  const p = structuredClone(base)
  p.segments[0].poses[0].x += 1
  assert.throws(() => r.load(p), /START_DOES_NOT_MATCH/)
})
test('34 mission clock rejects nonfinite or negative delta', () => {
  const r = new MissionRunner(world)
  assert.throws(() => r.tick(NaN, planner))
  assert.throws(() => r.tick(-1, planner))
})
test('36 docking slot names match original physical conventions', () => {
  for (const g of world.goals) {
    const a = world.assets.find((a) => a.id === g.asset_id)
    if (a.kind === 'organizer56') assert.match(g.slot, /^[A-G]0[1-8]$/)
    if (a.kind === 'drawer100') assert.match(g.slot, /^[A-E](0[1-9]|1[0-9]|20)$/)
  }
})
