<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh, Search, Setting } from '@element-plus/icons-vue'
import { useRoute, useRouter } from 'vue-router'
import OrganizerBox3D, {
  type OrganizerModule,
  type OrganizerLocation,
} from '../components/locations/OrganizerBox3D.vue'
import DrawerRack100 from '../components/locations/DrawerRack100.vue'
import ShelfRack6 from '../components/locations/ShelfRack6.vue'
import WarehouseMapAdmin from '../components/locations/WarehouseMapAdmin.vue'
import { api } from '../api/client'
import { useAuthStore } from '../stores/auth'
import type { Material, Page } from '../types'
import { formatQuantity } from '../utils/format'
import { formatBusinessText } from '../utils/businessCopy'

interface LocationItem extends OrganizerLocation {
  material_count: number
  quantity: string
  reserved_quantity: string
}

interface LocationNode extends LocationItem {
  children: LocationNode[]
}

interface OrganizerResponse {
  organizer: OrganizerLocation
  bins: OrganizerLocation[]
}

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const canManageLocations = computed(() => auth.can('location:manage'))

const locations = ref<LocationItem[]>([])
const boxMaterials = ref<Material[]>([])
const loading = ref(false)
const materialsLoading = ref(false)
const selectedWarehouseId = ref<number | null>(null)
const selectedBoxId = ref<number | null>(null)
const selectedBinId = ref<number | null>(null)
const selectedShelfBoxId = ref<number | null>(null)
const focusedLocationId = ref<number | null>(null)
const boxOpen = ref(false)
const boxClosing = ref(false)
const overviewEntering = ref(false)
const search = ref('')
const structureDrawer = ref(false)
const mapPlannerDrawer = ref(false)
const organizerDialog = ref(false)
const organizerSaving = ref(false)
const deletingOrganizerId = ref<number | null>(null)
const layoutSaving = ref(false)
const locationDialog = ref(false)
const locationSaving = ref(false)
const editingLocationId = ref<number | null>(null)
const shelfBoxDialog = ref(false)
const shelfBoxSaving = ref(false)
const shelfItemSaving = ref(false)
const shelfItemSavingId = ref<number | null>(null)
const deletingShelfBoxId = ref<number | null>(null)
const deletingShelfItemId = ref<number | null>(null)
let closeAnimationTimer: number | null = null
let overviewAnimationTimer: number | null = null
const contentSaveTimers = new Map<number, number>()
const contentSaveRevisions = new Map<number, number>()
const contentSaveQueues = new Map<number, Promise<void>>()
const contentSaveStates = ref<Record<number, 'idle' | 'pending' | 'saving' | 'saved' | 'error'>>({})

const binContentForm = reactive({
  material_name: '',
  quantity: null as number | null,
  notes: '',
})

interface ShelfItemDraft {
  material_name: string
  quantity: number | null
  notes: string
}

const shelfBoxForm = reactive({
  shelf_id: null as number | null,
  name: '',
  notes: '',
})

const shelfItemForm = reactive<ShelfItemDraft>({
  material_name: '',
  quantity: null,
  notes: '',
})

const shelfItemDrafts = reactive<Record<number, ShelfItemDraft>>({})

const organizerForm = reactive({
  code: '',
  name: '',
  parent_id: null as number | null,
  manager: '',
  notes: '',
  is_active: true,
  organizer_style: 'standard_56' as
    'standard_56' | 'split_configurable' | 'drawer_rack_100' | 'shelf_rack_6',
  organizer_left_module: 'large' as OrganizerModule,
  organizer_right_module: 'small' as OrganizerModule,
})

const locationForm = reactive({
  code: '',
  name: '',
  parent_id: null as number | null,
  type: 'area',
  manager: '',
  notes: '',
  is_active: true,
})

const typeLabels: Record<string, string> = {
  warehouse: '仓库',
  area: '区域',
  box: '元件盒',
  cabinet: '柜子',
  shelf: '层架',
  container: '物料箱',
  drawer: '抽屉',
  bin: '小格',
  temporary: '临时区',
  scrap: '报废区',
}

const warehouses = computed(() =>
  locations.value.filter(
    (item) => item.type === 'warehouse' || (item.parent_id === null && item.type === 'area'),
  ),
)

const selectedWarehouse = computed(
  () => warehouses.value.find((item) => item.id === selectedWarehouseId.value) ?? null,
)

const allBoxes = computed(() => locations.value.filter((item) => item.type === 'box'))

const warehouseBoxes = computed(() => {
  if (!selectedWarehouseId.value) return []
  return allBoxes.value.filter((item) =>
    locationBelongsTo(item.id, selectedWarehouseId.value as number),
  )
})

const boxes = computed(() =>
  warehouseBoxes.value.filter((item) => {
    const term = search.value.trim().toLowerCase()
    return (
      !term ||
      item.name.toLowerCase().includes(term) ||
      item.code.toLowerCase().includes(term) ||
      item.notes.toLowerCase().includes(term)
    )
  }),
)

const boxSummaries = computed(() => {
  const childrenByParent = new Map<number, LocationItem[]>()
  locations.value.forEach((item) => {
    if (!item.parent_id) return
    const children = childrenByParent.get(item.parent_id) ?? []
    children.push(item)
    childrenByParent.set(item.parent_id, children)
  })

  const summaries = new Map<
    number,
    {
      materials: number
      quantity: number
      occupied: number
      containers: number
      occupiedShelves: number
    }
  >()
  allBoxes.value.forEach((box) => {
    const pending = [box]
    const visited = new Set<number>()
    let materials = 0
    let quantity = 0
    let occupied = 0
    let containers = 0
    let occupiedShelves = 0
    while (pending.length) {
      const item = pending.pop()
      if (!item || visited.has(item.id)) continue
      visited.add(item.id)
      const hasDirectContent = item.type === 'bin' && Boolean(item.bin_material_name?.trim())
      materials += hasDirectContent ? 1 : item.material_count
      quantity += hasDirectContent ? Number(item.bin_quantity ?? 0) : Number(item.quantity)
      if (item.type === 'bin' && (hasDirectContent || item.material_count > 0)) occupied += 1
      if (item.type === 'container') containers += 1
      if (
        item.type === 'shelf' &&
        (childrenByParent.get(item.id) ?? []).some((child) => child.type === 'container')
      ) {
        occupiedShelves += 1
      }
      pending.push(...(childrenByParent.get(item.id) ?? []))
    }
    summaries.set(box.id, { materials, quantity, occupied, containers, occupiedShelves })
  })
  return summaries
})

const warehouseSummaries = computed(() => {
  const summaries = new Map<
    number,
    {
      organizers: number
      materials: number
      quantity: number
      occupied: number
      componentBoxes: number
      drawerRacks: number
      shelfRacks: number
    }
  >()
  warehouses.value.forEach((warehouse) => {
    const organizers = allBoxes.value.filter((item) => locationBelongsTo(item.id, warehouse.id))
    summaries.set(warehouse.id, {
      organizers: organizers.length,
      materials: organizers.reduce((total, item) => total + boxSummary(item).materials, 0),
      quantity: organizers.reduce((total, item) => total + boxSummary(item).quantity, 0),
      occupied: organizers.reduce((total, item) => total + boxSummary(item).occupied, 0),
      componentBoxes: organizers.filter((item) => !isDrawerRack(item) && !isShelfRack(item)).length,
      drawerRacks: organizers.filter((item) => isDrawerRack(item)).length,
      shelfRacks: organizers.filter((item) => isShelfRack(item)).length,
    })
  })
  return summaries
})

const selectedBox = computed(
  () => locations.value.find((item) => item.id === selectedBoxId.value) ?? null,
)

function moduleCapacity(module: OrganizerModule | null | undefined): number {
  return module === 'large' ? 8 : 28
}

function boxCapacity(box: OrganizerLocation | null): number {
  if (!box) return 56
  if (box.organizer_style === 'drawer_rack_100') return 100
  if (box.organizer_style === 'shelf_rack_6') return 6
  if (box.organizer_style !== 'split_configurable') return 56
  return moduleCapacity(box.organizer_left_module) + moduleCapacity(box.organizer_right_module)
}

function moduleSlotNames(
  side: 'left' | 'right',
  module: OrganizerModule | null | undefined,
): string[] {
  const resolvedModule = module || 'small'
  const sideCode = side === 'left' ? 'L' : 'R'
  const moduleCode = resolvedModule === 'small' ? 'S' : 'L'
  return Array.from(
    { length: moduleCapacity(resolvedModule) },
    (_, index) => `${sideCode}-${moduleCode}${String(index + 1).padStart(2, '0')}`,
  )
}

function boxBinNames(box: OrganizerLocation | null): Set<string> | null {
  if (!box || box.organizer_style !== 'split_configurable') return null
  return new Set([
    ...moduleSlotNames('left', box.organizer_left_module),
    ...moduleSlotNames('right', box.organizer_right_module),
  ])
}

const selectedBins = computed(() => {
  const allowedNames = boxBinNames(selectedBox.value)
  return locations.value
    .filter(
      (item) =>
        item.parent_id === selectedBoxId.value &&
        item.type === 'bin' &&
        (!allowedNames || allowedNames.has(item.name)),
    )
    .sort((a, b) => a.name.localeCompare(b.name, 'zh-CN', { numeric: true }))
})

const selectedShelves = computed(() =>
  locations.value
    .filter((item) => item.parent_id === selectedBoxId.value && item.type === 'shelf')
    .sort((a, b) => a.name.localeCompare(b.name, 'zh-CN', { numeric: true })),
)

const selectedShelfIds = computed(() => new Set(selectedShelves.value.map((shelf) => shelf.id)))

const shelfBoxFormShelf = computed(
  () => selectedShelves.value.find((shelf) => shelf.id === shelfBoxForm.shelf_id) ?? null,
)

const selectedShelfBoxes = computed(() =>
  locations.value
    .filter((item) => item.type === 'container' && selectedShelfIds.value.has(item.parent_id ?? -1))
    .sort((a, b) => a.name.localeCompare(b.name, 'zh-CN', { numeric: true })),
)

const selectedShelfBoxIds = computed(() => new Set(selectedShelfBoxes.value.map((box) => box.id)))

const selectedShelfItems = computed(() =>
  locations.value
    .filter((item) => item.type === 'bin' && selectedShelfBoxIds.value.has(item.parent_id ?? -1))
    .sort((a, b) => a.name.localeCompare(b.name, 'zh-CN', { numeric: true })),
)

const selectedShelfBox = computed(
  () => selectedShelfBoxes.value.find((box) => box.id === selectedShelfBoxId.value) ?? null,
)

const selectedShelfBoxItems = computed(() =>
  selectedShelfItems.value.filter((item) => item.parent_id === selectedShelfBoxId.value),
)

const occupiedShelfCount = computed(
  () =>
    selectedShelves.value.filter((shelf) =>
      selectedShelfBoxes.value.some((box) => box.parent_id === shelf.id),
    ).length,
)

const selectedBin = computed(
  () => selectedBins.value.find((item) => item.id === selectedBinId.value) ?? null,
)

const selectedBinMaterials = computed(() =>
  boxMaterials.value.filter((item) => item.location_id === selectedBinId.value),
)

const unassignedBoxMaterials = computed(() =>
  boxMaterials.value.filter((item) => item.location_id === selectedBoxId.value),
)

const occupiedBinCount = computed(
  () =>
    selectedBins.value.filter(
      (bin) =>
        Boolean(bin.bin_material_name?.trim()) ||
        boxMaterials.value.some((material) => material.location_id === bin.id),
    ).length,
)

const selectedQuantity = computed(() => {
  const directBinIds = new Set(
    selectedBins.value.filter((bin) => Boolean(bin.bin_material_name?.trim())).map((bin) => bin.id),
  )
  const directQuantity = selectedBins.value.reduce(
    (total, bin) => total + (directBinIds.has(bin.id) ? Number(bin.bin_quantity ?? 0) : 0),
    0,
  )
  const legacyQuantity = boxMaterials.value.reduce(
    (total, item) =>
      total + (item.location_id && directBinIds.has(item.location_id) ? 0 : Number(item.quantity)),
    0,
  )
  const shelfQuantity = selectedShelfItems.value.reduce(
    (total, item) => total + Number(item.bin_quantity ?? 0),
    0,
  )
  return directQuantity + legacyQuantity + shelfQuantity
})

const selectedBinHasDirectContent = computed(() =>
  Boolean(selectedBin.value?.bin_material_name?.trim()),
)

const selectedBinHasAnyContent = computed(
  () => selectedBinHasDirectContent.value || selectedBinMaterials.value.length > 0,
)

const selectedBinSaveState = computed(() =>
  selectedBinId.value ? contentSaveStates.value[selectedBinId.value] || 'idle' : 'idle',
)

const selectedBinSaveText = computed(() => {
  if (!binContentForm.material_name.trim()) return '填写物料名称后自动保存'
  const labels = {
    idle: '修改后自动保存',
    pending: '等待自动保存…',
    saving: '正在保存…',
    saved: '已自动保存',
    error: '保存失败，请继续修改后重试',
  }
  return labels[selectedBinSaveState.value]
})

const parentOptions = computed(() =>
  locations.value.filter(
    (item) => item.type !== 'bin' && item.type !== 'box' && item.id !== editingLocationId.value,
  ),
)

const allParentOptions = computed(() => {
  const blocked = new Set<number>()
  if (editingLocationId.value) {
    blocked.add(editingLocationId.value)
    collectDescendantIds(editingLocationId.value).forEach((id) => blocked.add(id))
  }
  return locations.value.filter((item) => !blocked.has(item.id))
})

const locationTree = computed<LocationNode[]>(() => {
  const nodes = new Map<number, LocationNode>()
  locations.value.forEach((item) => nodes.set(item.id, { ...item, children: [] }))
  const roots: LocationNode[] = []
  nodes.forEach((node) => {
    const parent = node.parent_id ? nodes.get(node.parent_id) : undefined
    if (parent) parent.children.push(node)
    else roots.push(node)
  })
  const sortNodes = (items: LocationNode[]) => {
    items.sort((a, b) => {
      if (a.type === 'box' && b.type !== 'box') return -1
      if (b.type === 'box' && a.type !== 'box') return 1
      return a.name.localeCompare(b.name, 'zh-CN', { numeric: true })
    })
    items.forEach((item) => sortNodes(item.children))
  }
  sortNodes(roots)
  return roots
})

