<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { Close } from '@element-plus/icons-vue'
import { useWarehouseAgent } from '../../composables/useWarehouseAgent'
import {
  FLOATING_AGENT_MOBILE_BREAKPOINT,
  FLOATING_AGENT_SAFE_MARGIN,
  FLOATING_AGENT_SIZE_KEY,
  clampFloatingAgentSize,
  computeFloatingAgentPlacement,
  type FloatingAgentSide,
} from '../../utils/floatingAgentPlacement'
import FloatingAgentConversation from './FloatingAgentConversation.vue'

const props = defineProps<{
  open: boolean
  anchor: { x: number; y: number; width: number; height: number }
}>()

const emit = defineEmits<{ close: [] }>()
const { proposalBusyId } = useWarehouseAgent()
const panel = ref<HTMLElement | null>(null)
const closeButton = ref<HTMLButtonElement | null>(null)
const panelPosition = reactive({ left: FLOATING_AGENT_SAFE_MARGIN, top: FLOATING_AGENT_SAFE_MARGIN })
const panelSize = reactive({ width: 460, height: 720 })
const placementSide = ref<FloatingAgentSide>('right')
const mobile = ref(false)
const resizeState = reactive({
  active: false,
  axes: '' as '' | 'x' | 'y' | 'xy',
  startX: 0,
  startY: 0,
  width: 0,
  height: 0,
})
let sizeLoaded = false
let placementFrame = 0
let resizeFrame = 0
let pendingResizeEvent: PointerEvent | null = null

function robotRect() {
  return {
    left: props.anchor.x,
    top: props.anchor.y,
    right: props.anchor.x + props.anchor.width,
    bottom: props.anchor.y + props.anchor.height,
    width: props.anchor.width,
    height: props.anchor.height,
  }
}

function applyClampedSize(requested: Partial<{ width: number; height: number }> | null) {
  const clamped = clampFloatingAgentSize(requested, window.innerWidth, window.innerHeight)
  panelSize.width = clamped.width
  panelSize.height = clamped.height
  mobile.value = window.innerWidth < FLOATING_AGENT_MOBILE_BREAKPOINT
}

function loadSavedSize() {
  let stored: Partial<{ width: number; height: number }> | null = null
  try {
    stored = JSON.parse(localStorage.getItem(FLOATING_AGENT_SIZE_KEY) || 'null')
  } catch {
    stored = null
  }
  applyClampedSize(stored)
  sizeLoaded = true
}

function saveSize() {
  try {
    localStorage.setItem(
      FLOATING_AGENT_SIZE_KEY,
      JSON.stringify({ width: panelSize.width, height: panelSize.height }),
    )
  } catch {
    // Storage may be unavailable in hardened or private browser contexts.
  }
}

function placePanel() {
  if (!props.open) return
  const placement = computeFloatingAgentPlacement({
    robotRect: robotRect(),
    panelWidth: panelSize.width,
    panelHeight: panelSize.height,
    viewportWidth: window.innerWidth,
    viewportHeight: window.innerHeight,
  })
  panelPosition.left = placement.left
  panelPosition.top = placement.top
  placementSide.value = placement.side
}

function schedulePlacement() {
  cancelAnimationFrame(placementFrame)
  placementFrame = requestAnimationFrame(placePanel)
}

async function openPanel() {
  if (!sizeLoaded) loadSavedSize()
  else applyClampedSize(panelSize)
  await nextTick()
  placePanel()
  closeButton.value?.focus({ preventScroll: true })
}

function requestClose() {
  if (proposalBusyId.value !== null) return
  emit('close')
}

function onWindowKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape' && props.open) requestClose()
}

function onViewportChange() {
  applyClampedSize(panelSize)
  placePanel()
}

function onResizeStart(event: PointerEvent, axes: 'x' | 'y' | 'xy') {
  if (mobile.value || event.button !== 0) return
  resizeState.active = true
  resizeState.axes = axes
  resizeState.startX = event.clientX
  resizeState.startY = event.clientY
  resizeState.width = panelSize.width
  resizeState.height = panelSize.height
  ;(event.currentTarget as HTMLElement | null)?.setPointerCapture?.(event.pointerId)
  event.preventDefault()
}

function applyResize(event: PointerEvent) {
  const requested = {
    width:
      resizeState.axes.includes('x')
        ? resizeState.width + event.clientX - resizeState.startX
        : panelSize.width,
    height:
      resizeState.axes.includes('y')
        ? resizeState.height + event.clientY - resizeState.startY
        : panelSize.height,
  }
  applyClampedSize(requested)
  panelPosition.left = Math.min(
    panelPosition.left,
    window.innerWidth - panelSize.width - FLOATING_AGENT_SAFE_MARGIN,
  )
  panelPosition.top = Math.min(
    panelPosition.top,
    window.innerHeight - panelSize.height - FLOATING_AGENT_SAFE_MARGIN,
  )
}

function onPointerMove(event: PointerEvent) {
  if (!resizeState.active) return
  pendingResizeEvent = event
  if (resizeFrame) return
  resizeFrame = requestAnimationFrame(() => {
    resizeFrame = 0
    if (pendingResizeEvent) applyResize(pendingResizeEvent)
  })
}

