import { describe, expect, it } from 'vitest'
import type { Material } from '../src/types'
import {
  isInternalPart,
  materialPartNumberText,
  materialPrimaryIdentity,
  materialSecondaryIdentity,
} from '../src/utils/materialIdentity'

function material(overrides: Partial<Material>): Material {
  return {
    id: 1,
    code: 'MAT-1',
    name: 'Test material',
    category_id: null,
    location_id: null,
    supplier_id: null,
    mpn: 'MPN-1',
    specification: '',
    package: '',
    footprint: '',
    manufacturer: 'Vendor',
    unit: 'pcs',
    unit_price: '0',
    safety_stock: '0',
    target_stock: '0',
    quantity: '0',
    reserved_quantity: '0',
    available_quantity: '0',
    barcode: '',
    lifecycle_status: 'active',
    rohs_status: 'unknown',
    datasheet_url: '',
    tags: [],
    attributes: {},
    notes: '',
    is_active: true,
    created_at: '',
    updated_at: '',
    ...overrides,
  }
}

describe('material business display identity', () => {
  it('keeps vendor MPN as the normal public identity', () => {
    const item = material({ name: 'INA240A1', mpn: 'INA240A1DR', manufacturer: 'Texas Instruments' })
    expect(materialPrimaryIdentity(item)).toBe('INA240A1DR')
    expect(materialSecondaryIdentity(item)).toBe('INA240A1')
    expect(materialPartNumberText(item)).toBe('INA240A1DR')
  })

  it('labels an explicitly internal identifier without treating it as a vendor MPN', () => {
    const item = material({
      name: '48V 转 5V/5A 电源模块',
      mpn: 'DEMO-DCDC-48-5-5A',
      manufacturer: 'Sample Robotics',
      attributes: { part_number_kind: 'internal' },
    })
    expect(isInternalPart(item)).toBe(true)
    expect(materialPrimaryIdentity(item)).toBe('48V 转 5V/5A 电源模块')
    expect(materialSecondaryIdentity(item)).toBe('内部料号 DEMO-DCDC-48-5-5A')
    expect(materialPartNumberText(item)).toBe('内部料号 DEMO-DCDC-48-5-5A')
  })

  it('uses catalog identity for an explicitly internal cable identifier', () => {
    const item = material({
      name: 'HC-0.8-7PWT 双头端子线',
      mpn: 'SAMPLE-CBL-00042',
      attributes: {
        part_number_kind: 'internal',
        material_kind: 'cable',
        catalog_mpn_hint: 'HC-0.8-7PWT',
        length_cm: '20.0',
      },
    })
    expect(materialPrimaryIdentity(item)).toBe('HC-0.8-7PWT · 20 cm')
    expect(materialSecondaryIdentity(item)).toBe('内部料号 SAMPLE-CBL-00042')
  })

  it('does not infer internal identity from a manufacturer name or code prefix', () => {
    const item = material({ mpn: 'DEMO-DEV-1', manufacturer: 'Sample Robotics' })
    expect(isInternalPart(item)).toBe(false)
    expect(materialPrimaryIdentity(item)).toBe('DEMO-DEV-1')
  })

  it('preserves a vendor cable MPN when catalog hints differ', () => {
    const item = material({
      mpn: 'VENDOR-CBL-1',
      attributes: { material_kind: 'cable', catalog_mpn_hint: 'CATALOG-CBL-2', length_cm: 20 },
    })
    expect(materialPrimaryIdentity(item)).toBe('VENDOR-CBL-1')
  })
})