function locationBelongsTo(locationId: number, ancestorId: number): boolean {
  const visited = new Set<number>()
  let current = locations.value.find((item) => item.id === locationId)
  while (current && !visited.has(current.id)) {
    if (current.id === ancestorId) return true
    visited.add(current.id)
    current = current.parent_id
      ? locations.value.find((item) => item.id === current?.parent_id)
      : undefined
  }
  return false
}

function warehouseSummary(warehouse: LocationItem) {
  return (
    warehouseSummaries.value.get(warehouse.id) ?? {
      organizers: 0,
      materials: 0,
      quantity: 0,
      occupied: 0,
      componentBoxes: 0,
      drawerRacks: 0,
      shelfRacks: 0,
    }
  )
}

function collectDescendantIds(locationId: number): number[] {
  const result: number[] = []
  const pending = [locationId]
  while (pending.length) {
    const current = pending.pop()
    if (!current) continue
    locations.value
      .filter((item) => item.parent_id === current && !result.includes(item.id))
      .forEach((item) => {
        result.push(item.id)
        pending.push(item.id)
      })
  }
  return result
}

function boxSummary(box: LocationItem) {
  return (
    boxSummaries.value.get(box.id) ?? {
      materials: 0,
      quantity: 0,
      occupied: 0,
      containers: 0,
      occupiedShelves: 0,
    }
  )
}

function boxLayoutLabel(box: OrganizerLocation): string {
  if (box.organizer_style === 'drawer_rack_100') return '100 抽货架 · 20 行 × 5 列'
  if (box.organizer_style === 'shelf_rack_6') return '六层箱式货架 · 每层可放多个箱子'
  if (box.organizer_style !== 'split_configurable') return '标准 56 格'
  const moduleLabel = (module: OrganizerModule | null | undefined) =>
    module === 'large' ? '大格' : '小格'
  return `左${moduleLabel(box.organizer_left_module)} · 右${moduleLabel(box.organizer_right_module)}`
}

function miniSections(box: OrganizerLocation) {
  if (box.organizer_style !== 'split_configurable') return []
  return [
    {
      side: 'left',
      module: box.organizer_left_module || 'small',
      count: moduleCapacity(box.organizer_left_module),
    },
    {
      side: 'right',
      module: box.organizer_right_module || 'small',
      count: moduleCapacity(box.organizer_right_module),
    },
  ] as const
}

const organizerFormCapacity = computed(() =>
  organizerForm.organizer_style === 'drawer_rack_100' ||
  organizerForm.organizer_style === 'shelf_rack_6'
    ? organizerForm.organizer_style === 'drawer_rack_100'
      ? 100
      : 6
    : organizerForm.organizer_style === 'standard_56'
      ? 56
      : moduleCapacity(organizerForm.organizer_left_module) +
        moduleCapacity(organizerForm.organizer_right_module),
)

function isDrawerRack(box: OrganizerLocation | null | undefined): boolean {
  return box?.organizer_style === 'drawer_rack_100'
}

function isShelfRack(box: OrganizerLocation | null | undefined): boolean {
  return box?.organizer_style === 'shelf_rack_6'
}

function organizerKindLabel(box: OrganizerLocation | null | undefined): string {
  if (isDrawerRack(box)) return '100 抽货架'
  if (isShelfRack(box)) return '六层箱式货架'
  return '大物料盒'
}

function unitLabel(box: OrganizerLocation | null | undefined): string {
  if (isDrawerRack(box)) return '抽屉'
  if (isShelfRack(box)) return '层'
  return '小盒'
}

function locationTypeLabel(item: OrganizerLocation): string {
  if (isDrawerRack(item)) return '100 抽货架'
  if (isShelfRack(item)) return '六层箱式货架'
  return typeLabels[item.type] || item.type
}

function syncBinContentForm() {
  const bin = selectedBin.value
  if (!bin) {
    Object.assign(binContentForm, { material_name: '', quantity: null, notes: '' })
    return
  }
  const legacyItems = selectedBinMaterials.value
  const hasDirectContent = Boolean(bin.bin_material_name?.trim())
  Object.assign(binContentForm, {
    material_name: hasDirectContent ? bin.bin_material_name || '' : legacyItems[0]?.name || '',
    quantity: hasDirectContent
      ? (bin.bin_quantity ?? null)
      : legacyItems.length
        ? legacyItems.reduce((total, item) => total + Number(item.quantity), 0)
        : null,
    notes: hasDirectContent ? bin.bin_content_notes || '' : legacyItems[0]?.notes || '',
  })
  contentSaveStates.value[bin.id] = hasDirectContent ? 'saved' : 'idle'
}

function syncShelfItemDrafts() {
  const activeIds = new Set(selectedShelfItems.value.map((item) => item.id))
  Object.keys(shelfItemDrafts).forEach((id) => {
    if (!activeIds.has(Number(id))) delete shelfItemDrafts[Number(id)]
  })
  selectedShelfItems.value.forEach((item) => {
    shelfItemDrafts[item.id] = {
      material_name: item.bin_material_name || '',
      quantity: item.bin_quantity ?? null,
      notes: item.bin_content_notes || '',
    }
  })
}

function updateLocationContent(updated: OrganizerLocation) {
  const target = locations.value.find((item) => item.id === updated.id)
  if (target) Object.assign(target, updated)
}

async function persistBinContent(
  binId: number,
  payload: { material_name: string; quantity: number | null; notes: string },
  revision: number,
) {
  if (contentSaveRevisions.get(binId) === revision) {
    contentSaveStates.value[binId] = 'saving'
  }
  try {
    const updated = (await api.put<OrganizerLocation>(`/locations/${binId}/content`, payload)).data
    updateLocationContent(updated)
    if (contentSaveRevisions.get(binId) === revision) {
      contentSaveStates.value[binId] = 'saved'
    }
  } catch {
    if (contentSaveRevisions.get(binId) === revision) {
      contentSaveStates.value[binId] = 'error'
    }
  }
}

function scheduleBinContentSave() {
  const binId = selectedBinId.value
  if (!binId) return
  const currentTimer = contentSaveTimers.get(binId)
  if (currentTimer !== undefined) window.clearTimeout(currentTimer)
  const revision = (contentSaveRevisions.get(binId) || 0) + 1
  contentSaveRevisions.set(binId, revision)

  const materialName = binContentForm.material_name.trim()
  if (!materialName) {
    contentSaveStates.value[binId] = 'idle'
    contentSaveTimers.delete(binId)
    return
  }

  const quantity =
    binContentForm.quantity === null || binContentForm.quantity === undefined
      ? null
      : Math.max(0, Math.trunc(Number(binContentForm.quantity)))
  const payload = {
    material_name: materialName,
    quantity,
    notes: binContentForm.notes,
  }
  contentSaveStates.value[binId] = 'pending'
  const timer = window.setTimeout(() => {
    contentSaveTimers.delete(binId)
    const previous = contentSaveQueues.get(binId) || Promise.resolve()
    const next = previous.then(() => persistBinContent(binId, payload, revision))
    contentSaveQueues.set(binId, next)
    void next.finally(() => {
      if (contentSaveQueues.get(binId) === next) contentSaveQueues.delete(binId)
    })
  }, 650)
  contentSaveTimers.set(binId, timer)
}

async function clearSelectedBinContent() {
  const bin = selectedBin.value
  if (!bin || !selectedBinHasAnyContent.value) return
  const legacyNames = selectedBinMaterials.value.map((item) => item.mpn || item.name)
  const contentNames = [
    ...(bin.bin_material_name?.trim() ? [bin.bin_material_name.trim()] : []),
    ...legacyNames,
  ]
  const contentLabel =
    contentNames.length > 2
      ? `${contentNames.slice(0, 2).join('、')} 等 ${contentNames.length} 项`
      : contentNames.join('、')
  await ElMessageBox.confirm(
    `确认清空${unitLabel(selectedBox.value)} ${bin.name} 中的“${contentLabel}”？物料资料、库存数量和历史流水仍会保留，仅解除与该${unitLabel(selectedBox.value)}的关联。`,
    `清空${unitLabel(selectedBox.value)}内容`,
    { type: 'warning', confirmButtonText: '清空', cancelButtonText: '取消' },
  )
  const timer = contentSaveTimers.get(bin.id)
  if (timer !== undefined) {
    window.clearTimeout(timer)
    contentSaveTimers.delete(bin.id)
  }
  await contentSaveQueues.get(bin.id)
  const updated = (await api.delete<OrganizerLocation>(`/locations/${bin.id}/content`)).data
  updateLocationContent(updated)
  contentSaveStates.value[bin.id] = 'idle'
  await loadLocations()
  ElMessage.success(`${unitLabel(selectedBox.value)}内容已清空`)
}

function clearOrganizerInspection() {
  boxMaterials.value = []
  selectedBinId.value = null
  selectedShelfBoxId.value = null
  Object.keys(shelfItemDrafts).forEach((id) => delete shelfItemDrafts[Number(id)])
  syncBinContentForm()
}

async function loadLocations() {
  loading.value = true
  try {
    locations.value = (await api.get<LocationItem[]>('/locations')).data.map((item) => ({
      ...item,
      name: formatBusinessText(item.name),
      full_path: formatBusinessText(item.full_path),
      manager: formatBusinessText(item.manager),
      notes: formatBusinessText(item.notes),
      bin_content_notes: formatBusinessText(item.bin_content_notes),
    }))
    if (
      selectedWarehouseId.value &&
      !warehouses.value.some((item) => item.id === selectedWarehouseId.value)
    ) {
      selectedWarehouseId.value = null
    }
    if (
      selectedBoxId.value &&
      !locations.value.some((item) => item.id === selectedBoxId.value && item.type === 'box')
    ) {
      selectedBoxId.value = null
    }
    if (
      selectedBoxId.value &&
      selectedWarehouseId.value &&
      !locationBelongsTo(selectedBoxId.value, selectedWarehouseId.value)
    ) {
      selectedBoxId.value = null
      boxOpen.value = false
    }
    if (selectedBoxId.value && boxOpen.value) await loadBoxMaterials()
    else clearOrganizerInspection()
  } finally {
    loading.value = false
  }
}

async function loadBoxMaterials() {
  if (!selectedBoxId.value) {
    boxMaterials.value = []
    syncBinContentForm()
    return
  }
  materialsLoading.value = true
  try {
    const items: Material[] = []
    let page = 1
    let total = 0
    do {
      const response = (
        await api.get<Page<Material>>('/materials', {
          params: {
            location_id: selectedBoxId.value,
            include_location_descendants: true,
            page,
            page_size: 200,
            sort: 'name',
            order: 'asc',
          },
        })
      ).data
      items.push(...response.items)
      total = response.total
      page += 1
    } while (items.length < total)
    boxMaterials.value = items
    if (
      selectedBinId.value &&
      !selectedBins.value.some((item) => item.id === selectedBinId.value)
    ) {
      selectedBinId.value = null
    }
    if (!selectedBinId.value) {
      const occupied = selectedBins.value.find(
        (bin) =>
          Boolean(bin.bin_material_name?.trim()) ||
          items.some((material) => material.location_id === bin.id),
      )
      selectedBinId.value = occupied?.id ?? selectedBins.value[0]?.id ?? null
    }
    if (isShelfRack(selectedBox.value)) {
      if (
        selectedShelfBoxId.value &&
        !selectedShelfBoxes.value.some((box) => box.id === selectedShelfBoxId.value)
      ) {
        selectedShelfBoxId.value = null
      }
      selectedShelfBoxId.value ??= selectedShelfBoxes.value[0]?.id ?? null
    } else {
      selectedShelfBoxId.value = null
    }
    syncBinContentForm()
    syncShelfItemDrafts()
  } finally {
    materialsLoading.value = false
  }
}

async function selectBox(box: LocationItem) {
  if (boxClosing.value) return
  if (overviewAnimationTimer !== null) {
    window.clearTimeout(overviewAnimationTimer)
    overviewAnimationTimer = null
  }
  overviewEntering.value = false
  if (!selectedWarehouseId.value) {
    selectedWarehouseId.value =
      warehouses.value.find((item) => locationBelongsTo(box.id, item.id))?.id ?? null
  }
  selectedBoxId.value = box.id
  selectedBinId.value = null
  selectedShelfBoxId.value = null
  boxOpen.value = false
  await loadBoxMaterials()
  await nextTick()
  boxOpen.value = true
}

function ancestorOfType(location: LocationItem, type: string): LocationItem | null {
  const visited = new Set<number>()
  let current: LocationItem | undefined = location
  while (current && !visited.has(current.id)) {
    visited.add(current.id)
    if (current.type === type) return current
    current = current.parent_id
      ? locations.value.find((item) => item.id === current?.parent_id)
      : undefined
  }
  return null
}

async function focusLocationFromRoute() {
  const targetId = Number(route.query.focus)
  if (!Number.isInteger(targetId) || targetId <= 0) {
    focusedLocationId.value = null
    return
  }
  const target = locations.value.find((item) => item.id === targetId)
  if (!target) {
    ElMessage.warning('Deep-link 指定的库位不存在或已删除')
    return
  }
  focusedLocationId.value = target.id
  const organizer = ancestorOfType(target, 'box')
  const warehouse = warehouses.value.find((item) => locationBelongsTo(target.id, item.id))
  selectedWarehouseId.value = warehouse?.id ?? null
  if (!organizer) {
    structureDrawer.value = true
    return
  }

  await selectBox(organizer)
  if (isShelfRack(organizer)) {
    if (target.type === 'container') selectedShelfBoxId.value = target.id
    else if (target.type === 'bin') {
      const parent = locations.value.find((item) => item.id === target.parent_id)
      if (parent?.type === 'container') selectedShelfBoxId.value = parent.id
    }
  } else if (target.type === 'bin' && target.parent_id === organizer.id) {
    selectedBinId.value = target.id
    syncBinContentForm()
  }
  await nextTick()
  document.querySelector(`[data-location-id="${target.id}"]`)?.scrollIntoView({
    block: 'center',
    inline: 'center',
    behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',
  })
}

