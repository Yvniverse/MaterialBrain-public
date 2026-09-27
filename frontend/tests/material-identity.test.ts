import { describe, expect, it } from 'vitest'
import type { Material } from '../src/types'
import {
  isInternalPengkaPart,
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

  it('labels PENGKA DEMO identifiers as internal part numbers instead of vendor MPNs', () => {
    const item = material({
      name: '48V 转 5V/5A 电源模块',
      mpn: 'DEMO-DCDC-48-5-5A',
      manufacturer: 'PENGKA Robotics',
    })
    expect(isInternalPengkaPart(item)).toBe(true)
    expect(materialPrimaryIdentity(item)).toBe('48V 转 5V/5A 电源模块')
    expect(materialSecondaryIdentity(item)).toBe('内部料号 DEMO-DCDC-48-5-5A')
    expect(materialPartNumberText(item)).toBe('内部料号 DEMO-DCDC-48-5-5A')
  })

  it('does not expose PORTFOLIO cable fallback MPN when a natural catalog identity exists', () => {
    const item = material({
      name: 'HC-0.8-7PWT 双头端子线',
      mpn: 'PORTFOLIO-CBL-PF-00042',
      attributes: {
        material_kind: 'cable',
        catalog_mpn_hint: 'HC-0.8-7PWT',
        length_cm: '20.0',
      },
    })
    expect(materialPrimaryIdentity(item)).toBe('HC-0.8-7PWT · 20 cm')
    expect(materialPrimaryIdentity(item)).not.toContain('PORTFOLIO')
  })
})
