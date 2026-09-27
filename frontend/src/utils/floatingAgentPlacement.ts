export const FLOATING_AGENT_SIZE_KEY = 'materialbrain:floating-agent:size:v1'
export const FLOATING_AGENT_MOBILE_BREAKPOINT = 720
export const FLOATING_AGENT_MIN_WIDTH = 360
export const FLOATING_AGENT_MIN_HEIGHT = 420
export const FLOATING_AGENT_DEFAULT_WIDTH = 460
export const FLOATING_AGENT_MAX_WIDTH = 760
export const FLOATING_AGENT_SAFE_MARGIN = 12
export const FLOATING_AGENT_GAP = 12

export interface FloatingAgentSize {
  width: number
  height: number
}

export interface FloatingAgentRect {
  left: number
  top: number
  right: number
  bottom: number
  width: number
  height: number
}

export interface FloatingAgentPlacementInput {
  robotRect: FloatingAgentRect
  panelWidth: number
  panelHeight: number
  viewportWidth: number
  viewportHeight: number
  safeMargin?: number
  gap?: number
}

export type FloatingAgentSide = 'left' | 'right' | 'clamped' | 'mobile'

export interface FloatingAgentPlacement {
  left: number
  top: number
  side: FloatingAgentSide
}

function clamp(value: number, minimum: number, maximum: number) {
  return Math.min(Math.max(value, minimum), Math.max(minimum, maximum))
}

export function floatingAgentLimits(viewportWidth: number, viewportHeight: number) {
  const safeWidth = Math.max(1, viewportWidth - FLOATING_AGENT_SAFE_MARGIN * 2)
  const safeHeight = Math.max(1, viewportHeight - FLOATING_AGENT_SAFE_MARGIN * 2)
  return {
    minWidth: Math.min(FLOATING_AGENT_MIN_WIDTH, safeWidth),
    minHeight: Math.min(FLOATING_AGENT_MIN_HEIGHT, safeHeight),
    maxWidth: Math.min(FLOATING_AGENT_MAX_WIDTH, safeWidth),
    maxHeight: safeHeight,
  }
}

export function clampFloatingAgentSize(
  requested: Partial<FloatingAgentSize> | null,
  viewportWidth: number,
  viewportHeight: number,
): FloatingAgentSize {
  const limits = floatingAgentLimits(viewportWidth, viewportHeight)
  const mobile = viewportWidth < FLOATING_AGENT_MOBILE_BREAKPOINT
  const defaultHeight = Math.min(720, limits.maxHeight)
  if (mobile) {
    return {
      width: limits.maxWidth,
      height: clamp(requested?.height ?? defaultHeight, limits.minHeight, limits.maxHeight),
    }
  }
  return {
    width: clamp(requested?.width ?? FLOATING_AGENT_DEFAULT_WIDTH, limits.minWidth, limits.maxWidth),
    height: clamp(requested?.height ?? defaultHeight, limits.minHeight, limits.maxHeight),
  }
}

export function computeFloatingAgentPlacement({
  robotRect,
  panelWidth,
  panelHeight,
  viewportWidth,
  viewportHeight,
  safeMargin = FLOATING_AGENT_SAFE_MARGIN,
  gap = FLOATING_AGENT_GAP,
}: FloatingAgentPlacementInput): FloatingAgentPlacement {
  if (viewportWidth < FLOATING_AGENT_MOBILE_BREAKPOINT) {
    return {
      left: safeMargin,
      top: Math.max(safeMargin, viewportHeight - panelHeight - safeMargin),
      side: 'mobile',
    }
  }

  const robotCenterX = robotRect.left + robotRect.width / 2
  const preferred: 'left' | 'right' = robotCenterX >= viewportWidth / 2 ? 'left' : 'right'
  const leftCandidate = robotRect.left - gap - panelWidth
  const rightCandidate = robotRect.right + gap
  const leftFits = leftCandidate >= safeMargin
  const rightFits = rightCandidate + panelWidth <= viewportWidth - safeMargin

  let side: FloatingAgentSide
  let candidateX: number
  if (preferred === 'left' && leftFits) {
    side = 'left'
    candidateX = leftCandidate
  } else if (preferred === 'right' && rightFits) {
    side = 'right'
    candidateX = rightCandidate
  } else if (preferred === 'left' && rightFits) {
    side = 'right'
    candidateX = rightCandidate
  } else if (preferred === 'right' && leftFits) {
    side = 'left'
    candidateX = leftCandidate
  } else {
    side = 'clamped'
    candidateX = preferred === 'left' ? leftCandidate : rightCandidate
  }

  const desiredY = robotRect.top + robotRect.height / 2 - panelHeight / 2
  return {
    left: clamp(candidateX, safeMargin, viewportWidth - panelWidth - safeMargin),
    top: clamp(desiredY, safeMargin, viewportHeight - panelHeight - safeMargin),
    side,
  }
}