function showBoxOverview(returnToAllWarehouses = false) {
  if (!selectedBox.value || boxClosing.value) return
  boxClosing.value = true
  if (closeAnimationTimer !== null) window.clearTimeout(closeAnimationTimer)
  const closeDuration =
    isDrawerRack(selectedBox.value) || isShelfRack(selectedBox.value) ? 220 : 320
  closeAnimationTimer = window.setTimeout(() => {
    closeAnimationTimer = null
    boxOpen.value = false
    clearOrganizerInspection()
    if (returnToAllWarehouses) {
      selectedWarehouseId.value = null
      selectedBoxId.value = null
    }
    boxClosing.value = false
    overviewEntering.value = true
    overviewAnimationTimer = window.setTimeout(() => {
      overviewEntering.value = false
      overviewAnimationTimer = null
    }, 460)
  }, closeDuration)
}

function selectBin(bin: OrganizerLocation) {
  selectedBinId.value = bin.id
  syncBinContentForm()
}

function requestLocationsOverview() {
  search.value = ''
  if (boxOpen.value && selectedBox.value) showBoxOverview(true)
  else {
    boxOpen.value = false
    selectedWarehouseId.value = null
    selectedBoxId.value = null
    clearOrganizerInspection()
  }
}

function selectWarehouse(warehouse: LocationItem) {
  if (boxClosing.value) return
  search.value = ''
  selectedWarehouseId.value = warehouse.id
  selectedBoxId.value = null
  boxOpen.value = false
  overviewEntering.value = false
  clearOrganizerInspection()
}

function showWarehouseOverview() {
  search.value = ''
  selectedWarehouseId.value = null
  selectedBoxId.value = null
  boxOpen.value = false
  overviewEntering.value = false
  clearOrganizerInspection()
}

function selectShelfStorageBox(box: OrganizerLocation) {
  selectedShelfBoxId.value = box.id
  syncShelfItemDrafts()
}

function openWarehouseMapPlanner() {
  if (!warehouses.value.length) {
    ElMessage.warning('请先创建仓库根库位。')
    return
  }
  if (!selectedWarehouseId.value) selectedWarehouseId.value = warehouses.value[0].id
  mapPlannerDrawer.value = true
}

function openOrganizerDialog() {
  Object.assign(organizerForm, {
    code: `BOX-${String(locations.value.filter((item) => item.type === 'box').length + 1).padStart(2, '0')}`,
    name: '',
    parent_id: selectedWarehouseId.value ?? warehouses.value[0]?.id ?? null,
    manager: '',
    notes: '',
    is_active: true,
    organizer_style: 'standard_56',
    organizer_left_module: 'large',
    organizer_right_module: 'small',
  })
  organizerDialog.value = true
}

function selectOrganizerStyle(
  style: 'standard_56' | 'split_configurable' | 'drawer_rack_100' | 'shelf_rack_6',
) {
  const boxCount = locations.value.filter((item) => item.type === 'box').length + 1
  const rackStyle = style === 'drawer_rack_100' || style === 'shelf_rack_6'
  if (rackStyle && /^BOX-\d+$/i.test(organizerForm.code)) {
    organizerForm.code = `RACK-${String(boxCount).padStart(2, '0')}`
  } else if (!rackStyle && /^RACK-\d+$/i.test(organizerForm.code)) {
    organizerForm.code = `BOX-${String(boxCount).padStart(2, '0')}`
  }
  organizerForm.organizer_style = style
}

async function createOrganizer() {
  organizerSaving.value = true
  try {
    organizerForm.code = organizerForm.code.trim().toUpperCase()
    const result = (await api.post<OrganizerResponse>('/locations/organizers', organizerForm)).data
    organizerDialog.value = false
    ElMessage.success(
      `${organizerKindLabel(result.organizer)}与 ${result.bins.length} 个${unitLabel(result.organizer)}已创建`,
    )
    selectedWarehouseId.value =
      warehouses.value.find((item) => locationBelongsTo(result.organizer.id, item.id))?.id ??
      organizerForm.parent_id
    selectedBoxId.value = result.organizer.id
    selectedBinId.value = isShelfRack(result.organizer) ? null : (result.bins[0]?.id ?? null)
    selectedShelfBoxId.value = null
    boxOpen.value = true
    await loadLocations()
  } finally {
    organizerSaving.value = false
  }
}

async function changeOrganizerModule(side: 'left' | 'right', module: OrganizerModule) {
  if (!selectedBox.value || selectedBox.value.organizer_style !== 'split_configurable') return
  const left = side === 'left' ? module : selectedBox.value.organizer_left_module || 'small'
  const right = side === 'right' ? module : selectedBox.value.organizer_right_module || 'small'
  layoutSaving.value = true
  try {
    await api.put(`/locations/organizers/${selectedBox.value.id}/layout`, {
      organizer_left_module: left,
      organizer_right_module: right,
    })
    selectedBinId.value = null
    await loadLocations()
    ElMessage.success(
      `${side === 'left' ? '左半区' : '右半区'}已切换为${module === 'small' ? '小格模块' : '大格模块'}`,
    )
  } finally {
    layoutSaving.value = false
  }
}

function openShelfBoxDialog(shelf: OrganizerLocation) {
  Object.assign(shelfBoxForm, {
    shelf_id: shelf.id,
    name: '',
    notes: '',
  })
  shelfBoxDialog.value = true
}

async function createShelfStorageBox() {
  if (!selectedBox.value || !shelfBoxForm.shelf_id || !shelfBoxForm.name.trim()) return
  shelfBoxSaving.value = true
  try {
    const created = (
      await api.post<OrganizerLocation>(
        `/locations/shelf-racks/${selectedBox.value.id}/shelves/${shelfBoxForm.shelf_id}/boxes`,
        {
          name: shelfBoxForm.name.trim(),
          notes: shelfBoxForm.notes.trim(),
        },
      )
    ).data
    selectedShelfBoxId.value = created.id
    shelfBoxDialog.value = false
    await loadLocations()
    ElMessage.success(`箱子“${created.name}”已放到货架`)
  } finally {
    shelfBoxSaving.value = false
  }
}

async function removeShelfStorageBox(box: OrganizerLocation) {
  if (!selectedBox.value || deletingShelfBoxId.value !== null) return
  const childItems = selectedShelfItems.value.filter((item) => item.parent_id === box.id)
  const childIds = new Set(childItems.map((item) => item.id))
  const hasFormalMaterials = boxMaterials.value.some(
    (material) =>
      material.location_id === box.id ||
      (material.location_id !== null && childIds.has(material.location_id)),
  )
  if (childItems.length || hasFormalMaterials) {
    ElMessage.warning('该箱子内仍有物料，请先删除或移出全部物料后再移除箱子')
    return
  }
  await ElMessageBox.confirm(`确认从货架移除空箱“${box.name}”？此操作不可恢复。`, '移除箱子', {
    type: 'warning',
    confirmButtonText: '移除箱子',
    cancelButtonText: '取消',
  })
  deletingShelfBoxId.value = box.id
  try {
    await api.delete(`/locations/shelf-racks/${selectedBox.value.id}/boxes/${box.id}`)
    if (selectedShelfBoxId.value === box.id) selectedShelfBoxId.value = null
    await loadLocations()
    ElMessage.success('箱子已从货架移除')
  } finally {
    deletingShelfBoxId.value = null
  }
}

async function addShelfBoxItem() {
  if (!selectedShelfBox.value || !shelfItemForm.material_name.trim()) return
  shelfItemSaving.value = true
  try {
    await api.post(`/locations/shelf-boxes/${selectedShelfBox.value.id}/items`, {
      material_name: shelfItemForm.material_name.trim(),
      quantity:
        shelfItemForm.quantity === null
          ? null
          : Math.max(0, Math.trunc(Number(shelfItemForm.quantity))),
      notes: shelfItemForm.notes.trim(),
    })
    Object.assign(shelfItemForm, { material_name: '', quantity: null, notes: '' })
    await loadLocations()
    ElMessage.success('箱内物料已添加')
  } finally {
    shelfItemSaving.value = false
  }
}

async function saveShelfBoxItem(item: OrganizerLocation) {
  const draft = shelfItemDrafts[item.id]
  if (!draft?.material_name.trim()) {
    ElMessage.warning('物料名称不能为空')
    return
  }
  shelfItemSavingId.value = item.id
  try {
    const updated = (
      await api.put<OrganizerLocation>(`/locations/${item.id}/content`, {
        material_name: draft.material_name.trim(),
        quantity: draft.quantity === null ? null : Math.max(0, Math.trunc(Number(draft.quantity))),
        notes: draft.notes.trim(),
      })
    ).data
    updateLocationContent(updated)
    syncShelfItemDrafts()
    ElMessage.success('物料信息已更新')
  } finally {
    shelfItemSavingId.value = null
  }
}

async function removeShelfBoxItem(item: OrganizerLocation) {
  if (!selectedShelfBox.value || deletingShelfItemId.value !== null) return
  await ElMessageBox.confirm(
    `确认从“${selectedShelfBox.value.name}”删除物料“${item.bin_material_name}”？`,
    '删除箱内物料',
    { type: 'warning', confirmButtonText: '删除物料', cancelButtonText: '取消' },
  )
  deletingShelfItemId.value = item.id
  try {
    await api.delete(`/locations/shelf-boxes/${selectedShelfBox.value.id}/items/${item.id}`)
    await loadLocations()
    ElMessage.success('箱内物料已删除')
  } finally {
    deletingShelfItemId.value = null
  }
}

function openLocation(row?: LocationItem, defaults?: Partial<typeof locationForm>) {
  Object.assign(
    locationForm,
    {
      code: '',
      name: '',
      parent_id: null,
      type: 'area',
      manager: '',
      notes: '',
      is_active: true,
    },
    defaults ?? {},
    row ?? {},
  )
  editingLocationId.value = row?.id ?? null
  locationDialog.value = true
}

async function saveLocation() {
  locationSaving.value = true
  try {
    locationForm.code = locationForm.code.trim().toUpperCase()
    if (editingLocationId.value) {
      await api.put(`/locations/${editingLocationId.value}`, locationForm)
    } else {
      await api.post('/locations', locationForm)
    }
    locationDialog.value = false
    ElMessage.success('库位已保存')
    await loadLocations()
  } finally {
    locationSaving.value = false
  }
}

async function removeLocation(row: LocationItem) {
  await ElMessageBox.confirm(
    `确认删除“${row.full_path}”？存在下级库位或物料时不会执行删除。`,
    '删除库位',
    { type: 'warning' },
  )
  await api.delete(`/locations/${row.id}`)
  ElMessage.success('库位已删除')
  await loadLocations()
}

async function removeOrganizer(box: LocationItem) {
  if (deletingOrganizerId.value !== null) return
  const summary = boxSummary(box)
  if (summary.occupied > 0 || summary.materials > 0) {
    ElMessage.warning(
      `该${organizerKindLabel(box)}仍有物料，请先逐${unitLabel(box)}清空内容后再删除`,
    )
    return
  }
  const kindLabel = organizerKindLabel(box)
  await ElMessageBox.confirm(
    `确认删除${kindLabel}“${box.name}”？其自动生成的 ${boxCapacity(box)} 个空${unitLabel(box)}也会一并删除，此操作不可恢复。`,
    `删除${kindLabel}`,
    {
      type: 'warning',
      confirmButtonText: isDrawerRack(box) || isShelfRack(box) ? '删除货架' : '删除大盒',
      cancelButtonText: '取消',
    },
  )
  deletingOrganizerId.value = box.id
  try {
    await api.delete(`/locations/organizers/${box.id}`)
    if (selectedBoxId.value === box.id) {
      selectedBoxId.value = null
      selectedBinId.value = null
      selectedShelfBoxId.value = null
      boxOpen.value = false
    }
    await loadLocations()
    ElMessage.success(`${kindLabel}已删除`)
  } finally {
    deletingOrganizerId.value = null
  }
}

onMounted(async () => {
  window.addEventListener('locations:show-overview', requestLocationsOverview)
  await loadLocations()
  await focusLocationFromRoute()
})
watch(
  () => route.query.focus,
  () => {
    if (locations.value.length) void focusLocationFromRoute()
  },
)
onBeforeUnmount(() => {
  window.removeEventListener('locations:show-overview', requestLocationsOverview)
  if (closeAnimationTimer !== null) window.clearTimeout(closeAnimationTimer)
  if (overviewAnimationTimer !== null) window.clearTimeout(overviewAnimationTimer)
})
</script>