function onPointerUp() {
  if (!resizeState.active) return
  resizeState.active = false
  resizeState.axes = ''
  pendingResizeEvent = null
  saveSize()
  schedulePlacement()
}

watch(() => props.open, (open) => open && openPanel())
watch(
  () => [props.anchor.x, props.anchor.y, props.anchor.width, props.anchor.height],
  schedulePlacement,
)

onMounted(() => {
  loadSavedSize()
  window.addEventListener('keydown', onWindowKeydown)
  window.addEventListener('resize', onViewportChange)
  window.addEventListener('orientationchange', onViewportChange)
  window.addEventListener('pointermove', onPointerMove)
  window.addEventListener('pointerup', onPointerUp)
  window.addEventListener('pointercancel', onPointerUp)
  if (props.open) openPanel()
})

onBeforeUnmount(() => {
  cancelAnimationFrame(placementFrame)
  cancelAnimationFrame(resizeFrame)
  window.removeEventListener('keydown', onWindowKeydown)
  window.removeEventListener('resize', onViewportChange)
  window.removeEventListener('orientationchange', onViewportChange)
  window.removeEventListener('pointermove', onPointerMove)
  window.removeEventListener('pointerup', onPointerUp)
  window.removeEventListener('pointercancel', onPointerUp)
})
</script>

<template>
  <aside
    v-if="open"
    ref="panel"
    class="floating-agent-panel"
    :class="{ resizing: resizeState.active, mobile }"
    :style="{
      left: `${panelPosition.left}px`,
      top: `${panelPosition.top}px`,
      width: `${panelSize.width}px`,
      height: `${panelSize.height}px`,
    }"
    :data-side="placementSide"
    role="dialog"
    aria-modal="false"
    aria-label="物料助手"
    data-testid="floating-agent-panel"
  >
    <header class="panel-header">
      <div><span>PENGKA MATERIAL BRAIN</span><b>物料大脑</b><small>找物料、查库存、看库位</small></div>
      <button ref="closeButton" type="button" class="panel-close" aria-label="关闭物料助手" @click="requestClose"><el-icon><Close /></el-icon></button>
    </header>
    <FloatingAgentConversation />
    <template v-if="!mobile">
      <button type="button" class="resize-handle resize-width" aria-label="调整物料助手宽度" data-testid="floating-resize-width" @pointerdown="onResizeStart($event, 'x')"></button>
      <button type="button" class="resize-handle resize-height" aria-label="调整物料助手高度" data-testid="floating-resize-height" @pointerdown="onResizeStart($event, 'y')"></button>
      <button type="button" class="resize-handle resize-corner" aria-label="调整物料助手宽度和高度" data-testid="floating-resize-corner" @pointerdown="onResizeStart($event, 'xy')"></button>
    </template>
  </aside>
</template>

<style scoped>
.floating-agent-panel{position:fixed;z-index:46;display:grid;min-width:0;min-height:0;grid-template-rows:auto minmax(0,1fr);overflow:hidden;border:1px solid #cbdbe7;border-radius:20px;background:#f4f7fa;box-shadow:0 24px 70px #102d4e42}.floating-agent-panel.resizing{user-select:none}.panel-header{display:flex;min-height:74px;align-items:center;justify-content:space-between;gap:16px;padding:13px 14px 13px 18px;border-bottom:1px solid #d7e2eb;background:linear-gradient(135deg,#fff,#edf6fd)}.panel-header>div{display:grid;min-width:0}.panel-header span{color:#3479bd;font-size:12px;font-weight:800;letter-spacing:1px}.panel-header b{color:#213f5e;font-size:18px}.panel-header small{color:#667f94;font-size:13px}.panel-close{display:grid;flex:0 0 44px;width:44px;height:44px;place-items:center;border:0;border-radius:12px;background:transparent;color:#526d83;cursor:pointer;font-size:20px}.panel-close:hover,.panel-close:focus-visible{background:#e5eff6;color:#235f8d;outline:0}.resize-handle{position:absolute;z-index:3;width:44px;height:44px;padding:0;border:0;background:transparent;touch-action:none}.resize-width{top:50%;right:0;cursor:ew-resize;transform:translateY(-50%)}.resize-height{bottom:0;left:0;cursor:ns-resize}.resize-corner{right:0;bottom:0;cursor:nwse-resize}.resize-corner::after{position:absolute;right:9px;bottom:9px;width:10px;height:10px;border-right:2px solid #7c99af;border-bottom:2px solid #7c99af;content:''}.resize-handle:focus-visible{border-radius:10px;outline:3px solid #58a9e6;outline-offset:-5px}.floating-agent-panel:not(.mobile) :deep(.conversation-composer){padding-right:56px;padding-left:56px}.floating-agent-panel.mobile{border-radius:18px}.floating-agent-panel.mobile .panel-header{min-height:68px}@media(max-width:719px){.panel-header{padding-left:15px}.panel-header small{display:none}}
</style>
