import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import ShelfRack6 from '../src/components/locations/ShelfRack6.vue'
import type { OrganizerLocation } from '../src/components/locations/OrganizerBox3D.vue'

function createRack(): OrganizerLocation {
  return {
    id: 600,
    parent_id: null,
    code: 'RACK-06',
    name: '六层材料货架',
    type: 'box',
    full_path: '研发仓库 / 六层材料货架',
    manager: '',
    notes: '包装材料与结构件',
    is_active: true,
    organizer_style: 'shelf_rack_6',
  }
}

function createShelves(): OrganizerLocation[] {
  return Array.from({ length: 6 }, (_, index) => ({
    id: 610 + index,
    parent_id: 600,
    code: `RACK-06-L0${index + 1}`,
    name: `第 ${index + 1} 层`,
    type: 'shelf',
    full_path: `研发仓库 / 六层材料货架 / 第 ${index + 1} 层`,
    manager: '',
    notes: '六层箱式货架固定层位',
    is_active: true,
  }))
}

function createBox(id: number, shelfId: number, name: string): OrganizerLocation {
  return {
    id,
    parent_id: shelfId,
    code: `RACK-06-B${id}`,
    name,
    type: 'container',
    full_path: `研发仓库 / 六层材料货架 / ${name}`,
    manager: '',
    notes: '',
    is_active: true,
    organizer_style: 'shelf_storage_box',
  }
}

describe('ShelfRack6', () => {
  it('renders six empty levels and lets the user place a box on each level', async () => {
    const shelves = createShelves()
    const wrapper = mount(ShelfRack6, {
      props: {
        rack: createRack(),
        shelves,
        storageBoxes: [],
        items: [],
        materials: [],
        selectedBoxId: null,
        closing: false,
      },
    })

    expect(wrapper.findAll('.rack-level')).toHaveLength(6)
    expect(wrapper.findAll('.empty-level')).toHaveLength(6)
    expect(wrapper.findAll('button.add-storage-box')).toHaveLength(6)
    expect(wrapper.text()).toContain('每层初始为空')

    await wrapper.find('button.add-storage-box').trigger('click')
    expect(wrapper.emitted('addBox')?.[0]?.[0]).toEqual(shelves[5])
  })

  it('shows multiple boxes and multiple materials, then emits selection and deletion', async () => {
    const shelves = createShelves()
    const fastenerBox = createBox(701, shelves[0].id, '紧固件箱')
    const cableBox = createBox(702, shelves[0].id, '线缆附件箱')
    const items: OrganizerLocation[] = [
      {
        id: 801,
        parent_id: fastenerBox.id,
        code: 'SBX-701-I001',
        name: 'M3 内六角螺丝',
        type: 'bin',
        full_path: `${fastenerBox.full_path} / M3 内六角螺丝`,
        manager: '',
        notes: '',
        is_active: true,
        bin_material_name: 'M3 内六角螺丝',
        bin_quantity: 120,
        bin_content_notes: '黑色',
      },
      {
        id: 802,
        parent_id: fastenerBox.id,
        code: 'SBX-701-I002',
        name: 'M3 螺母',
        type: 'bin',
        full_path: `${fastenerBox.full_path} / M3 螺母`,
        manager: '',
        notes: '',
        is_active: true,
        bin_material_name: 'M3 螺母',
        bin_quantity: 80,
        bin_content_notes: '',
      },
    ]
    const wrapper = mount(ShelfRack6, {
      props: {
        rack: createRack(),
        shelves,
        storageBoxes: [fastenerBox, cableBox],
        items,
        materials: [],
        selectedBoxId: fastenerBox.id,
        closing: false,
      },
    })

    const boxes = wrapper.findAll('.storage-box')
    expect(boxes).toHaveLength(2)
    expect(boxes[0].classes()).toContain('selected')
    expect(boxes[0].text()).toContain('M3 内六角螺丝')
    expect(boxes[0].text()).toContain('M3 螺母')
    expect(boxes[0].text()).toContain('200 件')

    await boxes[1].trigger('click')
    expect(wrapper.emitted('selectBox')?.[0]?.[0]).toEqual(cableBox)

    await boxes[1].find('button.box-delete').trigger('click')
    expect(wrapper.emitted('deleteBox')?.[0]?.[0]).toEqual(cableBox)
  })
})