<template>
  <div class="page locations-page">
    <div class="page-header">
      <div>
        <h1 class="page-title">可视化库位管理</h1>
        <div class="page-subtitle">
          按“大仓库 → 盒子、柜子或货架 → 内部格口”逐级核查现实库位与物料
        </div>
      </div>
      <div class="header-actions">
        <el-button
          v-if="route.query.task"
          @click="
            router.push({
              path: '/projects',
              query: { task: route.query.task, allocation: route.query.allocation },
            })
          "
          >返回拣货任务</el-button
        >
        <el-button :disabled="!warehouses.length" @click="openWarehouseMapPlanner"
          >仓库地图 / 路线</el-button
        >
        <el-button :icon="Setting" @click="structureDrawer = true">库位结构</el-button>
        <el-button :icon="Refresh" @click="loadLocations">刷新</el-button>
        <el-button
          v-if="canManageLocations"
          type="primary"
          :icon="Plus"
          @click="openOrganizerDialog"
        >
          新增盒子或柜子
        </el-button>
      </div>
    </div>

    <section
      class="box-library"
      :class="{
        'is-overview': !boxOpen,
        'is-warehouse-overview': !selectedWarehouse,
        'is-entering': overviewEntering,
        'is-closing': boxClosing,
      }"
      data-testid="box-library"
    >
      <div class="section-heading">
        <div>
          <span>{{
            !selectedWarehouse
              ? 'WAREHOUSE OVERVIEW'
              : boxOpen
                ? selectedWarehouse.code
                : 'STORAGE EQUIPMENT'
          }}</span>
          <h2>
            {{
              !selectedWarehouse
                ? '全部仓库'
                : boxOpen
                  ? `切换${selectedWarehouse.name}内的库位`
                  : `${selectedWarehouse.name} · 库位总览`
            }}
          </h2>
          <p v-if="!selectedWarehouse">
            先选择要核查的大仓库，再查看该仓库中的物料盒、抽屉柜和货架。
          </p>
          <p v-else-if="!boxOpen">
            当前仅显示“{{ selectedWarehouse.name }}”中的库位设备；点击盒子或柜子后再查看内部物料。
          </p>
        </div>
        <div class="section-tools">
          <el-button
            v-if="selectedWarehouse"
            plain
            @click="boxOpen ? showBoxOverview(true) : showWarehouseOverview()"
          >
            ← 返回全部仓库
          </el-button>
          <el-input
            v-if="selectedWarehouse && warehouseBoxes.length"
            v-model="search"
            :prefix-icon="Search"
            clearable
            placeholder="搜索当前仓库内的盒子或柜子"
          />
        </div>
      </div>

      <template v-if="!selectedWarehouse">
        <div v-if="warehouses.length" class="warehouse-overview-summary">
          <div>
            <b>{{ warehouses.length }} 个大仓库</b>
            <span>所有盒子、柜子和货架均按所属仓库归类显示</span>
          </div>
          <small>请选择一个仓库进入核查</small>
        </div>
        <div v-if="warehouses.length" class="warehouse-grid">
          <button
            v-for="warehouse in warehouses"
            :key="warehouse.id"
            type="button"
            class="warehouse-card"
            :aria-label="`进入仓库 ${warehouse.name}`"
            data-testid="warehouse-card"
            @click="selectWarehouse(warehouse)"
          >
            <div class="warehouse-visual" aria-hidden="true">
              <div class="warehouse-roof"></div>
              <div class="warehouse-building">
                <span class="warehouse-nameplate">{{ warehouse.code }}</span>
                <div class="warehouse-door">
                  <i v-for="index in 4" :key="index"></i>
                </div>
              </div>
            </div>
            <div class="warehouse-card-copy">
              <span>{{ locationTypeLabel(warehouse) }}</span>
              <b>{{ warehouse.name }}</b>
              <small>{{ warehouse.full_path }}</small>
              <div class="warehouse-equipment-tags">
                <em v-if="warehouseSummary(warehouse).componentBoxes">
                  元件盒 {{ warehouseSummary(warehouse).componentBoxes }}
                </em>
                <em v-if="warehouseSummary(warehouse).drawerRacks">
                  抽屉柜 {{ warehouseSummary(warehouse).drawerRacks }}
                </em>
                <em v-if="warehouseSummary(warehouse).shelfRacks">
                  层架 {{ warehouseSummary(warehouse).shelfRacks }}
                </em>
                <em v-if="!warehouseSummary(warehouse).organizers">暂无设备</em>
              </div>
            </div>
            <div class="warehouse-card-stats">
              <span>
                <b>{{ warehouseSummary(warehouse).organizers }}</b>
                个盒子/柜子
              </span>
              <span>
                <b>{{ warehouseSummary(warehouse).materials }}</b>
                种物料
              </span>
              <span>
                <b>{{ formatQuantity(warehouseSummary(warehouse).quantity) }}</b>
                件库存
              </span>
            </div>
            <span class="warehouse-enter-cue">进入仓库核查 →</span>
          </button>
        </div>
        <div v-else class="first-box-tip">
          <div>
            <b>还没有建立大仓库</b>
            <span>请先在库位结构中创建“仓库”或顶层“区域”，再向其中添加盒子和柜子。</span>
          </div>
          <el-button type="primary" @click="structureDrawer = true">打开库位结构</el-button>
        </div>
      </template>

      <template v-else>
        <div v-if="!boxOpen && warehouseBoxes.length" class="overview-summary">
          <div class="closed-box-mark" aria-hidden="true"><span></span></div>
          <div>
            <b>{{ warehouseBoxes.length }} 个盒子、柜子或货架</b>
            <span>所属仓库：{{ selectedWarehouse.full_path }}</span>
          </div>
          <small>当前显示 {{ boxes.length }} 个</small>
        </div>

        <div v-if="boxes.length" class="box-strip">
          <button
            v-for="box in boxes"
            :key="box.id"
            type="button"
            class="box-card"
            :class="{
              active: box.id === selectedBoxId && boxOpen,
              'just-closed': overviewEntering && box.id === selectedBoxId,
            }"
            :aria-label="`打开库位 ${box.name}`"
            @click="selectBox(box)"
          >
            <div
              class="mini-case"
              :class="{
                'rack-case': isDrawerRack(box),
                'shelf-rack-case': isShelfRack(box),
              }"
            >
              <span
                v-if="!isDrawerRack(box) && !isShelfRack(box)"
                class="mini-case-cover"
                aria-hidden="true"
              ></span>
              <div
                v-if="!box.organizer_style || box.organizer_style === 'standard_56'"
                class="mini-grid"
              >
                <i v-for="index in 56" :key="index"></i>
              </div>
              <div v-else-if="isDrawerRack(box)" class="mini-rack-grid">
                <i v-for="index in 100" :key="index"></i>
              </div>
              <div v-else-if="isShelfRack(box)" class="mini-shelf-rack">
                <i v-for="index in 6" :key="index"><span></span><span></span><span></span></i>
              </div>
              <div v-else class="mini-mixed-grid">
                <div
                  v-for="section in miniSections(box)"
                  :key="section.side"
                  class="mini-half"
                  :class="`module-${section.module}`"
                >
                  <i v-for="index in section.count" :key="index"></i>
                </div>
              </div>
            </div>
            <div class="box-card-copy">
              <span>{{ box.code }}</span>
              <b>{{ box.name }}</b>
              <small>{{ box.notes || '未填写存放类型' }}</small>
              <em>{{ boxLayoutLabel(box) }}</em>
            </div>
            <div class="box-card-stats">
              <span v-if="isShelfRack(box)">
                <b>{{ boxSummary(box).occupiedShelves }}</b
                >/{{ boxCapacity(box) }} 层已用
              </span>
              <span v-else>
                <b>{{ boxSummary(box).occupied }}</b
                >/{{ boxCapacity(box) }} 已用
              </span>
              <span v-if="isShelfRack(box)"
                ><b>{{ boxSummary(box).containers }}</b> 个箱子</span
              >
              <span
                ><b>{{ boxSummary(box).materials }}</b> 种物料</span
              >
              <span v-if="!isShelfRack(box)"
                ><b>{{ formatQuantity(boxSummary(box).quantity) }}</b> 件</span
              >
            </div>
            <span v-if="!boxOpen" class="box-open-cue">
              {{ isDrawerRack(box) || isShelfRack(box) ? '查看柜子 →' : '打开盒盖 →' }}
            </span>
          </button>
          <button
            v-if="canManageLocations"
            type="button"
            class="add-box-card"
            @click="openOrganizerDialog"
          >
            <span>＋</span>
            <b>向该仓库添加库位</b>
            <small>可选择元件盒、抽屉柜或六层货架</small>
          </button>
        </div>
        <div v-else-if="search" class="search-empty">
          当前仓库中没有匹配的盒子或柜子
          <el-button link type="primary" @click="search = ''">清除搜索</el-button>
        </div>
        <div v-else class="first-box-tip">
          <div>
            <b>“{{ selectedWarehouse.name }}”中还没有盒子或柜子</b>
            <span>新增设备会自动归入当前仓库，不会改变其他仓库。</span>
          </div>
          <el-button v-if="canManageLocations" type="primary" @click="openOrganizerDialog"
            >添加第一个库位</el-button
          >
        </div>
      </template>
    </section>

    <section
      v-if="boxOpen && selectedBox"
      v-loading="loading || materialsLoading"
      class="box-workspace"
      :class="{
        'is-closing': boxClosing,
        'is-rack': isDrawerRack(selectedBox) || isShelfRack(selectedBox),
      }"
      data-testid="box-workspace"
    >
      <ShelfRack6
        v-if="isShelfRack(selectedBox)"
        :rack="selectedBox"
        :shelves="selectedShelves"
        :storage-boxes="selectedShelfBoxes"
        :items="selectedShelfItems"
        :materials="boxMaterials"
        :selected-box-id="selectedShelfBoxId"
        :highlighted-location-id="focusedLocationId"
        :editable="canManageLocations"
        :closing="boxClosing"
        @select-box="selectShelfStorageBox"
        @add-box="openShelfBoxDialog"
        @delete-box="removeShelfStorageBox"
        @toggle="showBoxOverview"
      />

      <DrawerRack100
        v-else-if="isDrawerRack(selectedBox)"
        :rack="selectedBox"
        :bins="selectedBins"
        :materials="boxMaterials"
        :selected-bin-id="selectedBinId"
        :highlighted-bin-id="focusedLocationId"
        :closing="boxClosing"
        @select="selectBin"
        @toggle="showBoxOverview"
      />

      <OrganizerBox3D
        v-else
        :box="selectedBox"
        :bins="selectedBins"
        :materials="boxMaterials"
        :selected-bin-id="selectedBinId"
        :highlighted-bin-id="focusedLocationId"
        :editable="canManageLocations"
        :open="boxOpen"
        :closing="boxClosing"
        :layout-saving="layoutSaving"
        @select="selectBin"
        @toggle="showBoxOverview"
        @change-module="changeOrganizerModule"
      />

      <aside class="detail-panel">
        <template v-if="selectedBox">
          <div class="detail-top">
            <div>
              <span>{{ selectedBox.code }}</span>
              <h2>{{ selectedBox.name }}</h2>
              <p>{{ selectedBox.notes || '尚未填写该盒存放的物料类型' }}</p>
            </div>
            <div v-if="canManageLocations" class="detail-actions">
              <el-button
                type="danger"
                plain
                :loading="deletingOrganizerId === selectedBox.id"
                @click="removeOrganizer(selectedBox)"
              >
                {{
                  isDrawerRack(selectedBox) || isShelfRack(selectedBox) ? '删除货架' : '删除大盒'
                }}
              </el-button>
              <el-button circle title="编辑元件盒" @click="openLocation(selectedBox)">✎</el-button>
            </div>
          </div>

          <div class="box-metrics">
            <template v-if="isShelfRack(selectedBox)">
              <div>
                <span>已用层位</span>
                <b>{{ occupiedShelfCount }}</b
                ><small>/ 6 层</small>
              </div>
              <div>
                <span>货架箱子</span>
                <b>{{ selectedShelfBoxes.length }}</b
                ><small>个</small>
              </div>
              <div>
                <span>箱内物料</span>
                <b>{{ boxSummary(selectedBox).materials }}</b
                ><small>种</small>
              </div>
            </template>
            <template v-else>
              <div>
                <span>已用{{ unitLabel(selectedBox) }}</span>
                <b>{{ occupiedBinCount }}</b
                ><small>/ {{ boxCapacity(selectedBox) }}</small>
              </div>
              <div>
                <span>物料种类</span>
                <b>{{ boxSummary(selectedBox).materials }}</b
                ><small>种</small>
              </div>
              <div>
                <span>{{ isDrawerRack(selectedBox) ? '架内库存' : '盒内库存' }}</span
                ><b>{{ formatQuantity(selectedQuantity) }}</b
                ><small>件</small>
              </div>
            </template>
          </div>

          <el-alert
            v-if="unassignedBoxMaterials.length"
            :title="`${unassignedBoxMaterials.length} 种物料只分配到了${organizerKindLabel(selectedBox)}，尚未指定${unitLabel(selectedBox)}`"
            type="warning"
            :closable="false"
            show-icon
          />

          <template v-if="isShelfRack(selectedBox)">
            <div v-if="selectedShelfBox" class="shelf-box-detail">
              <div class="shelf-box-heading">
                <div>
                  <span>当前箱子</span>
                  <h3>{{ selectedShelfBox.name }}</h3>
                  <small>{{ selectedShelfBox.notes || '未填写箱子说明' }}</small>
                </div>
                <div>
                  <el-button
                    v-if="canManageLocations"
                    link
                    type="primary"
                    @click="openLocation(selectedShelfBox)"
                  >
                    编辑箱子
                  </el-button>
                  <el-button
                    v-if="canManageLocations"
                    link
                    type="danger"
                    :loading="deletingShelfBoxId === selectedShelfBox.id"
                    @click="removeShelfStorageBox(selectedShelfBox)"
                  >
                    移除箱子
                  </el-button>
                </div>
              </div>

              <div class="bin-path">{{ selectedShelfBox.full_path }}</div>

              <div class="shelf-item-section">
                <div class="shelf-section-title">
                  <div>
                    <b>箱内物料</b>
                    <span>一个箱子可以保存多种物料</span>
                  </div>
                  <small>{{ selectedShelfBoxItems.length }} 种</small>
                </div>

                <div v-if="selectedShelfBoxItems.length" class="shelf-item-list">
                  <article v-for="(item, index) in selectedShelfBoxItems" :key="item.id">
                    <header>
                      <span>物料 {{ index + 1 }}</span>
                      <small>{{ item.code }}</small>
                    </header>
                    <el-form
                      v-if="shelfItemDrafts[item.id]"
                      label-position="top"
                      class="shelf-item-editor"
                    >
                      <el-form-item label="物料名称（必填）" required>
                        <el-input
                          v-model="shelfItemDrafts[item.id].material_name"
                          maxlength="200"
                          placeholder="例如：M3 内六角螺丝"
                        />
                      </el-form-item>
                      <el-form-item label="数量（选填）">
                        <el-input-number
                          v-model="shelfItemDrafts[item.id].quantity"
                          :min="0"
                          :step="1"
                          :precision="0"
                          controls-position="right"
                        />
                      </el-form-item>
                      <el-form-item label="备注（选填）" class="full-row">
                        <el-input
                          v-model="shelfItemDrafts[item.id].notes"
                          maxlength="2000"
                          placeholder="规格、批次或其他说明"
                        />
                      </el-form-item>
                    </el-form>
                    <footer v-if="canManageLocations">
                      <el-button
                        link
                        type="danger"
                        :loading="deletingShelfItemId === item.id"
                        @click="removeShelfBoxItem(item)"
                      >
                        删除物料
                      </el-button>
                      <el-button
                        type="primary"
                        plain
                        :loading="shelfItemSavingId === item.id"
                        @click="saveShelfBoxItem(item)"
                      >
                        保存修改
                      </el-button>
                    </footer>
                  </article>
                </div>
                <div v-else class="empty-shelf-box">这个箱子还是空的，可在下方添加第一种物料。</div>
              </div>

              <div v-if="canManageLocations" class="add-shelf-item">
                <div class="shelf-section-title">
                  <div>
                    <b>添加物料</b>
                    <span>仅名称必填，数量与备注均可留空</span>
                  </div>
                </div>
                <el-form label-position="top" class="shelf-item-editor">
                  <el-form-item label="物料名称（必填）" required>
                    <el-input
                      v-model="shelfItemForm.material_name"
                      maxlength="200"
                      clearable
                      placeholder="输入物料名称"
                      @keyup.enter="addShelfBoxItem"
                    />
                  </el-form-item>
                  <el-form-item label="数量（选填）">
                    <el-input-number
                      v-model="shelfItemForm.quantity"
                      :min="0"
                      :step="1"
                      :precision="0"
                      controls-position="right"
                    />
                  </el-form-item>
                  <el-form-item label="备注（选填）" class="full-row">
                    <el-input
                      v-model="shelfItemForm.notes"
                      maxlength="2000"
                      placeholder="规格、批次或其他说明"
                    />
                  </el-form-item>
                </el-form>
                <el-button
                  type="primary"
                  :loading="shelfItemSaving"
                  :disabled="!shelfItemForm.material_name.trim()"
                  @click="addShelfBoxItem"
                >
                  添加到当前箱子
                </el-button>
              </div>
            </div>
            <div v-else class="select-hint">
              点击左侧货架中的箱子管理物料；如果层位为空，请先点击“放置箱子”。
            </div>
          </template>

          <div v-else-if="selectedBin" class="bin-detail">
            <div class="bin-heading">
              <div>
                <span>当前{{ unitLabel(selectedBox) }}</span>
                <h3>{{ selectedBin.name }}</h3>
              </div>
              <span class="bin-save-state" :class="selectedBinSaveState">
                {{ selectedBinSaveText }}
              </span>
            </div>

            <div class="bin-content-editor">
              <el-form label-position="top">
                <el-form-item label="物料名称（必填）" required>
                  <el-input
                    v-model="binContentForm.material_name"
                    maxlength="200"
                    clearable
                    placeholder="例如：功率继电器"
                    :disabled="!canManageLocations"
                    @input="scheduleBinContentSave"
                  />
                </el-form-item>
                <el-form-item label="数量（选填）">
                  <el-input-number
                    v-model="binContentForm.quantity"
                    :min="0"
                    :step="1"
                    :precision="0"
                    controls-position="right"
                    placeholder="可不填写"
                    :disabled="!canManageLocations"
                    @change="scheduleBinContentSave"
                  />
                </el-form-item>
                <el-form-item label="备注（选填）">
                  <el-input
                    v-model="binContentForm.notes"
                    type="textarea"
                    :rows="4"
                    maxlength="2000"
                    show-word-limit
                    placeholder="例如：待测试、采购批次或其他说明"
                    :disabled="!canManageLocations"
                    @input="scheduleBinContentSave"
                  />
                </el-form-item>
              </el-form>

              <div
                v-if="selectedBinMaterials.length && !selectedBinHasDirectContent"
                class="legacy-content-tip"
              >
                已从原物料资料带入当前内容；修改任意字段后会保存为简易库位记录，原资料不会被删除。
              </div>

              <div class="bin-editor-footer">
                <span>停止输入约 0.7 秒后自动更新，无需打开单独页面。</span>
                <el-button
                  v-if="canManageLocations && selectedBinHasAnyContent"
                  link
                  type="danger"
                  @click="clearSelectedBinContent"
                >
                  清空内容
                </el-button>
              </div>
            </div>
          </div>
          <div v-else class="select-hint">请在左侧模型中选择一个{{ unitLabel(selectedBox) }}</div>
        </template>

        <template v-else>
          <div class="onboarding">
            <span>READY FOR YOUR FIRST BOX</span>
            <h2>把现实中的元件盒和货架映射到网页</h2>
            <p>
              可创建标准 56 格盒、左右半区独立配置的混合盒，以及 20 行 × 5 列的 100
              抽货架和六层箱式货架。格口与抽屉可直接填写物料；六层货架可逐层摆放多个箱子，
              每个箱子可保存多种物料。
            </p>
            <ul>
              <li><b>1</b>填写大库位名称和存放类型</li>
              <li><b>2</b>选择元件盒、抽屉货架或六层货架</li>
              <li><b>3</b>点击具体格口、抽屉或箱子管理物料</li>
            </ul>
            <el-button
              v-if="canManageLocations"
              type="primary"
              size="large"
              @click="openOrganizerDialog"
            >
              创建第一个大库位
            </el-button>
          </div>
        </template>
      </aside>
    </section>

    <el-drawer
      v-model="mapPlannerDrawer"
      title="仓库地图、通行图与路线校准"
      size="92%"
      destroy-on-close
    >
      <WarehouseMapAdmin
        v-if="selectedWarehouseId"
        :warehouse-id="selectedWarehouseId"
        :can-manage="canManageLocations"
      />
      <el-empty v-else description="请先选择一个仓库" />
    </el-drawer>

    <el-drawer v-model="structureDrawer" title="完整库位结构" size="480px">
      <div v-if="canManageLocations" class="drawer-actions">
        <el-button type="primary" :icon="Plus" @click="openLocation()">新增普通库位</el-button>
        <el-button @click="openOrganizerDialog">新增可视化大库位</el-button>
      </div>
      <el-tree
        :data="locationTree"
        node-key="id"
        :props="{ label: 'name', children: 'children' }"
        :expand-on-click-node="false"
        class="location-tree"
      >
        <template #default="{ data }">
          <div class="tree-row">
            <div>
              <span class="tree-icon" :class="data.type">{{
                data.type === 'box'
                  ? '▦'
                  : data.type === 'container'
                    ? '▣'
                    : data.type === 'shelf'
                      ? '═'
                      : data.type === 'bin'
                        ? '▫'
                        : '⌂'
              }}</span>
              <span
                ><b>{{ data.name }}</b
                ><small>{{ locationTypeLabel(data) }} · {{ data.code }}</small></span
              >
            </div>
            <span v-if="canManageLocations" class="tree-actions">
              <el-button link type="primary" @click.stop="openLocation(data)">编辑</el-button>
              <el-button
                v-if="data.type === 'box'"
                link
                type="danger"
                :loading="deletingOrganizerId === data.id"
                @click.stop="removeOrganizer(data)"
              >
                {{ isDrawerRack(data) || isShelfRack(data) ? '删除货架' : '删除大盒' }}
              </el-button>
              <el-button
                v-if="data.type !== 'box'"
                link
                type="danger"
                @click.stop="removeLocation(data)"
              >
                删除
              </el-button>
            </span>
          </div>
        </template>
      </el-tree>
      <el-empty v-if="!locations.length" description="暂无库位" />
    </el-drawer>

    <el-dialog v-model="organizerDialog" title="向仓库添加盒子、柜子或货架" width="780px">
      <el-alert
        :title="
          organizerForm.organizer_style === 'standard_56'
            ? '标准盒将创建 7 行 × 8 列，共 56 个独立小格。'
            : organizerForm.organizer_style === 'drawer_rack_100'
              ? '100 抽货架将按实物结构创建 20 行 × 5 列，共 100 个独立抽屉。'
              : organizerForm.organizer_style === 'shelf_rack_6'
                ? '六层箱式货架将创建 6 个固定层位；所有层初始为空，可随后逐层添加多个箱子。'
                : `混合盒将按左右半区配置创建 ${organizerFormCapacity} 个格口，之后仍可切换。`
        "
        type="info"
        :closable="false"
        show-icon
      />
      <el-form label-position="top" class="dialog-form">
        <div class="two">
          <el-form-item label="设备编码" required>
            <el-input v-model="organizerForm.code" placeholder="例如 BOX-R-01 或 RACK-01" />
          </el-form-item>
          <el-form-item label="盒子、柜子或货架名称" required>
            <el-input
              v-model="organizerForm.name"
              placeholder="例如 贴片电阻盒 01 或 100抽货架 01"
            />
          </el-form-item>
        </div>
        <el-form-item label="所属仓库或区域">
          <el-select
            v-model="organizerForm.parent_id"
            clearable
            filterable
            placeholder="请选择所属仓库"
          >
            <el-option
              v-for="item in parentOptions"
              :key="item.id"
              :label="item.full_path"
              :value="item.id"
            />
          </el-select>
        </el-form-item>
        <div class="two">
          <el-form-item label="负责人">
            <el-input v-model="organizerForm.manager" placeholder="可选" />
          </el-form-item>
          <el-form-item label="存放类型">
            <el-input v-model="organizerForm.notes" placeholder="例如 0603 电阻" />
          </el-form-item>
        </div>

        <el-form-item label="设备样式">
          <div class="organizer-style-options">
            <button
              type="button"
              :class="{ active: organizerForm.organizer_style === 'standard_56' }"
              @click="selectOrganizerStyle('standard_56')"
            >
              <span class="style-icon standard"><i v-for="index in 12" :key="index"></i></span>
              <b>标准 56 格盒</b>
              <small>7 × 8 个同尺寸格口</small>
            </button>
            <button
              type="button"
              :class="{ active: organizerForm.organizer_style === 'split_configurable' }"
              @click="selectOrganizerStyle('split_configurable')"
            >
              <span class="style-icon mixed">
                <i v-for="index in 4" :key="`large-${index}`"></i>
                <em><i v-for="index in 8" :key="`small-${index}`"></i></em>
              </span>
              <b>左右可配置混合盒</b>
              <small>每半边可独立选择大小格</small>
            </button>
            <button
              type="button"
              :class="{ active: organizerForm.organizer_style === 'drawer_rack_100' }"
              @click="selectOrganizerStyle('drawer_rack_100')"
            >
              <span class="style-icon rack">
                <i v-for="index in 100" :key="`rack-${index}`"></i>
              </span>
              <b>100 抽零件货架</b>
              <small>20 行 × 5 列固定抽屉</small>
            </button>
            <button
              type="button"
              :class="{ active: organizerForm.organizer_style === 'shelf_rack_6' }"
              @click="selectOrganizerStyle('shelf_rack_6')"
            >
              <span class="style-icon shelf-rack">
                <i v-for="index in 6" :key="`shelf-${index}`"><em></em><em></em></i>
              </span>
              <b>六层箱式货架</b>
              <small>层位初始为空，可动态摆放箱子</small>
            </button>
          </div>
        </el-form-item>

        <div
          v-if="organizerForm.organizer_style === 'split_configurable'"
          class="create-half-config"
        >
          <div class="create-half-row">
            <span>左半区</span>
            <el-radio-group v-model="organizerForm.organizer_left_module">
              <el-radio-button value="small">小格模块 · 28 格</el-radio-button>
              <el-radio-button value="large">大格模块 · 8 格</el-radio-button>
            </el-radio-group>
          </div>
          <div class="create-half-row">
            <span>右半区</span>
            <el-radio-group v-model="organizerForm.organizer_right_module">
              <el-radio-button value="small">小格模块 · 28 格</el-radio-button>
              <el-radio-button value="large">大格模块 · 8 格</el-radio-button>
            </el-radio-group>
          </div>
          <p>小格约 24 × 22 mm；大格约 49 × 40 mm。两半区配置会单独保存。</p>
        </div>

        <div class="layout-preview">
          <template v-if="organizerForm.organizer_style === 'standard_56'">
            <span>自动编号</span>
            <b>A01 → A08</b><b>···</b><b>G01 → G08</b>
          </template>
          <template v-else-if="organizerForm.organizer_style === 'split_configurable'">
            <span>当前组合</span>
            <b> 左{{ organizerForm.organizer_left_module === 'small' ? '小格 28' : '大格 8' }} </b>
            <b>＋</b>
            <b> 右{{ organizerForm.organizer_right_module === 'small' ? '小格 28' : '大格 8' }} </b>
          </template>
          <template v-else-if="organizerForm.organizer_style === 'drawer_rack_100'">
            <span>抽屉坐标</span>
            <b>A01 → E01</b><b>···</b><b>A20 → E20</b>
          </template>
          <template v-else>
            <span>固定层位</span>
            <b>第 6 层 → 第 1 层</b><b>·</b><b>初始均为空</b>
          </template>
          <small>
            {{ organizerFormCapacity }} 个{{
              organizerForm.organizer_style === 'drawer_rack_100'
                ? '抽屉'
                : organizerForm.organizer_style === 'shelf_rack_6'
                  ? '层位'
                  : '格口'
            }}
          </small>
        </div>
        <el-switch
          v-model="organizerForm.is_active"
          active-text="立即启用"
          inactive-text="暂不启用"
        />
      </el-form>
      <template #footer>
        <el-button @click="organizerDialog = false">取消</el-button>
        <el-button
          type="primary"
          :loading="organizerSaving"
          :disabled="!organizerForm.code.trim() || !organizerForm.name.trim()"
          @click="createOrganizer"
        >
          创建{{
            organizerForm.organizer_style === 'drawer_rack_100' ||
            organizerForm.organizer_style === 'shelf_rack_6'
              ? '货架'
              : '元件盒'
          }}和 {{ organizerFormCapacity }} 个{{
            organizerForm.organizer_style === 'drawer_rack_100'
              ? '抽屉'
              : organizerForm.organizer_style === 'shelf_rack_6'
                ? '层位'
                : '格口'
          }}
        </el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="shelfBoxDialog"
      :title="`在${shelfBoxFormShelf?.name || '当前层'}放置箱子`"
      width="520px"
    >
      <el-alert
        title="箱子放置后默认为空；打开箱子后可在右侧连续添加多种物料。"
        type="info"
        :closable="false"
        show-icon
      />
      <el-form label-position="top" class="dialog-form">
        <el-form-item label="箱子名称" required>
          <el-input
            v-model="shelfBoxForm.name"
            maxlength="100"
            clearable
            placeholder="例如：紧固件箱、包装材料箱"
            @keyup.enter="createShelfStorageBox"
          />
        </el-form-item>
        <el-form-item label="箱子说明（选填）">
          <el-input
            v-model="shelfBoxForm.notes"
            type="textarea"
            :rows="3"
            maxlength="2000"
            show-word-limit
            placeholder="例如：M2—M5 螺丝与螺母"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="shelfBoxDialog = false">取消</el-button>
        <el-button
          type="primary"
          :loading="shelfBoxSaving"
          :disabled="!shelfBoxForm.name.trim()"
          @click="createShelfStorageBox"
        >
          放置到{{ shelfBoxFormShelf?.name || '当前层' }}
        </el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="locationDialog"
      :title="editingLocationId ? '编辑库位' : '新增普通库位'"
      width="540px"
    >
      <el-form label-position="top">
        <div class="two">
          <el-form-item label="库位编码" required>
            <el-input v-model="locationForm.code" />
          </el-form-item>
          <el-form-item label="库位名称" required>
            <el-input v-model="locationForm.name" />
          </el-form-item>
        </div>
        <el-form-item label="上级库位">
          <el-select
            v-model="locationForm.parent_id"
            clearable
            filterable
            placeholder="作为顶级库位"
            :disabled="['shelf', 'container', 'bin'].includes(locationForm.type)"
          >
            <el-option
              v-for="item in allParentOptions"
              :key="item.id"
              :label="item.full_path"
              :value="item.id"
            />
          </el-select>
        </el-form-item>
        <div class="two">
          <el-form-item label="库位类型">
            <el-select
              v-model="locationForm.type"
              :disabled="['box', 'shelf', 'container', 'bin'].includes(locationForm.type)"
            >
              <el-option
                v-for="(label, value) in typeLabels"
                :key="value"
                :label="label"
                :value="value"
                :disabled="value === 'box'"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="负责人">
            <el-input v-model="locationForm.manager" />
          </el-form-item>
        </div>
        <el-form-item label="说明">
          <el-input v-model="locationForm.notes" type="textarea" :rows="3" />
        </el-form-item>
        <el-switch v-model="locationForm.is_active" active-text="启用" inactive-text="停用" />
      </el-form>
      <template #footer>
        <el-button @click="locationDialog = false">取消</el-button>
        <el-button
          type="primary"
          :loading="locationSaving"
          :disabled="!locationForm.code.trim() || !locationForm.name.trim()"
          @click="saveLocation"
        >
          保存
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.locations-page {
  max-width: 1680px;
}
.header-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.header-actions .el-button {
  margin: 0;
}
.box-library {
  margin-bottom: 18px;
  padding: 17px;
  border: 1px solid var(--g-line);
  border-radius: 18px;
  background: #fff;
  box-shadow: 0 8px 28px #29496b0a;
}
.section-heading {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 14px;
}
.section-heading > div {
  display: flex;
  flex-direction: column;
}
.section-heading span {
  color: var(--g-accent-strong);
  font-size: 12px;
  letter-spacing: 1.5px;
}
.section-heading h2 {
  margin: 3px 0 0;
  color: var(--g-ink);
  font-size: 18px;
}
.section-heading p {
  max-width: 680px;
  margin: 7px 0 0;
  color: var(--g-accent-strong);
  font-size: 13px;
  line-height: 1.6;
}
.section-heading .el-input {
  max-width: 320px;
}
.section-tools {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  flex-direction: row !important;
  gap: 10px;
}
.section-tools .el-input {
  width: 320px;
}
.warehouse-overview-summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  margin-bottom: 20px;
  padding: 14px 17px;
  border: 1px solid var(--g-line);
  border-radius: 14px;
  background: linear-gradient(135deg, var(--mb-surface-subtle), var(--mb-surface-subtle));
  color: var(--g-accent-strong);
}
.warehouse-overview-summary > div {
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.warehouse-overview-summary b {
  color: var(--g-ink);
  font-size: 15px;
}
.warehouse-overview-summary span,
.warehouse-overview-summary small {
  font-size: 13px;
}
.warehouse-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(380px, 1fr));
  gap: 18px;
}
.warehouse-card {
  position: relative;
  display: grid;
  grid-template-columns: 154px minmax(0, 1fr);
  min-width: 0;
  min-height: 250px;
  padding: 24px 24px 19px;
  overflow: hidden;
  border: 1px solid var(--g-line);
  border-radius: 18px;
  background:
    radial-gradient(circle at 18% 4%, var(--mb-surface-subtle) 0, transparent 31%),
    linear-gradient(145deg, #fff, var(--mb-surface-subtle));
  color: var(--g-accent-strong);
  text-align: left;
  cursor: pointer;
  transition:
    transform 0.2s ease,
    border-color 0.2s ease,
    box-shadow 0.2s ease;
}
.warehouse-card::after {
  position: absolute;
  right: -52px;
  bottom: -58px;
  width: 150px;
  height: 150px;
  border: 22px solid #dceaf833;
  border-radius: 50%;
  content: '';
}
.warehouse-card:hover {
  transform: translateY(-3px);
  border-color: var(--g-line);
  box-shadow: 0 16px 34px #2d628c1c;
}
.warehouse-visual {
  position: relative;
  align-self: center;
  width: 134px;
  height: 126px;
  filter: drop-shadow(0 12px 10px #274d6c20);
}
.warehouse-roof {
  position: absolute;
  z-index: 2;
  top: 0;
  left: 1px;
  width: 132px;
  height: 40px;
  border: 2px solid var(--g-accent);
  background: linear-gradient(145deg, var(--g-accent), var(--g-accent));
  clip-path: polygon(50% 0, 100% 78%, 96% 100%, 4% 100%, 0 78%);
}
.warehouse-building {
  position: absolute;
  right: 8px;
  bottom: 0;
  left: 8px;
  height: 94px;
  border: 2px solid var(--g-line);
  border-radius: 3px 3px 7px 7px;
  background:
    linear-gradient(90deg, var(--g-ice) 0 9%, transparent 9% 91%, var(--g-ice) 91%),
    linear-gradient(var(--mb-surface-subtle), var(--mb-surface-subtle));
}
.warehouse-nameplate {
  position: absolute;
  top: 10px;
  left: 50%;
  padding: 3px 8px;
  border-radius: 4px;
  background: var(--g-accent);
  color: #fff !important;
  font-size: 8px !important;
  font-weight: 800;
  letter-spacing: 1px !important;
  transform: translateX(-50%);
}
.warehouse-door {
  position: absolute;
  right: 18px;
  bottom: 0;
  left: 18px;
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 4px;
  height: 54px;
  padding: 7px 8px 0;
  border: 2px solid var(--g-accent);
  border-bottom: 0;
  border-radius: 4px 4px 0 0;
  background: linear-gradient(180deg, var(--g-ice), var(--g-accent));
}
.warehouse-door i {
  border: 1px solid var(--g-accent);
  border-radius: 2px 2px 0 0;
  background: repeating-linear-gradient(
    180deg,
    var(--mb-surface-subtle) 0 6px,
    var(--g-accent) 6px 8px
  );
}
.warehouse-card-copy {
  display: flex;
  min-width: 0;
  align-self: center;
  flex-direction: column;
}
.warehouse-card-copy > span {
  color: var(--g-accent-strong);
  font-size: 12px;
  font-weight: 800;
  letter-spacing: 1.1px;
}
.warehouse-card-copy > b {
  margin: 7px 0 5px;
  color: var(--g-ink);
  font-size: 23px;
  line-height: 1.2;
}
.warehouse-card-copy > small {
  min-height: 20px;
  color: var(--g-accent-strong);
  font-size: 13px;
  line-height: 1.55;
}
.warehouse-equipment-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 13px;
}
.warehouse-equipment-tags em {
  padding: 4px 7px;
  border: 1px solid var(--g-line);
  border-radius: 999px;
  background: var(--mb-surface-subtle);
  color: var(--g-accent-strong);
  font-size: 9px;
  font-style: normal;
}
.warehouse-card-stats {
  z-index: 1;
  grid-column: 1/-1;
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
  margin-top: 18px;
  padding-top: 14px;
  border-top: 1px solid var(--g-line);
}
.warehouse-card-stats span {
  display: flex;
  align-items: baseline;
  justify-content: center;
  gap: 4px;
  color: var(--g-accent-strong);
  font-size: 9px;
}
.warehouse-card-stats b {
  color: var(--g-accent-strong);
  font-size: 14px;
}
.warehouse-enter-cue {
  z-index: 1;
  grid-column: 1/-1;
  justify-self: end;
  margin-top: 12px;
  color: var(--g-accent-strong);
  font-size: 13px;
  font-weight: 800;
}
.box-strip {
  display: flex;
  gap: 12px;
  padding: 2px 2px 8px;
  overflow-x: auto;
  scroll-snap-type: x proximity;
}
.box-strip::-webkit-scrollbar {
  height: 6px;
}
.box-strip::-webkit-scrollbar-thumb {
  border-radius: 10px;
  background: var(--g-ice);
}
.box-card,
.add-box-card {
  position: relative;
  display: grid;
  grid-template-columns: 80px minmax(145px, 1fr);
  flex: 0 0 320px;
  min-width: 0;
  min-height: 120px;
  padding: 13px;
  border: 1px solid var(--g-line);
  border-radius: 14px;
  background: linear-gradient(145deg, #fff, var(--mb-surface-subtle));
  color: var(--g-accent-strong);
  text-align: left;
  cursor: pointer;
  scroll-snap-align: start;
  transition:
    transform 0.18s,
    border-color 0.18s,
    box-shadow 0.18s;
}
.box-card:hover {
  transform: translateY(-2px);
  border-color: var(--g-line);
  box-shadow: 0 10px 22px #345e8617;
}
.box-card.active {
  border-color: var(--g-accent);
  background: linear-gradient(145deg, var(--mb-surface-subtle), var(--mb-surface-subtle));
  box-shadow:
    inset 0 0 0 1px #3b7fc3,
    0 10px 24px #2e6ea522;
}
.mini-case {
  align-self: center;
  width: 68px;
  padding: 7px 6px 9px;
  border: 2px solid var(--g-line);
  border-radius: 7px;
  background: var(--mb-surface-subtle);
  box-shadow: 3px 5px 0 #8fa3b8;
}
.mini-grid {
  display: grid;
  grid-template-columns: repeat(8, 1fr);
  gap: 2px;
}
.mini-grid i {
  width: 5px;
  height: 5px;
  border: 1px solid var(--g-line);
  border-radius: 1px;
  background: #fff;
}
.box-card.active .mini-case {
  border-color: var(--g-accent);
  background: var(--mb-surface-subtle);
  box-shadow: 3px 5px 0 #6d9bc8;
}
.box-card-copy {
  display: flex;
  min-width: 0;
  flex-direction: column;
  padding: 2px 0;
}
.box-card-copy span {
  color: var(--g-accent-strong);
  font-size: 9px;
  letter-spacing: 1px;
}
.box-card-copy b {
  margin: 5px 0 3px;
  color: var(--g-ink);
  font-size: 15px;
  white-space: nowrap;
  text-overflow: ellipsis;
  overflow: hidden;
}
.box-card-copy small {
  color: var(--g-accent-strong);
  white-space: nowrap;
  text-overflow: ellipsis;
  overflow: hidden;
}
.box-card-stats {
  grid-column: 1/-1;
  display: flex;
  justify-content: space-between;
  gap: 8px;
  margin-top: 10px;
  padding-top: 9px;
  border-top: 1px solid var(--g-line);
  color: var(--g-accent-strong);
  font-size: 9px;
}
.box-card-stats b {
  color: var(--g-accent-strong);
  font-size: 13px;
}
.box-open-cue {
  grid-column: 1/-1;
  justify-self: end;
  margin-top: 8px;
  color: var(--g-accent-strong);
  font-size: 12px;
  font-weight: 700;
}
.add-box-card {
  display: flex;
  align-items: center;
  justify-content: center;
  flex-direction: column;
  flex-basis: 220px;
  border-style: dashed;
  color: var(--g-accent-strong);
  text-align: center;
}
.add-box-card span {
  font-size: 25px;
}
.add-box-card b {
  margin: 5px 0;
  font-size: 13px;
}
.add-box-card small {
  color: var(--g-accent-strong);
}
.first-box-tip,
.search-empty {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  padding: 17px 19px;
  border: 1px dashed var(--g-line);
  border-radius: 13px;
  background: var(--mb-surface-subtle);
  color: var(--g-accent-strong);
}
.first-box-tip > div {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.first-box-tip b {
  color: var(--g-ink);
}
.first-box-tip span {
  font-size: 13px;
}
.overview-summary {
  display: flex;
  align-items: center;
  gap: 13px;
  margin-bottom: 17px;
  padding: 13px 15px;
  border: 1px solid var(--g-line);
  border-radius: 13px;
  background: linear-gradient(135deg, var(--mb-surface-subtle), var(--mb-surface-subtle));
  color: var(--g-accent-strong);
}
.overview-summary > div:nth-child(2) {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 3px;
}
.overview-summary b {
  color: var(--g-ink);
}
.overview-summary span,
.overview-summary small {
  font-size: 12px;
}
.overview-summary small {
  margin-left: auto;
  color: var(--g-accent-strong);
}
.closed-box-mark {
  position: relative;
  flex: 0 0 42px;
  width: 42px;
  height: 28px;
  border: 2px solid var(--g-line);
  border-radius: 5px;
  background: var(--g-ice);
  box-shadow: 3px 4px 0 #9eb2c5;
}
.closed-box-mark:before {
  position: absolute;
  right: 2px;
  bottom: 4px;
  left: 2px;
  height: 7px;
  border-radius: 2px;
  content: '';
  background: repeating-linear-gradient(
    90deg,
    var(--g-accent) 0 3px,
    var(--mb-surface-subtle) 3px 5px
  );
}
.closed-box-mark span {
  position: absolute;
  top: -7px;
  right: -2px;
  left: -2px;
  height: 13px;
  border: 2px solid var(--g-line);
  border-radius: 5px;
  background: #f5f9fdd9;
  transform: skewX(-7deg);
}
.box-library.is-overview {
  padding: 24px;
  background: radial-gradient(circle at 50% 0, #fff, var(--mb-surface-subtle) 70%);
}
.box-library.is-overview .section-heading {
  align-items: center;
  margin-bottom: 17px;
}
.box-library.is-overview .section-heading h2 {
  font-size: 24px;
}
.box-library.is-overview .box-strip {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(290px, 1fr));
  gap: 16px;
  padding: 2px;
  overflow: visible;
  scroll-snap-type: none;
}
.box-library.is-overview .box-card,
.box-library.is-overview .add-box-card {
  flex: none;
  min-height: 168px;
  padding: 18px;
}
.box-library.is-overview .box-card {
  grid-template-columns: 88px minmax(145px, 1fr);
}
.box-library.is-overview .mini-case {
  transform: scale(1.12);
}
.box-library.is-overview .add-box-card {
  min-width: 0;
}
.box-workspace {
  display: grid;
  grid-template-columns: minmax(0, 1.55fr) minmax(310px, 0.65fr);
  align-items: start;
  gap: 18px;
  min-width: 0;
}
.detail-panel {
  min-width: 0;
  padding: 20px;
  border: 1px solid var(--g-line);
  border-radius: 18px;
  background: #fff;
  box-shadow: 0 8px 28px #29496b0a;
}
.detail-top {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}
.detail-top > div {
  min-width: 0;
}
.detail-top span {
  color: var(--g-accent-strong);
  font-size: 12px;
  letter-spacing: 1.3px;
}
.detail-top h2 {
  margin: 4px 0 5px;
  color: var(--g-ink);
  font-size: 22px;
  overflow-wrap: anywhere;
}
.detail-top p {
  margin: 0;
  color: var(--g-accent-strong);
  font-size: 13px;
  line-height: 1.5;
  overflow-wrap: anywhere;
}
.box-metrics {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 7px;
  margin: 18px 0;
}
.box-metrics > div {
  min-width: 0;
  padding: 12px 9px;
  border: 1px solid var(--g-line);
  border-radius: 10px;
  background: var(--mb-surface-subtle);
}
.box-metrics span {
  display: block;
  color: var(--g-accent-strong);
  font-size: 9px;
}
.box-metrics b {
  display: inline-block;
  max-width: 100%;
  margin-top: 6px;
  color: var(--g-ink);
  font-size: 19px;
  overflow-wrap: anywhere;
}
.box-metrics small {
  margin-left: 3px;
  color: var(--g-accent-strong);
  font-size: 8px;
}
.bin-detail {
  margin-top: 17px;
  padding-top: 17px;
  border-top: 1px solid var(--g-line);
}
.shelf-box-detail {
  min-width: 0;
  margin-top: 17px;
  padding-top: 17px;
  border-top: 1px solid var(--g-line);
}
.shelf-box-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  min-width: 0;
  gap: 12px;
}
.shelf-box-heading > div:first-child {
  display: flex;
  min-width: 0;
  flex-direction: column;
}
.shelf-box-heading > div:last-child {
  display: flex;
  flex: 0 0 auto;
  flex-direction: column;
  align-items: flex-end;
}
.shelf-box-heading span {
  color: var(--g-accent-strong);
  font-size: 12px;
}
.shelf-box-heading h3 {
  margin: 3px 0;
  color: var(--g-accent-strong);
  font-size: 23px;
  overflow-wrap: anywhere;
}
.shelf-box-heading small {
  color: var(--g-accent-strong);
  font-size: 12px;
  line-height: 1.5;
}
.shelf-box-heading .el-button {
  height: 23px;
  margin: 0;
}
.shelf-item-section,
.add-shelf-item {
  margin-top: 14px;
}
.shelf-section-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 9px;
  gap: 10px;
}
.shelf-section-title > div {
  display: flex;
  min-width: 0;
  flex-direction: column;
}
.shelf-section-title b {
  color: var(--g-ink);
  font-size: 13px;
}
.shelf-section-title span,
.shelf-section-title small {
  color: var(--g-accent-strong);
  font-size: 9px;
}
.shelf-section-title small {
  flex: 0 0 auto;
  padding: 4px 7px;
  border-radius: 7px;
  background: var(--mb-surface-subtle);
  color: var(--g-accent-strong);
  font-weight: 700;
}
.shelf-item-list {
  display: grid;
  max-height: 480px;
  padding-right: 3px;
  gap: 9px;
  overflow-y: auto;
}
.shelf-item-list article {
  padding: 11px;
  border: 1px solid var(--g-line);
  border-radius: 11px;
  background: #fff;
  box-shadow: 0 4px 12px #29496b0a;
}
.shelf-item-list article > header,
.shelf-item-list article > footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.shelf-item-list article > header {
  margin-bottom: 9px;
}
.shelf-item-list article > header span {
  color: var(--g-accent-strong);
  font-size: 12px;
  font-weight: 700;
}
.shelf-item-list article > header small {
  color: var(--g-accent-strong);
  font-size: 8px;
}
.shelf-item-list article > footer {
  margin-top: -1px;
}
.shelf-item-list article > footer .el-button {
  margin: 0;
}
.shelf-item-editor {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(100px, 0.42fr);
  gap: 0 9px;
}
.shelf-item-editor .full-row {
  grid-column: 1 / -1;
}
.shelf-item-editor :deep(.el-form-item) {
  margin-bottom: 10px;
}
.shelf-item-editor :deep(.el-form-item__label) {
  color: var(--g-accent-strong);
  font-size: 9px;
  line-height: 1.4;
}
.shelf-item-editor :deep(.el-input-number) {
  width: 100%;
}
.shelf-item-editor :deep(.el-input-number .el-input__inner) {
  text-align: left;
}
.empty-shelf-box {
  padding: 17px 12px;
  border: 1px dashed var(--g-line);
  border-radius: 10px;
  background: var(--mb-surface-subtle);
  color: var(--g-accent-strong);
  font-size: 12px;
  line-height: 1.55;
  text-align: center;
}
.add-shelf-item {
  padding: 13px;
  border: 1px solid var(--g-line);
  border-radius: 12px;
  background: var(--mb-surface-subtle);
}
.add-shelf-item > .el-button {
  width: 100%;
  margin: 1px 0 0;
}
.bin-heading {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
}
.bin-heading > div {
  display: flex;
  flex-direction: column;
}
.bin-heading span {
  color: var(--g-accent-strong);
  font-size: 12px;
}
.bin-heading h3 {
  margin: 3px 0 0;
  color: var(--g-accent-strong);
  font-size: 25px;
}
.bin-path {
  margin: 10px 0 13px;
  padding: 8px 10px;
  border-radius: 8px;
  background: var(--mb-surface-subtle);
  color: var(--g-accent-strong);
  font-size: 12px;
  overflow-wrap: anywhere;
}
.material-stack {
  display: grid;
  gap: 8px;
}
.material-stack article {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 10px;
  padding: 12px;
  border: 1px solid var(--g-line);
  border-radius: 11px;
  background: #fff;
}
.material-stack a {
  display: flex;
  min-width: 0;
  flex-direction: column;
  text-decoration: none;
}
.material-stack a span {
  color: var(--g-accent-strong);
  font-size: 9px;
}
.material-stack a b {
  margin: 3px 0;
  color: var(--g-accent-strong);
  white-space: nowrap;
  text-overflow: ellipsis;
  overflow: hidden;
}
.material-stack a small {
  color: var(--g-accent-strong);
  white-space: nowrap;
  text-overflow: ellipsis;
  overflow: hidden;
}
.material-stack article > div {
  display: flex;
  align-items: flex-end;
  flex-direction: column;
}
.material-stack strong {
  color: #1b755c;
  font-size: 18px;
}
.material-stack article > div span {
  color: var(--g-accent-strong);
  font-size: 9px;
}
.empty-bin,
.onboarding {
  display: flex;
  align-items: center;
  flex-direction: column;
  padding: 20px 8px;
  color: var(--g-accent-strong);
  text-align: center;
}
.empty-bin b {
  margin: 11px 0 5px;
  color: var(--g-accent-strong);
}
.empty-bin span {
  margin-bottom: 13px;
  font-size: 13px;
  line-height: 1.5;
}
.empty-cube {
  width: 42px;
  height: 35px;
  border: 2px solid var(--g-line);
  border-radius: 7px;
  background: linear-gradient(145deg, #fff, var(--g-ice));
  box-shadow: 4px 5px 0 #a4b7ca;
}
.select-hint {
  padding: 30px 0;
  color: var(--g-accent-strong);
  text-align: center;
}
.onboarding {
  align-items: flex-start;
  padding: 10px 4px;
  text-align: left;
}
.onboarding > span {
  color: var(--g-accent-strong);
  font-size: 9px;
  letter-spacing: 1.4px;
}
.onboarding h2 {
  margin: 8px 0;
  color: var(--g-ink);
  font-size: 24px;
  line-height: 1.25;
}
.onboarding p {
  margin: 0;
  color: var(--g-accent-strong);
  font-size: 13px;
  line-height: 1.7;
}
.onboarding ul {
  display: grid;
  width: 100%;
  margin: 20px 0;
  padding: 0;
  gap: 10px;
  list-style: none;
}
.onboarding li {
  display: flex;
  align-items: center;
  gap: 9px;
  color: var(--g-accent-strong);
  font-size: 13px;
}
.onboarding li b {
  display: grid;
  place-items: center;
  width: 24px;
  height: 24px;
  border-radius: 8px;
  background: var(--mb-surface-subtle);
  color: var(--g-accent-strong);
}
.drawer-actions {
  display: flex;
  gap: 8px;
  margin-bottom: 16px;
}
.drawer-actions .el-button {
  margin: 0;
}
.location-tree {
  background: transparent;
}
.location-tree :deep(.el-tree-node__content) {
  height: auto;
  min-height: 49px;
  margin: 2px 0;
  border-radius: 9px;
}
.location-tree :deep(.el-tree-node__content:hover) {
  background: var(--mb-surface-subtle);
}
.tree-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  min-width: 0;
  padding-right: 7px;
}
.tree-row > div {
  display: flex;
  align-items: center;
  min-width: 0;
  gap: 8px;
}
.tree-icon {
  display: grid;
  place-items: center;
  flex: 0 0 27px;
  width: 27px;
  height: 27px;
  border-radius: 8px;
  background: var(--mb-surface-subtle);
  color: var(--g-accent-strong);
}
.tree-icon.box {
  background: var(--mb-surface-subtle);
  color: var(--g-accent-strong);
}
.tree-icon.bin {
  background: var(--mb-surface-subtle);
  color: var(--g-accent-strong);
}
.tree-row > div > span:last-child {
  display: flex;
  min-width: 0;
  flex-direction: column;
}
.tree-row b {
  color: var(--g-ink);
  font-size: 13px;
  white-space: nowrap;
  text-overflow: ellipsis;
  overflow: hidden;
}
.tree-row small {
  color: var(--g-accent-strong);
  font-size: 9px;
}
.tree-actions {
  display: flex;
  opacity: 0;
  transition: opacity 0.15s;
}
.tree-row:hover .tree-actions {
  opacity: 1;
}
.dialog-form {
  margin-top: 16px;
}
.two {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px;
}
.layout-preview {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  margin: 4px 0 16px;
  padding: 13px;
  border: 1px solid var(--g-line);
  border-radius: 10px;
  background: var(--mb-surface-subtle);
  color: var(--g-accent-strong);
  font-size: 13px;
}
.layout-preview b {
  color: var(--g-accent-strong);
}
.layout-preview small {
  padding: 3px 6px;
  border-radius: 6px;
  background: var(--mb-surface-subtle);
  color: var(--g-accent-strong);
}
:deep(.el-dialog .el-select) {
  width: 100%;
}
@media (max-width: 1150px) {
  .box-workspace {
    grid-template-columns: 1fr;
  }
  .detail-panel {
    display: grid;
    grid-template-columns: minmax(240px, 0.65fr) minmax(0, 1fr);
    gap: 18px;
  }
  .detail-panel > .detail-top,
  .detail-panel > .box-metrics,
  .detail-panel > .el-alert {
    grid-column: 1;
  }
  .detail-panel > .bin-detail,
  .detail-panel > .shelf-box-detail,
  .detail-panel > .select-hint {
    grid-column: 2;
    grid-row: 1/5;
    margin: 0;
    padding: 0 0 0 18px;
    border-top: 0;
    border-left: 1px solid var(--g-line);
  }
  .detail-panel > .onboarding {
    grid-column: 1/-1;
  }
}
@media (max-width: 760px) {
  .header-actions {
    width: 100%;
  }
  .header-actions .el-button {
    flex: 1;
  }
  .section-heading {
    align-items: stretch;
    flex-direction: column;
  }
  .section-heading .el-input {
    max-width: none;
  }
  .section-tools {
    align-items: stretch;
    flex-direction: column !important;
  }
  .section-tools .el-input {
    width: 100%;
  }
  .warehouse-overview-summary {
    align-items: flex-start;
    flex-direction: column;
  }
  .warehouse-grid {
    grid-template-columns: 1fr;
  }
  .warehouse-card {
    grid-template-columns: 116px minmax(0, 1fr);
    min-height: 224px;
    padding: 19px 17px 16px;
  }
  .warehouse-visual {
    width: 104px;
    height: 104px;
    transform: scale(0.8);
    transform-origin: left center;
  }
  .warehouse-card-copy > b {
    font-size: 19px;
  }
  .box-card {
    flex-basis: 285px;
  }
  .box-library.is-overview {
    padding: 17px;
  }
  .box-library.is-overview .section-heading {
    align-items: stretch;
  }
  .box-library.is-overview .box-strip {
    grid-template-columns: 1fr;
  }
  .overview-summary {
    align-items: flex-start;
    flex-wrap: wrap;
  }
  .overview-summary small {
    width: 100%;
    margin-left: 55px;
  }
  .first-box-tip {
    align-items: stretch;
    flex-direction: column;
  }
  .detail-panel {
    display: block;
    padding: 16px;
  }
  .detail-panel > .bin-detail,
  .detail-panel > .shelf-box-detail,
  .detail-panel > .select-hint {
    margin-top: 17px;
    padding: 17px 0 0;
    border-top: 1px solid var(--g-line);
    border-left: 0;
  }
  .two {
    grid-template-columns: 1fr;
  }
  .layout-preview {
    flex-wrap: wrap;
  }
  .tree-actions {
    opacity: 1;
  }
}
.bin-heading {
  align-items: center;
}

.bin-save-state {
  flex: 0 0 auto;
  padding: 5px 8px;
  border-radius: 7px;
  background: var(--mb-surface-subtle);
  color: var(--g-accent-strong) !important;
  font-size: 12px !important;
  letter-spacing: 0 !important;
}

.bin-save-state.pending,
.bin-save-state.saving {
  background: #fff4de;
  color: #a66b14 !important;
}

.bin-save-state.saved {
  background: #e5f5ee;
  color: #1d7a5e !important;
}

.bin-save-state.error {
  background: #fdeceb;
  color: #c34f49 !important;
}

.bin-content-editor {
  margin-top: 15px;
  padding: 15px;
  border: 1px solid var(--g-line);
  border-radius: 12px;
  background: var(--mb-surface-subtle);
}

.bin-content-editor :deep(.el-form-item) {
  margin-bottom: 15px;
}

.bin-content-editor :deep(.el-form-item__label) {
  color: var(--g-accent-strong);
  font-size: 13px;
  line-height: 1.4;
}

.bin-content-editor :deep(.el-input-number) {
  width: 100%;
}

.bin-content-editor :deep(.el-input-number .el-input__inner) {
  text-align: left;
}

.legacy-content-tip {
  margin: -2px 0 13px;
  padding: 9px 10px;
  border-radius: 8px;
  background: var(--mb-surface-subtle);
  color: var(--g-accent-strong);
  font-size: 12px;
  line-height: 1.55;
}

.bin-editor-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 28px;
  gap: 10px;
}

