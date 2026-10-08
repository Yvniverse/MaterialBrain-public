/** Frozen Spatial Agent v1 contracts. Values originate in the authenticated API. */
export type SpatialRecord = Record<string, unknown>
export type RouteProfile = 'fastest' | 'safest' | 'esd_safe'
export type SpatialLayer = 'geometry' | 'semantics' | 'rules' | 'cost' | 'dynamic' | 'trajectory'
export interface SpatialPose {
  x: number
  y: number
  yaw: number
}
export interface SpatialPolygon {
  type: 'Polygon'
  coordinates: number[][][]
}
export interface SpatialZone {
  id: string
  name?: string
  kind: string
  polygon: SpatialPolygon
  speed_limit_mps: number | null
  risk_level: number
  rule_refs?: string[]
}
export interface SpatialDock extends SpatialRecord {
  id: string
  label?: string
  pose: SpatialPose
  node_id: string
  kind: string
  capabilities: string[]
}
export interface SpatialMapSnapshot {
  schema_version: 1
  map_id: string
  revision: string
  frame: SpatialRecord
  geometry: SpatialRecord
  zones: SpatialZone[]
  route_graph: SpatialRecord
  docks: SpatialDock[]
  affordances: SpatialRecord[]
  dynamic_overlays: SpatialRecord[]
  provenance: SpatialRecord | string
}
export interface MissionRequest {
  map_id: string
  map_revision: string
  profile: RouteProfile
  start_pose: SpatialPose
  goal_ids: string[]
  completed_goal_ids: string[]
  stops: SpatialRecord[]
  constraints: {
    payload_capacity_kg: number
    battery_pct: number
    battery_reserve_pct: number
    robot_class: string
  }
  return_home: boolean
  dynamic_overlays: SpatialRecord[]
}
export interface MissionPlan {
  schema_version: 1
  mission_id: string
  map_id: string
  map_revision: string
  profile: RouteProfile
  status: 'READY' | 'INFEASIBLE' | 'CLARIFICATION'
  stops: SpatialRecord[]
  ordered_goal_ids: string[]
  completed_goal_ids: string[]
  constraints: SpatialRecord
  solver: SpatialRecord
  metrics: SpatialRecord
  objective_terms: SpatialRecord
  segments: SpatialRecord[]
  violations: (SpatialRecord | string)[]
}
export interface SpatialExecutionEvent {
  event_id: string
  sequence: number
  type: string
  timestamp: string
  goal_id?: string | null
  pose?: SpatialPose | null
  details: SpatialRecord
}
export interface SpatialExecution {
  mission_id: string
  map_id: string
  map_revision: string
  status: string
  current_goal_id?: string | null
  current_pose: SpatialPose | null
  completed_goal_ids: string[]
  remaining_goal_ids: string[]
  robot_state: SpatialRecord
  events: SpatialExecutionEvent[]
  last_sequence: number
  metrics: SpatialRecord
  planned_metrics?: SpatialRecord
  route_profile?: RouteProfile
  execution_boundary: 'ros2_nav2_simulation'
  hardware_control: false
}
export interface SpatialTaskNode {
  id: string
  skill: string
  args: SpatialRecord
  dependencies: string[]
  status: string
  result?: SpatialRecord | null
  failure_code?: string | null
  attempts: number
  idempotency_key: string
}
export interface SpatialTaskGraph {
  schema_version: 1
  task_id: string
  conversation_id: string | null
  map_id: string
  map_revision: string
  mission_id: string | null
  nodes: SpatialTaskNode[]
  current_skill: string | null
  completed_goal_ids: string[]
  remaining_goal_ids: string[]
  robot_state: SpatialRecord
  recovery: SpatialRecord | null
  events: SpatialExecutionEvent[]
  last_valid_plan: MissionPlan | null
  status: string
}
export interface SpatialMissionProjection {
  mission_id: string
  conversation_id: string
  task_graph: SpatialTaskGraph
  mission_plan: MissionPlan | null
  execution: SpatialExecution | null
  execution_boundary: 'ros2_nav2_simulation'
  inventory_written: false
  hardware_control: false
}
export interface SpatialNavigationHealth {
  ready: boolean
  map_id: string
  map_revision: string
  build_sha?: string
  ros_distro?: string
  nav2_version?: string
  active_plugins: SpatialRecord
  lifecycle: Record<string, string>
  actions: Record<string, boolean>
  topic_message_counts: Record<string, number>
  observed_tf: string[][]
  robot_state: SpatialRecord
  active_mission_id?: string | null
  execution_boundary: 'ros2_nav2_simulation'
  hardware_control: false
}
export interface WarehouseBenchmark {
  benchmark: string
  captured_at: string
  source_sha: string | null
  map_id: string
  map_revision: string
  seed: number
  repetitions: number
  execution_boundary: string
  measurement: Record<string, string>
  algorithm_availability: { algorithm: string; status: string; reason?: string }[]
  summaries: Record<string, Record<string, number | null>>
  artifacts?: Record<string, string>
}
export interface SpatialSceneProjection {
  snapshot: SpatialMapSnapshot | null
  layers: SpatialLayer[]
  mission: MissionPlan | null
  execution: SpatialExecution | null
}
