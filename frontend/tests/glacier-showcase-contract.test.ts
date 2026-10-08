import { describe, expect, it } from 'vitest'
import {
  assertCaptureEvidence,
  assertLayout,
  assertRenderedScene,
  assertRuntimeIdentity,
  episodeRecoveryEvidence,
  requiredShots,
  responsiveViewports,
} from '../tools/showcase_contract.mjs'

const revision = 'a'.repeat(40)
const scene = { renderer: 'THREE.WebGLRenderer', triangles: 4500, draw_calls: 80, assets: 12, frames: 80, goal_ids: ['P-IC', 'P-LAB'] }
const layout = {
  viewport: { width: 1440, height: 900 }, documentWidth: 1440, shellLight: true,
  stage: { width: 840, height: 600, top: 145, visibleArea: 504000 }, obscuredPrimaryControls: [],
}

describe('real screenshot release contract', () => {
  it('checks the exact required widths and distinct gallery roles', () => {
    expect(responsiveViewports.map(viewport => viewport.width)).toEqual([390, 768, 1024, 1440, 1728, 1920])
    expect(new Set(requiredShots.map(shot => shot.name)).size).toBe(6)
    expect(requiredShots.find(shot => shot.kind === 'recovery')?.route).toContain('workspace=robot-lab')
  })
  it('rejects mismatched deployment metadata', () => {
    expect(() => assertRuntimeIdentity({ frontend_build_sha: revision, backend_build_sha: 'b'.repeat(40) }, revision)).toThrow('differ')
    expect(() => assertRuntimeIdentity({ frontend_build_sha: 'unknown', backend_build_sha: revision }, revision)).toThrow('actual')
  })
  it('requires the production renderer and actual geometry', () => {
    expect(() => assertRenderedScene({ ...scene, draw_calls: 0 }, 'laboratory')).toThrow('geometry')
    expect(() => assertRenderedScene({ ...scene, renderer: 'placeholder' }, 'warehouse')).toThrow('renderer')
    expect(() => assertRenderedScene({ ...scene, cameraInsideAsset: true }, 'warehouse')).toThrow('inside')
  })
  it('rejects offscreen equipment and permits a rendered static scene', () => {
    expect(() => assertRenderedScene({ ...scene, selectedCoverage: { area: 0 } }, 'warehouse')).toThrow('outside')
    expect(() => assertRenderedScene({ ...scene, frames: 0 }, 'laboratory')).toThrow('frame')
    expect(() => assertRenderedScene({ ...scene, frames: 4 }, 'laboratory')).not.toThrow()
  })
  it('rejects a canvas thumbnail or a stage below the first viewport', () => {
    expect(() => assertLayout({ ...layout, stage: { ...layout.stage, width: 280 } }, true)).toThrow('too small')
    expect(() => assertLayout({ ...layout, stage: { ...layout.stage, top: 700 } }, true)).toThrow('below')
    expect(() => assertLayout({ ...layout, stage: { ...layout.stage, visibleArea: 100000 } }, true)).toThrow('focal')
  })
  it.each(responsiveViewports.filter(viewport => viewport.width >= 768))('rejects a clipped stage at $width pixels while allowing two pixels of rounding', viewport => {
    const stage = { width: Math.min(840, viewport.width - 32), height: 600, top: viewport.height - 597, visibleArea: 504000 }
    const desktop = { ...layout, viewport, documentWidth: viewport.width, stage }
    expect(() => assertLayout(desktop, true)).toThrow('extends below')
    expect(() => assertLayout({ ...desktop, stage: { ...stage, top: viewport.height - 598 } }, true)).not.toThrow()
  })
  it('rejects overflow, the legacy shell and an obscured primary action', () => {
    expect(() => assertLayout({ ...layout, documentWidth: 1500 }, false)).toThrow('overflow')
    expect(() => assertLayout({ ...layout, shellLight: false }, false)).toThrow('shell')
    expect(() => assertLayout({ ...layout, obscuredPrimaryControls: ['Start'] }, true)).toThrow('obscured')
    expect(() => assertLayout({ ...layout, overlappingBotControls: ['Pause'] }, true)).toThrow('robot overlaps')
  })
  it('accepts a useful mobile stage without imposing desktop columns', () => {
    expect(() => assertLayout({ ...layout, viewport: { width: 390, height: 844 }, documentWidth: 390, stage: { width: 358, height: 360, top: 290, visibleArea: 128880 } }, true)).not.toThrow()
    expect(() => assertLayout({ ...layout, viewport: { width: 390, height: 844 }, documentWidth: 390, stage: { width: 358, height: 620, top: 290, visibleArea: 198332 } }, true)).not.toThrow()
  })
  it('does not accept an idle robot image as a recovery trace', () => {
    expect(() => assertCaptureEvidence('recovery', { kind: 'recovery', verified: true, scene, recovery_events: ['REPLAN_READY'] })).toThrow('actual server')
    const serverScene = { ...scene, spatial_execution: { mission_id: 'SM-observed', execution_boundary: 'ros2_nav2_simulation', hardware_control: false, completed_goal_ids: ['P-CABLE'] } }
    const events = ['handoff_verified', 'obstacle_added', 'replanning'].map((type, sequence) => ({
      type, sequence, mission_id: 'SM-observed', event_id: `SM-observed:${sequence}`, timestamp: '2026-10-08T01:00:00Z',
      details: { execution_boundary: 'ros2_nav2_simulation', hardware_control: false },
    }))
    const episode = { mission_id: 'SM-observed', source: 'observed_execution_events', execution_boundary: 'ros2_nav2_simulation', inventory_written: false, steps: events.map(outcome => ({ source: 'observed_execution_event', outcome })) }
    const recovery = episodeRecoveryEvidence(episode, { mission_id: 'SM-observed', path: '/api/v1/spatial/missions/SM-observed/episode', response_sha256: 'd'.repeat(64) })
    expect(() => assertCaptureEvidence('recovery', { kind: 'recovery', verified: true, scene: serverScene, recovery_events: [] })).toThrow('trace')
    expect(() => assertCaptureEvidence('recovery', { kind: 'recovery', verified: true, scene: serverScene, recovery_events: events })).toThrow('episode response')
    expect(() => assertCaptureEvidence('recovery', { kind: 'recovery', verified: true, scene: serverScene, ...recovery })).not.toThrow()
    expect(() => episodeRecoveryEvidence(episode, { mission_id: 'SM-other' })).toThrow('another mission')
    expect(() => assertCaptureEvidence('recovery', { kind: 'recovery', verified: true, scene: serverScene, ...recovery, recovery_source: { ...recovery.recovery_source, path: '/api/v1/local/episode' } })).toThrow('episode response')
  })
  it('requires actual candidate, inventory and selected storage state', () => {
    expect(() => assertCaptureEvidence('agent', { kind: 'agent', verified: true })).toThrow('grounded')
    expect(() => assertCaptureEvidence('dashboard', { kind: 'dashboard', verified: true, summary_values: ['0'] })).toThrow('empty')
    expect(() => assertCaptureEvidence('storage', { kind: 'storage', verified: true, equipment_selector: '.drawer' })).toThrow('compartment')
    expect(() => assertCaptureEvidence('laboratory', { kind: 'laboratory', verified: true, scene: { ...scene, goal_ids: ['P-IC'] } })).toThrow('multi-stop')
  })
})
