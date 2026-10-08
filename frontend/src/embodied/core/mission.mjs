import { angleDelta, distance, segmentClearance } from './geometry.mjs'
/** A deterministic execution simulator, not a velocity controller. No inventory writes. */
export class MissionRunner {
  constructor(world, { onChange = () => {} } = {}) {
    this.world = world
    this.onChange = onChange
    this.events = []
    this.seq = 0
    this.reset()
  }
  emit(type, detail = {}) {
    const e = { seq: ++this.seq, t_sim_s: +this.clock.toFixed(3), type, ...detail }
    this.events.push(e)
    this.onChange(this, e)
    return e
  }
  reset() {
    this.state = 'IDLE'
    this.pose = { ...this.world.home }
    this.clock = 0
    this.completed = []
    this.plan = null
    this.segmentIndex = 0
    this.actionIndex = 0
    this.actionTime = 0
    this.travelled = 0
    this.obstacles = []
    this.cargo = 0
    this.waitTime = 0
    this.autoHandoff = false
    this.batteryBasePct = this.world.robot.battery_pct
  }
  snapshot(scenarioId = 'baseline') {
    return {
      world_id: this.world.id,
      world_revision: this.world.revision_sha256,
      mode: 'simulation',
      scenario_id: scenarioId,
      state: this.state,
      goal_ids: [...new Set([...this.completed, ...(this.plan?.goal_ids || [])])],
      completed_goal_ids: [...this.completed],
      pose: { ...this.pose },
      payload_kg: this.cargo,
      battery_pct: this.batteryPct,
      clock: this.clock,
      travelled: this.travelled,
      events: this.events.slice(-256).map((e) => ({ ...e })),
    }
  }
  restore(snapshot) {
    const known = new Set(this.world.goals.map((g) => g.id))
    const ids = snapshot.goal_ids || [],
      completed = snapshot.completed_goal_ids || []
    if (
      snapshot.world_id !== this.world.id ||
      snapshot.world_revision !== this.world.revision_sha256 ||
      snapshot.mode !== 'simulation' ||
      ids.some((id) => !known.has(id)) ||
      completed.some((id) => !ids.includes(id)) ||
      new Set(ids).size !== ids.length ||
      !['x', 'y', 'yaw'].every((key) => Number.isFinite(snapshot.pose?.[key])) ||
      snapshot.pose.x < 0 ||
      snapshot.pose.x > this.world.width ||
      snapshot.pose.y < 0 ||
      snapshot.pose.y > this.world.height ||
      !Number.isFinite(snapshot.battery_pct) ||
      !Number.isFinite(snapshot.payload_kg)
    )
      throw new Error('INVALID_EXECUTION_SNAPSHOT')
    this.reset()
    this.pose = { ...snapshot.pose }
    this.completed = [...completed]
    this.cargo = snapshot.payload_kg
    this.clock = Number.isFinite(snapshot.clock) ? snapshot.clock : 0
    this.travelled = Number.isFinite(snapshot.travelled) ? snapshot.travelled : 0
    this.batteryBasePct =
      snapshot.battery_pct +
      (100 *
        (this.travelled * this.world.robot.wh_per_m +
          (this.clock * this.world.robot.idle_w) / 3600)) /
        this.world.robot.battery_wh
    this.events = (snapshot.events || []).slice(-256).map((e) => ({ ...e }))
    this.seq = this.events.reduce((n, e) => Math.max(n, e.seq || 0), 0)
    this.state = 'PAUSED'
  }
  load(plan, { preserveProgress = false, obstacles = [] } = {}) {
    if (plan.status !== 'READY') throw new Error('PLAN_NOT_EXECUTABLE:' + plan.status)
    if (!preserveProgress) {
      this.reset()
      this.events = []
      this.seq = 0
    }
    const first = plan.segments[0]?.poses[0]
    if (
      first &&
      (distance(first, this.pose) > 0.01 || Math.abs(angleDelta(this.pose.yaw, first.yaw)) > 0.01)
    )
      throw new Error('PLAN_START_DOES_NOT_MATCH_POSE')
    this.plan = plan
    this.segmentIndex = 0
    this.actionIndex = 0
    this.actionTime = 0
    this.obstacles = obstacles
    this.state = 'READY'
    this.emit(preserveProgress ? 'REPLAN_READY' : 'PLAN_READY', {
      revision: plan.revision,
      goals: plan.goal_ids,
    })
  }
  start() {
    if (!['READY', 'PAUSED'].includes(this.state)) return false
    this.state = 'RUNNING'
    this.emit('START')
    return true
  }
  pause() {
    if (this.state === 'RUNNING') {
      this.state = 'PAUSED'
      this.emit('PAUSE')
    }
  }
  cancel() {
    if (!['COMPLETED', 'CANCELLED', 'IDLE'].includes(this.state)) {
      this.state = 'CANCELLED'
      this.emit('CANCEL')
    }
  }
  get batteryPct() {
    return Math.max(
      0,
      this.batteryBasePct -
        (100 *
          (this.travelled * this.world.robot.wh_per_m +
            (this.clock * this.world.robot.idle_w) / 3600)) /
          this.world.robot.battery_wh,
    )
  }
  remainingGoals() {
    return (this.plan?.goal_ids || []).filter((id) => !this.completed.includes(id))
  }
  beginReplan(obstacles) {
    if (this.state === 'WAITING_HANDOFF') throw new Error('CONFIRM_HANDOFF_FIRST')
    this.state = 'REPLANNING'
    this.obstacles = obstacles
    this.emit('REPLAN_REQUESTED', {
      pose: { ...this.pose },
      remaining: this.remainingGoals(),
      obstacles: obstacles.map((o) => o.id),
    })
  }
  failReplan(reason) {
    this.state = 'BLOCKED'
    this.emit('REPLAN_BLOCKED', { reason })
  }
  currentGoal() {
    return this.plan?.segments[this.segmentIndex]?.to_id || null
  }
  confirmHandoff({ scanCode = null, automatic = false } = {}) {
    if (this.state !== 'WAITING_HANDOFF') return false
    const id = this.currentGoal(),
      goal = this.world.goals.find((g) => g.id === id)
    if (!goal) return false
    if (!automatic && scanCode !== goal.slot) {
      this.emit('SCAN_REJECTED', { goal: id, expected_slot: goal.slot })
      return false
    }
    if (!this.completed.includes(id)) {
      this.completed.push(id)
      this.cargo += goal.payload_kg
    }
    this.segmentIndex++
    this.actionIndex = 0
    this.actionTime = 0
    this.waitTime = 0
    this.state = 'RUNNING'
    this.emit('HANDOFF_CONFIRMED', {
      goal: id,
      mode: automatic ? 'sim_auto' : 'scan',
      inventory_written: false,
    })
    return true
  }
  tick(dt, planner) {
    if (!Number.isFinite(dt) || dt < 0) throw new Error('INVALID_DT')
    if (this.state === 'WAITING_HANDOFF') {
      this.clock += dt
      this.waitTime += dt
      const goal = this.world.goals.find((g) => g.id === this.currentGoal())
      if (this.autoHandoff && this.waitTime >= (goal?.service_s || 12))
        this.confirmHandoff({ automatic: true })
      return
    }
    if (this.state !== 'RUNNING' || !this.plan) return
    let budget = dt,
      guard = 0
    while (budget > 1e-9 && this.state === 'RUNNING' && guard++ < 10000) {
      const seg = this.plan.segments[this.segmentIndex]
      if (!seg) {
        this.state = 'COMPLETED'
        this.emit('COMPLETED', { distance_m: this.travelled, inventory_written: false })
        break
      }
      const action = seg.actions[this.actionIndex]
      if (!action) {
        if (seg.to_id === '__end') {
          this.segmentIndex++
          continue
        }
        this.state = 'WAITING_HANDOFF'
        this.waitTime = 0
        this.emit('ARRIVED', { goal: seg.to_id, pose: { ...this.pose } })
        break
      }
      const delta = Math.min(budget, Math.max(0, action.duration_s - this.actionTime))
      const t =
        action.duration_s <= 1e-12 ? 1 : Math.min(1, (this.actionTime + delta) / action.duration_s)
      const next = {
        x: action.from.x + (action.to.x - action.from.x) * t,
        y: action.from.y + (action.to.y - action.from.y) * t,
        yaw: action.from.yaw + angleDelta(action.from.yaw, action.to.yaw) * t,
      }
      if (
        planner &&
        segmentClearance(this.world, this.pose, next, planner.obstacles) <= planner.r + 1e-9
      ) {
        this.state = 'BLOCKED'
        this.emit('COLLISION_STOP', { pose: { ...this.pose } })
        break
      }
      this.travelled += distance(this.pose, next)
      this.pose = next
      this.clock += delta
      budget -= delta
      this.actionTime += delta
      if (t >= 1 - 1e-9) {
        this.pose = { ...action.to }
        this.actionIndex++
        this.actionTime = 0
      }
    }
  }
  exportReplay() {
    return {
      schema_version: 1,
      kind: 'deterministic_navigation_simulation',
      world_id: this.world.id,
      world_revision: this.world.revision_sha256,
      seed: this.world.seed,
      profile: this.world.robot,
      plan: this.plan,
      state: this.state,
      pose: this.pose,
      completed: this.completed,
      events: this.events,
      summary: { t_sim_s: this.clock, distance_m: this.travelled, cargo_kg: this.cargo },
      hardware_run: false,
      inventory_written: false,
    }
  }
}
