import { beforeEach, describe, expect, it } from 'vitest'
import world from '../src/embodied/scene/world.v3.json'
import {
  captureNavigationContext,
  attachNavigationSession,
  queueNavigationPlan,
  resetNavigationSession,
  takeNavigationSession,
  type ExecutionSnapshot,
} from '../src/embodied/navigationSession'
import { MissionRunner } from '../src/embodied/core/mission.mjs'
import { MetricPlanner } from '../src/embodied/core/planner.mjs'

const planner = new MetricPlanner(world)
function context(): ExecutionSnapshot {
  return {
    world_id: 'MB-EMB-LAB-03',
    world_revision: world.revision_sha256,
    mode: 'simulation',
    scenario_id: 'baseline',
    state: 'PAUSED',
    goal_ids: ['P-IC', 'P-CABLE'],
    completed_goal_ids: ['P-IC'],
    pose: { ...world.goals[0].pose },
    payload_kg: 0.8,
    battery_pct: 78.5,
    clock: 90,
    travelled: 15,
    events: [{ seq: 1, type: 'PAUSE' }],
  }
}
describe('shared laboratory execution session', () => {
  beforeEach(resetNavigationSession)
  it('does not invent execution before a task exists', () =>
    expect(captureNavigationContext()).toBeNull())
  it('captures current pose and progress but omits local replay fields from HTTP', () => {
    const read = context()
    attachNavigationSession(() => read)
    const result = captureNavigationContext()
    expect(result?.pose).toEqual(read.pose)
    expect(result?.completed_goal_ids).toEqual(['P-IC'])
    expect(result).not.toHaveProperty('clock')
    expect(result).not.toHaveProperty('events')
  })
  it('keeps the paused mission across route unmount', () => {
    const detach = attachNavigationSession(context)
    detach()
    expect(takeNavigationSession()?.execution?.payload_kg).toBe(0.8)
  })
  it('ignores detach from an older scene instance', () => {
    const detach = attachNavigationSession(context)
    attachNavigationSession(() => ({ ...context(), battery_pct: 70 }))
    detach()
    expect(captureNavigationContext()?.battery_pct).toBe(70)
  })
  it('clears execution on account transition', () => {
    const detach = attachNavigationSession(context)
    resetNavigationSession()
    detach()
    expect(takeNavigationSession()).toBeNull()
    expect(captureNavigationContext()).toBeNull()
  })
  it('opens a new task without reusing previous progress', () => {
    attachNavigationSession(context)()
    expect(queueNavigationPlan({ status: 'READY', goal_ids: ['P-SENSOR'] })).toBe(true)
    const next = takeNavigationSession()
    expect(next?.execution).toBeNull()
    expect(next?.goals).toEqual(['P-SENSOR'])
  })
  it('preserves completed stops and current pose for Agent replanning', () => {
    attachNavigationSession(context)
    const captured = captureNavigationContext()!
    expect(
      queueNavigationPlan({
        status: 'READY',
        resumed: true,
        scenario_id: 'blocked-crossing',
        goal_ids: ['P-CABLE'],
        execution_context: captured,
      }),
    ).toBe(true)
    const next = takeNavigationSession()
    expect(next?.execution?.pose).toEqual(captured.pose)
    expect(next?.execution?.completed_goal_ids).toEqual(['P-IC'])
    expect(next?.execution?.scenario_id).toBe('blocked-crossing')
  })
  it('rejects stale cards after the robot changes pose', () => {
    attachNavigationSession(context)
    const captured = captureNavigationContext()!
    const expected = { ...captured, pose: { ...captured.pose, x: captured.pose.x + 1 } }
    expect(
      queueNavigationPlan({ status: 'READY', resumed: true, execution_context: expected }),
    ).toBe(false)
    expect(takeNavigationSession(true)).toBeNull()
  })
  it('refreshes the attached scene before accepting a returned replan card', () => {
    let live = context()
    attachNavigationSession(() => structuredClone(live))
    const captured = captureNavigationContext()!
    live = { ...live, pose: { ...live.pose, x: live.pose.x + 1 } }

    expect(
      queueNavigationPlan({ status: 'READY', resumed: true, execution_context: captured }),
    ).toBe(false)
    expect(takeNavigationSession(true)).toBeNull()
    expect(captureNavigationContext()?.pose).toEqual(live.pose)
  })
  it('rejects a returned card after the live task completes another handoff', () => {
    let live = context()
    attachNavigationSession(() => structuredClone(live))
    const captured = captureNavigationContext()!
    live = {
      ...live,
      completed_goal_ids: [...live.goal_ids],
      payload_kg: 1.5,
      battery_pct: 76,
    }

    expect(
      queueNavigationPlan({ status: 'READY', resumed: true, execution_context: captured }),
    ).toBe(false)
    expect(takeNavigationSession(true)).toBeNull()
    expect(captureNavigationContext()?.completed_goal_ids).toEqual(live.goal_ids)
  })
  it('isolates a returned HTTP context from local state mutations', () => {
    attachNavigationSession(context)
    const captured = captureNavigationContext()!
    captured.pose.x = 0
    expect(captureNavigationContext()?.pose.x).toBe(world.goals[0].pose.x)
  })
})
describe('mission restore uses the production simulation state machine', () => {
  it('retains pose, cargo, completed stations and battery budget', () => {
    const runner = new MissionRunner(world)
    runner.restore(context())
    const plan = planner.plan(['P-CABLE'], {
      start: runner.pose,
      payload_kg: runner.cargo,
      battery_pct: runner.batteryPct,
    })
    runner.load(plan, { preserveProgress: true })
    expect(runner.completed).toEqual(['P-IC'])
    expect(runner.pose).toEqual(world.goals[0].pose)
    expect(runner.cargo).toBe(0.8)
    expect(runner.batteryPct).toBeCloseTo(78.5, 9)
  })
  it('refuses a different world revision', () => {
    expect(() =>
      new MissionRunner(world).restore({ ...context(), world_revision: 'wrong' }),
    ).toThrow('INVALID_EXECUTION_SNAPSHOT')
  })
  it('refuses unregistered docks', () => {
    expect(() => new MissionRunner(world).restore({ ...context(), goal_ids: ['P-FAKE'] })).toThrow(
      'INVALID_EXECUTION_SNAPSHOT',
    )
  })
  it('refuses nonfinite robot poses', () => {
    expect(() =>
      new MissionRunner(world).restore({ ...context(), pose: { x: NaN, y: 1, yaw: 0 } }),
    ).toThrow('INVALID_EXECUTION_SNAPSHOT')
  })
  it('preserves completed stops in a snapshot after replanning only the remaining route', () => {
    const runner = new MissionRunner(world)
    runner.restore(context())
    runner.load(planner.plan(['P-CABLE'], { start: runner.pose, payload_kg: runner.cargo }), {
      preserveProgress: true,
    })
    expect(runner.snapshot().goal_ids).toEqual(['P-IC', 'P-CABLE'])
    expect(runner.exportReplay().inventory_written).toBe(false)
  })
})