.bin-editor-footer > span {
  color: var(--g-accent-strong);
  font-size: 9px;
  line-height: 1.45;
}

.detail-actions {
  display: flex;
  align-items: center;
  flex: 0 0 auto;
  gap: 8px;
}

.detail-actions .el-button {
  margin: 0;
}

.box-card-copy em {
  margin-top: 5px;
  color: var(--g-accent-strong);
  font-size: 9px;
  font-style: normal;
}

.mini-mixed-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  height: 47px;
  gap: 3px;
}

.mini-case.rack-case {
  align-self: center;
  width: 54px;
  height: 76px;
  padding: 5px;
  border-color: var(--g-accent);
  border-radius: 4px;
  background: linear-gradient(90deg, var(--g-accent), var(--g-accent), var(--g-accent));
  box-shadow: 3px 5px 0 #1c242c;
}

.box-card.active .mini-case.rack-case {
  border-color: var(--g-accent);
  background: linear-gradient(90deg, var(--g-accent), var(--g-accent), var(--g-accent));
  box-shadow: 3px 5px 0 #2e506d;
}

.mini-case.shelf-rack-case {
  align-self: center;
  width: 62px;
  height: 76px;
  padding: 5px 7px;
  border-color: var(--g-line);
  border-radius: 3px;
  background: linear-gradient(
    90deg,
    var(--g-accent),
    var(--mb-surface-subtle) 12% 88%,
    var(--g-accent)
  );
  box-shadow: 3px 5px 0 #7f8c97;
}

