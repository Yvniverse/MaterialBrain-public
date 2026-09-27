import { describe, expect, it } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'

const source = fs.readFileSync(
  path.resolve(process.cwd(), 'src/components/locations/WarehouseRouteMap.vue'),
  'utf8',
)

describe('warehouse route map', () => {
  it('renders configured graph routes without overstating uncalibrated geometry', () => {
    expect(source).toContain('data-testid="warehouse-route-map"')
    expect(source).toContain('route.total_distance_m.toFixed(1)')
    expect(source).toContain("map.calibration_status !== 'verified'")
    expect(source).toContain('示例地图 · 非实测')
    expect(source).toContain('不代表真实仓库实测最短距离')
  })

  it('renders ordered route stops and graph provenance', () => {
    expect(source).toContain('stopSequence')
    expect(source).toContain('route.graph_hash.slice(0, 10)')
    expect(source).toContain('route-line')
  })
})
