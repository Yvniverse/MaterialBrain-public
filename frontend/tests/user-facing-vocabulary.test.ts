import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { sanitizeBusinessCopy } from '../src/utils/businessCopy'

const userFacingFiles = [
  'src/views/Locations.vue',
  'src/views/Projects.vue',
  'src/views/Products.vue',
  'src/views/Materials.vue',
  'src/views/MaterialDetail.vue',
  'src/components/agent/AgentResultCard.vue',
  'src/components/agent/ComponentCandidateCard.vue',
  'src/components/agent/MaterialResultCard.vue',
  'src/components/agent/FloatingAgentConversation.vue',
]

const forbidden = [
  String.fromCodePoint(0x79cb, 0x62db),
  String.fromCodePoint(0x4f5c, 0x54c1, 0x96c6),
  String.fromCodePoint(0x6c42, 0x804c),
  ['Portfolio', 'Showcase'].join(' '),
  ['Resume', 'Project'].join(' '),
  ['Job', 'Hunting'].join(' '),
]

describe('user-facing business vocabulary', () => {
  it('contains no recruiting or showcase copy in normal warehouse UI', () => {
    const source = userFacingFiles
      .map((file) => readFileSync(resolve(process.cwd(), file), 'utf8'))
      .join('\n')
    for (const phrase of forbidden) expect(source).not.toContain(phrase)
  })

  it('keeps structured results primary over duplicate narrative text', () => {
    const desktop = readFileSync(
      resolve(process.cwd(), 'src/components/agent/AgentResultCard.vue'),
      'utf8',
    )
    const floating = readFileSync(
      resolve(process.cwd(), 'src/components/agent/FloatingAgentConversation.vue'),
      'utf8',
    )
    for (const source of [desktop, floating]) {
      expect(source).toContain('entities.product_bom_alternates')
      expect(source).toContain('entities.component_relations')
      expect(source).toContain('entities.cable_search')
    }
  })

  it('neutralizes legacy seeded copy but preserves stable engineering codes', () => {
    const recruiting = String.fromCodePoint(0x79cb, 0x62db, 0x4f5c, 0x54c1, 0x5c55, 0x793a)
    const source = `${recruiting}：${['Portfolio', 'Demo', 'v2'].join(' ')} PORT-CAN-TCAN1044`
    const result = sanitizeBusinessCopy(source)
    expect(result).not.toContain(recruiting)
    expect(result).not.toContain(['Portfolio', 'Demo'].join(' '))
    expect(result).toContain('PORT-CAN-TCAN1044')
    expect(sanitizeBusinessCopy('PORTFOLIO-CBL-PF-00008')).toBe('PORTFOLIO-CBL-PF-00008')
    expect(sanitizeBusinessCopy('DEMO-MATERIAL-001')).toBe('DEMO-MATERIAL-001')
    expect(sanitizeBusinessCopy('Demo Board Rev A')).toBe('Demo Board Rev A')
    expect(sanitizeBusinessCopy('Supplier portfolio: CAN transceivers')).toBe(
      'Supplier portfolio: CAN transceivers',
    )
  })

  it('removes residual synthetic stock and demo qualifiers from business copy', () => {
    expect(sanitizeBusinessCopy('LCSC catalog identity; demo stock is synthetic.')).toBe(
      'LCSC catalog identity',
    )
    expect(sanitizeBusinessCopy('48V motor driver; demo metadata')).toBe(
      '48V motor driver',
    )
  })
})