.box-card.active .mini-case.shelf-rack-case {
  border-color: var(--g-accent);
  background: linear-gradient(
    90deg,
    var(--g-accent),
    var(--mb-surface-subtle) 12% 88%,
    var(--g-accent)
  );
  box-shadow: 3px 5px 0 #547b9c;
}

.mini-shelf-rack {
  display: grid;
  width: 100%;
  height: 100%;
  grid-template-rows: repeat(6, minmax(0, 1fr));
  gap: 2px;
}

.mini-shelf-rack > i {
  position: relative;
  display: flex;
  align-items: flex-end;
  justify-content: center;
  min-height: 0;
  border-bottom: 2px solid var(--g-accent);
  gap: 2px;
}

.mini-shelf-rack > i > span {
  width: 11px;
  height: 6px;
  border-radius: 1px 1px 0 0;
  background: var(--g-accent);
}

.mini-rack-grid {
  display: grid;
  width: 100%;
  height: 100%;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  grid-template-rows: repeat(20, minmax(0, 1fr));
  gap: 1px;
}

.mini-rack-grid i {
  min-width: 0;
  min-height: 0;
  border-radius: 1px;
  background: var(--g-ice);
  box-shadow: inset 0 0 0 0.5px #8896a3;
}

.mini-half {
  display: grid;
  min-width: 0;
  gap: 1px;
}

