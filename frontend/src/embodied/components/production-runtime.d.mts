import type { LabAppHandle, LabOptions } from './lab-app.mjs'

export function mountProductionLabApp(
  root: HTMLElement,
  world: unknown,
  options: Omit<LabOptions, 'THREE' | 'OrbitControls'>,
): LabAppHandle
