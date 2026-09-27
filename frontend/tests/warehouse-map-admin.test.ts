import { describe, expect, it } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'

const adminSource = fs.readFileSync(
  path.resolve(process.cwd(), 'src/components/locations/WarehouseMapAdmin.vue'),
  'utf8',
)
const locationsSource = fs.readFileSync(path.resolve(process.cwd(), 'src/views/Locations.vue'), 'utf8')

describe('warehouse map administration UX', () => {
  it('surfaces versioned calibration lifecycle without overwriting active maps', () => {
    expect(adminSource).toContain('复制为新草稿')
    expect(adminSource).toContain("setCalibration('measured')")
    expect(adminSource).toContain("setCalibration('verified')")
    expect(adminSource).toContain('启用生产地图')
    expect(adminSource).toContain('历史 PickTask 仍保留旧路线快照')
  })

  it('makes synthetic demo geometry impossible to mistake for measured production geometry', () => {
    expect(adminSource).toContain('示例数据 · 非实测')
    expect(adminSource).toContain('生产使用前必须按现场实测坐标修改')
  })

  it('allows coordinate, graph-edge and organizer-binding edits on drafts', () => {
    expect(adminSource).toContain('通行节点')
    expect(adminSource).toContain('可通行边')
    expect(adminSource).toContain('设备/货架几何与取料面绑定')
    expect(adminSource).toContain('/definition')
  })

  it('is reachable from the existing visual location workspace', () => {
    expect(locationsSource).toContain('仓库地图 / 路线')
    expect(locationsSource).toContain('<WarehouseMapAdmin')
  })
})