.mini-half.module-small {
  grid-template-columns: repeat(4, minmax(0, 1fr));
  grid-template-rows: repeat(7, minmax(0, 1fr));
}

.mini-half.module-large {
  grid-template-columns: repeat(2, minmax(0, 1fr));
  grid-template-rows: repeat(4, minmax(0, 1fr));
}

.mini-half i {
  min-width: 0;
  min-height: 0;
  border: 1px solid var(--g-line);
  border-radius: 1px;
  background: #fff;
}

.organizer-style-options {
  display: grid;
  width: 100%;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.organizer-style-options > button {
  display: grid;
  grid-template-columns: 62px minmax(0, 1fr);
  min-width: 0;
  padding: 12px;
  border: 1px solid var(--g-line);
  border-radius: 12px;
  background: var(--mb-surface-subtle);
  color: var(--g-accent-strong);
  text-align: left;
  cursor: pointer;
  gap: 3px 11px;
}

.organizer-style-options > button:hover,
.organizer-style-options > button.active {
  border-color: var(--g-accent);
  background: var(--mb-surface-subtle);
  box-shadow: inset 0 0 0 1px #4d91cf;
}

.organizer-style-options > button > b {
  align-self: end;
  color: var(--g-ink);
  font-size: 13px;
}

.organizer-style-options > button > small {
  color: var(--g-accent-strong);
  font-size: 12px;
}

.style-icon {
  display: grid;
  grid-row: 1 / 3;
  align-self: center;
  width: 56px;
  height: 42px;
  padding: 4px;
  border: 2px solid var(--g-line);
  border-radius: 6px;
  background: var(--mb-surface-subtle);
  gap: 2px;
}

.style-icon.standard {
  grid-template-columns: repeat(4, 1fr);
}

.style-icon.standard i,
.style-icon.mixed i {
  min-width: 0;
  border: 1px solid var(--g-line);
  border-radius: 1px;
  background: #fff;
}

.style-icon.mixed {
  grid-template-columns: 1fr 1fr;
}

.style-icon.mixed > i {
  grid-column: 1;
}

.style-icon.mixed > em {
  display: grid;
  grid-column: 2;
  grid-row: 1 / 5;
  grid-template-columns: repeat(2, 1fr);
  gap: 1px;
}

.style-icon.rack {
  height: 62px;
  padding: 4px;
  border-color: var(--g-accent);
  border-radius: 4px;
  background: var(--g-accent);
  grid-template-columns: repeat(5, minmax(0, 1fr));
  grid-template-rows: repeat(20, minmax(0, 1fr));
  gap: 1px;
}

.style-icon.rack i {
  min-width: 0;
  min-height: 0;
  border-radius: 1px;
  background: var(--g-ice);
}

.style-icon.shelf-rack {
  height: 62px;
  padding: 4px 6px;
  border-color: var(--g-accent);
  border-radius: 3px;
  background: linear-gradient(
    90deg,
    var(--g-accent),
    var(--mb-surface-subtle) 12% 88%,
    var(--g-accent)
  );
  grid-template-rows: repeat(6, minmax(0, 1fr));
  gap: 2px;
}

.style-icon.shelf-rack > i {
  display: flex;
  align-items: flex-end;
  justify-content: center;
  min-height: 0;
  border-bottom: 2px solid var(--g-accent);
  gap: 2px;
}

.style-icon.shelf-rack > i > em {
  width: 13px;
  height: 6px;
  border-radius: 1px 1px 0 0;
  background: var(--g-accent);
}

.create-half-config {
  display: grid;
  margin: -2px 0 15px;
  padding: 13px;
  border: 1px solid var(--g-line);
  border-radius: 11px;
  background: var(--mb-surface-subtle);
  gap: 9px;
}

.create-half-row {
  display: grid;
  grid-template-columns: 58px minmax(0, 1fr);
  align-items: center;
  gap: 10px;
}

.create-half-row > span {
  color: var(--g-accent-strong);
  font-size: 13px;
  font-weight: 700;
}

.create-half-row :deep(.el-radio-group) {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.create-half-row :deep(.el-radio-button__inner) {
  width: 100%;
}

.create-half-config p {
  margin: 2px 0 0 68px;
  color: var(--g-accent-strong);
  font-size: 12px;
}

.mini-case {
  position: relative;
  overflow: visible;
}

.mini-case-cover {
  position: absolute;
  z-index: 3;
  inset: -3px -4px;
  border: 2px solid var(--g-line);
  border-radius: 7px;
  background: linear-gradient(145deg, #f9fcffeb, #dbe7f2e8);
  box-shadow:
    inset 0 0 0 3px #ffffff8c,
    2px 4px 7px #34506b26;
  opacity: 0;
  transform: translateY(-11px) scaleY(0.78);
  transform-origin: 50% 0;
  transition:
    transform 0.25s ease,
    opacity 0.2s ease;
  pointer-events: none;
}

.mini-case-cover::after {
  position: absolute;
  right: 9px;
  bottom: 6px;
  left: 9px;
  height: 3px;
  border-radius: 3px;
  content: '';
  background: var(--g-accent);
}

.box-library.is-overview .mini-case-cover {
  opacity: 0.94;
  transform: none;
}

.box-workspace.is-closing {
  pointer-events: none;
}

.box-workspace.is-rack {
  grid-template-columns: minmax(0, 1.9fr) minmax(340px, 0.55fr);
}

.box-library.is-entering .box-card,
.box-library.is-entering .add-box-card {
  animation: box-join-overview 0.42s cubic-bezier(0.2, 0.78, 0.28, 1) both;
}

.box-library.is-entering .box-card.just-closed {
  animation: closed-box-join-overview 0.46s cubic-bezier(0.2, 0.82, 0.28, 1) both;
}

.box-library.is-entering .box-card.just-closed .mini-case-cover {
  animation: mini-cover-settle 0.4s 0.04s ease both;
}

@keyframes box-join-overview {
  from {
    opacity: 0;
    transform: translateY(14px) scale(0.96);
  }

  to {
    opacity: 1;
    transform: none;
  }
}

@keyframes closed-box-join-overview {
  0% {
    opacity: 0;
    transform: translateY(46px) scale(1.08);
  }

  62% {
    opacity: 1;
    transform: translateY(-4px) scale(0.99);
  }

  100% {
    opacity: 1;
    transform: none;
  }
}

@keyframes mini-cover-settle {
  from {
    opacity: 0.3;
    transform: translateY(-13px) scaleY(0.72);
  }

  to {
    opacity: 0.94;
    transform: none;
  }
}

@media (prefers-reduced-motion: reduce) {
  .box-library.is-entering .box-card,
  .box-library.is-entering .add-box-card,
  .box-library.is-entering .box-card.just-closed,
  .box-library.is-entering .box-card.just-closed .mini-case-cover {
    animation-duration: 0.01ms;
  }
}

@media (max-width: 1750px) {
  .box-workspace.is-rack {
    grid-template-columns: 1fr;
  }

  .box-workspace.is-rack .detail-panel {
    display: grid;
    grid-template-columns: minmax(240px, 0.65fr) minmax(0, 1fr);
    gap: 18px;
  }

  .box-workspace.is-rack .detail-panel > .detail-top,
  .box-workspace.is-rack .detail-panel > .box-metrics,
  .box-workspace.is-rack .detail-panel > .el-alert {
    grid-column: 1;
  }

  .box-workspace.is-rack .detail-panel > .bin-detail,
  .box-workspace.is-rack .detail-panel > .shelf-box-detail,
  .box-workspace.is-rack .detail-panel > .select-hint {
    grid-row: 1 / 5;
    grid-column: 2;
    margin: 0;
    padding: 0 0 0 18px;
    border-top: 0;
    border-left: 1px solid var(--g-line);
  }
}

@media (max-width: 640px) {
  .organizer-style-options {
    grid-template-columns: 1fr;
  }

  .create-half-row {
    grid-template-columns: 1fr;
  }

  .create-half-config p {
    margin-left: 0;
  }

  .box-workspace.is-rack .detail-panel {
    display: block;
  }

  .box-workspace.is-rack .detail-panel > .bin-detail,
  .box-workspace.is-rack .detail-panel > .shelf-box-detail,
  .box-workspace.is-rack .detail-panel > .select-hint {
    margin-top: 17px;
    padding: 17px 0 0;
    border-top: 1px solid var(--g-line);
    border-left: 0;
  }
}

@media (max-width: 980px) {
  .organizer-style-options {
    grid-template-columns: 1fr;
  }
}
</style>
