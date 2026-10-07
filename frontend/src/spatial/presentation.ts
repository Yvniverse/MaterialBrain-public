import type { SpatialMapSnapshot, SpatialPose, SpatialRecord, SpatialZone } from './contracts'

export const profileLabels = { fastest: '最快', safest: '更安全', esd_safe: '防静电优先' }
export const layerLabels = {
  geometry: 'Geometry · 几何',
  semantics: 'Semantics · 语义',
  rules: 'Rules · 规则',
  cost: 'Cost · 代价',
  dynamic: 'Dynamic · 动态',
  trajectory: 'Trajectory · 轨迹',
}
const statusLabels: Record<string, string> = {
  READY: '待启动',
  QUEUED: '排队中',
  NAVIGATING: '导航中',
  AWAITING_HANDOFF: '等待扫码交接',
  CHARGING: '充电中',
  COMPLETED: '已完成',
  CANCELLED: '已取消',
  BLOCKED: '任务受阻',
  FAILED: '执行失败',
  TRANSPORT_PAUSED: '连接暂停',
  INFEASIBLE: '约束不可行',
  CLARIFICATION: '待补充条件',
  BLOCKED_LOW_BATTERY: '电量约束受阻',
  PENDING: '待执行',
  RUNNING: '进行中',
  SUCCEEDED: '完成',
  SKIPPED: '已跳过',
  RECOVERING: '恢复中',
}
const eventLabels: Record<string, string> = {
  started: '启动',
  feedback: '导航反馈',
  arrived: '到达站点',
  scan_rejected: '扫码不匹配',
  scan_verified: '扫码验证',
  handoff_verified: '交接确认',
  obstacle_added: '添加临时障碍',
  obstacle_removed: '移除临时障碍',
  replanning: '重新规划',
  recovery: '导航恢复',
  charging: '充电',
  cancelled: '取消',
  transport_paused: '连接暂停',
  returned_home: '返回待命点',
  completed: '任务完成',
  failed: '失败',
}
export const skillLabels: Record<string, string> = {
  resolve_engineering_bom: '核对工程 BOM',
  check_inventory: '核对库存',
  resolve_pick_locations: '定位取料库位',
  query_spatial_context: '读取空间约束',
  navigate_mission: '导航',
  confirm_scan: '扫码验证',
  verify_handoff: '人工交接',
  charge_robot: '充电',
  summarize_mission: '任务总结',
  query_spatial_map: '读取地图',
  plan_mission: '规划任务',
  navigate_to: '导航',
  navigate_to_pose: '导航',
  follow_route: '跟随路线',
  scan_code: '扫码验证',
  human_handoff: '人工交接',
  handoff: '交接',
  charge: '充电',
  return_home: '返回待命点',
  wait_or_yield: '等待 / 避让',
  replan_remaining: '重规划剩余任务',
}
export const statusLabel = (value: string) => statusLabels[value] || value
export const eventLabel = (value: string) => eventLabels[value] || value
export const skillLabel = (value: string | null) => (value ? skillLabels[value] || value : '—')
export const finite = (value: unknown): value is number =>
  typeof value === 'number' && Number.isFinite(value)
export const formatMetric = (value: unknown, digits = 1): string =>
  finite(value) ? value.toFixed(digits) : '—'
export const formatPercent = (value: unknown): string =>
  finite(value) ? `${(value * 100).toFixed(1)}%` : '—'
export const record = (value: unknown): SpatialRecord =>
  value && typeof value === 'object' && !Array.isArray(value) ? (value as SpatialRecord) : {}
export const textValue = (value: unknown, fallback = '—'): string =>
  typeof value === 'string' && value ? value : fallback
export function goalLabel(snapshot: SpatialMapSnapshot | null, id: string): string {
  return snapshot?.docks.find((dock) => dock.id === id)?.label || id
}

function onSegment(x: number, y: number, a: number[], b: number[]): boolean {
  const cross = (y - a[1]) * (b[0] - a[0]) - (x - a[0]) * (b[1] - a[1])
  return (
    Math.abs(cross) < 1e-9 &&
    x >= Math.min(a[0], b[0]) &&
    x <= Math.max(a[0], b[0]) &&
    y >= Math.min(a[1], b[1]) &&
    y <= Math.max(a[1], b[1])
  )
}
function ringContains(ring: number[][], point: SpatialPose): boolean {
  let inside = false
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const a = ring[j],
      b = ring[i]
    if (onSegment(point.x, point.y, a, b)) return true
    if (
      a[1] > point.y !== b[1] > point.y &&
      point.x < ((b[0] - a[0]) * (point.y - a[1])) / (b[1] - a[1]) + a[0]
    )
      inside = !inside
  }
  return inside
}
/** Project an observed pose onto the registered map; no guessed coordinates. */
export function currentZones(
  snapshot: SpatialMapSnapshot | null,
  point: SpatialPose | null,
): SpatialZone[] {
  if (!snapshot || !point || !finite(point.x) || !finite(point.y)) return []
  return snapshot.zones.filter((zone) => {
    const [outer, ...holes] = zone.polygon.coordinates
    return outer && ringContains(outer, point) && !holes.some((hole) => ringContains(hole, point))
  })
}
