import type { ExecutionSnapshot } from '../navigationSession'
export interface LabAppHandle {
  diagnostics(): Record<string, unknown>
  dispose(): void
  navigate(id: string): void
  planMission(options?: { replan?: boolean }): Promise<void>
  captureExecution(): ExecutionSnapshot
  setSpatialLayers(
    snapshot: unknown,
    layers: string[],
    mission?: unknown,
    execution?: unknown,
  ): void
}
export interface LabOptions {
  THREE: unknown
  OrbitControls: unknown
  botUrl: string
  showNavigation?: boolean
  showBot?: boolean
  onNavigate?: (id: string) => void
  onOpenLocation?: (selection: {
    reference_code?: string
    asset_id: string
    slot?: string
    world_id: string
  }) => void
  initialGoalIds?: string[] | null
  initialScenario?: string
  initialExecution?: ExecutionSnapshot | null
  planningClient?: ((request: unknown) => Promise<unknown>) | null
}
export function mountLabApp(root: HTMLElement, world: unknown, options: LabOptions): LabAppHandle
