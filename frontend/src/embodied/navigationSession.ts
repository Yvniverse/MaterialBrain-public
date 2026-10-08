import type { AgentNavigationPlan } from '../types'

export interface NavigationExecutionContext {
  world_id: 'MB-EMB-LAB-03'
  world_revision: string
  mode: 'simulation'
  scenario_id: 'baseline' | 'blocked-crossing' | 'isolated-dock' | 'low-battery'
  state:
    'READY' | 'PAUSED' | 'BLOCKED' | 'REPLANNING' | 'WAITING_HANDOFF' | 'COMPLETED' | 'CANCELLED'
  goal_ids: string[]
  completed_goal_ids: string[]
  pose: { x: number; y: number; yaw: number }
  payload_kg: number
  battery_pct: number
}
export interface ExecutionSnapshot extends NavigationExecutionContext {
  clock?: number
  travelled?: number
  events?: Array<Record<string, unknown>>
}
let current: ExecutionSnapshot | null = null
let capture: (() => ExecutionSnapshot) | null = null
let queued: { goals: string[]; scenario: string; execution: ExecutionSnapshot | null } | null = null

export function attachNavigationSession(read: () => ExecutionSnapshot) {
  capture = read
  return () => {
    if (capture === read) {
      current = read()
      capture = null
    }
  }
}
export function resetNavigationSession() {
  current = null
  capture = null
  queued = null
}
export function captureNavigationContext(): NavigationExecutionContext | null {
  if (capture) current = capture()
  if (
    !current ||
    !current.goal_ids.length ||
    !['READY', 'PAUSED', 'BLOCKED', 'REPLANNING', 'WAITING_HANDOFF'].includes(current.state)
  )
    return null
  return structuredClone({
    world_id: current.world_id,
    world_revision: current.world_revision,
    mode: current.mode,
    scenario_id: current.scenario_id,
    state: current.state,
    goal_ids: current.goal_ids,
    completed_goal_ids: current.completed_goal_ids,
    pose: current.pose,
    payload_kg: current.payload_kg,
    battery_pct: current.battery_pct,
  })
}
export function queueNavigationPlan(plan: AgentNavigationPlan): boolean {
  if (plan.resumed) {
    // The live scene may have advanced while the Agent request was in flight.
    if (capture) current = capture()
    const expected = plan.execution_context
    if (
      !current ||
      !expected ||
      expected.world_revision !== current.world_revision ||
      expected.state !== current.state ||
      JSON.stringify(expected.goal_ids) !== JSON.stringify(current.goal_ids) ||
      Math.hypot(expected.pose.x - current.pose.x, expected.pose.y - current.pose.y) > 0.01 ||
      Math.abs(expected.pose.yaw - current.pose.yaw) > 0.01 ||
      JSON.stringify(expected.completed_goal_ids) !== JSON.stringify(current.completed_goal_ids) ||
      Math.abs(expected.payload_kg - current.payload_kg) > 1e-6 ||
      Math.abs(expected.battery_pct - current.battery_pct) > 1e-6
    )
      return false
    queued = {
      goals: current.goal_ids,
      scenario: plan.scenario_id || current.scenario_id,
      execution: structuredClone(current),
    }
  } else {
    queued = {
      goals: plan.requested_goal_ids || plan.goal_ids || [],
      scenario: plan.scenario_id || 'baseline',
      execution: null,
    }
  }
  return true
}
export function takeNavigationSession(freshTask = false) {
  if (queued) {
    const next = queued
    queued = null
    if (next.execution)
      next.execution.scenario_id = next.scenario as NavigationExecutionContext['scenario_id']
    return next
  }
  if (freshTask) return null
  if (current && ['READY', 'PAUSED', 'BLOCKED', 'REPLANNING'].includes(current.state))
    return {
      goals: current.goal_ids,
      scenario: current.scenario_id,
      execution: structuredClone(current),
    }
  return null
}
