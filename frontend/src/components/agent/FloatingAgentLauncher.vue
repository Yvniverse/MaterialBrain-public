<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import robotImage from '../../embodied/assets/materialbrain-bot-v3.png'

const props = defineProps<{ workspace?: boolean; overview?: boolean }>()

const emit = defineEmits<{
  open: []
  positionChange: [anchor: { x: number; y: number; width: number; height: number }]
}>()

const STORAGE_KEY = 'materialbrain:floating-agent-position:v1'
const FALLBACK_WIDTH = 100
const FALLBACK_HEIGHT = 100
const EDGE_GAP = 8
const launcher = ref<HTMLButtonElement | null>(null)
const position = reactive({ x: 0, y: EDGE_GAP })
const drag = reactive({ active: false, moved: false, startX: 0, startY: 0, x: 0, y: 0 })
let anchorFrame = 0
let hasSavedPosition = false

function bounds() {
  const rect = launcher.value?.getBoundingClientRect()
  return {
    width: rect?.width || FALLBACK_WIDTH,
    height: rect?.height || FALLBACK_HEIGHT,
  }
}

function clamp(x: number, y: number) {
  const size = bounds()
  position.x = Math.min(
    Math.max(EDGE_GAP, x),
    Math.max(EDGE_GAP, window.innerWidth - size.width - EDGE_GAP),
  )
  position.y = Math.min(
    Math.max(EDGE_GAP, y),
    Math.max(EDGE_GAP, window.innerHeight - size.height - EDGE_GAP),
  )
  scheduleAnchorEmit()
}

function scheduleAnchorEmit() {
  cancelAnimationFrame(anchorFrame)
  anchorFrame = requestAnimationFrame(() => {
    const rect = launcher.value?.getBoundingClientRect()
    if (!rect) return
    emit('positionChange', {
      x: rect.left,
      y: rect.top,
      width: rect.width,
      height: rect.height,
    })
  })
}

function savePosition() {
  hasSavedPosition = true
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(position))
  } catch {
    // Storage can be unavailable in hardened/private browser contexts.
  }
}

function loadPosition() {
  try {
    const stored = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null') as {
      x?: number
      y?: number
    } | null
    if (stored && Number.isFinite(stored.x) && Number.isFinite(stored.y)) {
      hasSavedPosition = true
      clamp(Number(stored.x), Number(stored.y))
      return
    }
  } catch {
    // Invalid local state falls back to the bottom-right position.
  }
  placeDefault()
}

function placeDefault() {
  const size = bounds()
  const rightInset = props.workspace && window.innerWidth >= 1200 ? 340 : 22
  const mobile = window.innerWidth <= 600
  let top = window.innerHeight - size.height - (mobile ? 8 : 24)
  const welcome = mobile && props.overview ? document.querySelector('.g-home .g-welcome') : null
  if (welcome) top = welcome.getBoundingClientRect().top + 64
  clamp(window.innerWidth - size.width - rightInset, top)
}

function onPointerDown(event: PointerEvent) {
  if (event.button !== 0) return
  drag.active = true
  drag.moved = false
  drag.startX = event.clientX
  drag.startY = event.clientY
  drag.x = position.x
  drag.y = position.y
  launcher.value?.setPointerCapture?.(event.pointerId)
  event.preventDefault()
}

function onPointerMove(event: PointerEvent) {
  if (!drag.active) return
  const dx = event.clientX - drag.startX
  const dy = event.clientY - drag.startY
  if (Math.hypot(dx, dy) >= 5) drag.moved = true
  clamp(drag.x + dx, drag.y + dy)
}

function onPointerUp() {
  if (!drag.active) return
  drag.active = false
  if (drag.moved) savePosition()
}

function onClick() {
  if (drag.moved) {
    drag.moved = false
    return
  }
  emit('open')
}

function onKeydown(event: KeyboardEvent) {
  const delta = event.shiftKey ? 24 : 8
  const offsets: Record<string, [number, number]> = {
    ArrowLeft: [-delta, 0],
    ArrowRight: [delta, 0],
    ArrowUp: [0, -delta],
    ArrowDown: [0, delta],
  }
  const offset = offsets[event.key]
  if (!offset) return
  event.preventDefault()
  clamp(position.x + offset[0], position.y + offset[1])
  savePosition()
}

function onResize() {
  if (hasSavedPosition) clamp(position.x, position.y)
  else placeDefault()
}

watch([() => props.workspace, () => props.overview], () => {
  if (!hasSavedPosition) void nextTick(placeDefault)
})

onMounted(async () => {
  await nextTick()
  loadPosition()
  window.addEventListener('pointermove', onPointerMove)
  window.addEventListener('pointerup', onPointerUp)
  window.addEventListener('pointercancel', onPointerUp)
  window.addEventListener('resize', onResize)
  window.addEventListener('orientationchange', onResize)
})

onBeforeUnmount(() => {
  cancelAnimationFrame(anchorFrame)
  window.removeEventListener('pointermove', onPointerMove)
  window.removeEventListener('pointerup', onPointerUp)
  window.removeEventListener('pointercancel', onPointerUp)
  window.removeEventListener('resize', onResize)
  window.removeEventListener('orientationchange', onResize)
})
</script>

<template>
  <button
    ref="launcher"
    type="button"
    class="floating-agent-launcher"
    :class="{ dragging: drag.active, workspace }"
    :style="{ left: `${position.x}px`, top: `${position.y}px` }"
    aria-label="打开物料大脑；可拖动位置"
    title="点击打开物料大脑；拖动可调整位置"
    data-testid="floating-agent-launcher"
    @pointerdown="onPointerDown"
    @click="onClick"
    @keydown="onKeydown"
  >
    <img :src="robotImage" alt="MaterialBrain 悬浮助手" draggable="false" />
  </button>
</template>

<style scoped>
.floating-agent-launcher {
  position: fixed;
  z-index: 47;
  display: grid;
  width: 100px;
  height: 100px;
  place-items: center;
  padding: 0;
  border: 0;
  background: transparent;
  cursor: grab;
  touch-action: none;
  user-select: none;
  filter: drop-shadow(0 12px 14px #17395e38);
}
.floating-agent-launcher.dragging {
  cursor: grabbing;
}
.floating-agent-launcher.workspace {
  z-index: 10;
}
.floating-agent-launcher img {
  width: 100px;
  height: 100px;
  object-fit: contain;
  pointer-events: none;
  transition: transform 0.18s ease;
}
.floating-agent-launcher:hover:not(.dragging) img {
  transform: scale(1.04);
}
.floating-agent-launcher:focus-visible {
  outline: 3px solid var(--g-line);
  outline-offset: 4px;
  border-radius: 18px;
}
@keyframes robot-float {
  0%,
  100% {
    transform: translateY(0);
  }
  50% {
    transform: translateY(-7px);
  }
}
@media (prefers-reduced-motion: reduce) {
  .floating-agent-launcher img {
    animation: none;
  }
}
@media (max-width: 600px) {
  .floating-agent-launcher {
    width: 72px;
    height: 72px;
  }
  .floating-agent-launcher img {
    width: 72px;
    height: 72px;
  }
}
</style>
