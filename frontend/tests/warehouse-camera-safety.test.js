import { describe, expect, it } from 'vitest'
import * as THREE from 'three'
import {
  cameraInsideAnyBox,
  chooseFocusPose,
  occlusionCount,
  perspectiveFitDistance,
  projectedBoxCoverage,
  resolveCameraPenetration,
} from '../src/components/locations/digitalTwin/cameraSafety.js'

describe('warehouse digital-twin camera safety', () => {
  it('looks down into tabletop organizer slots while fitting the complete equipment', () => {
    const camera = new THREE.PerspectiveCamera(40, 1.7, 0.03, 100)
    const box = new THREE.Box3(new THREE.Vector3(-0.4, 0, -0.4), new THREE.Vector3(0.4, 1.7, 0.4))
    const pose = chooseFocusPose({ box, facingRadians: 0, camera, topSurface: true })
    expect(pose.position.y).toBeGreaterThan(box.max.y)
    camera.position.copy(pose.position)
    camera.lookAt(pose.target)
    camera.updateMatrixWorld(true)
    const coverage = projectedBoxCoverage(box, camera)
    expect(coverage.area).toBeGreaterThan(0.03)
    expect(coverage.area).toBeLessThan(0.65)
    for (const y of [box.min.y, box.max.y]) {
      expect(Math.abs(new THREE.Vector3(0, y, 0).project(camera).y)).toBeLessThan(1)
    }
  })
  it('fits both wide and tall equipment using horizontal and vertical FOV', () => {
    const camera = new THREE.PerspectiveCamera(40, 16 / 9, 0.03, 100)
    const wide = new THREE.Box3(new THREE.Vector3(-1.2, 0, -0.3), new THREE.Vector3(1.2, 0.6, 0.3))
    const tall = new THREE.Box3(new THREE.Vector3(-0.35, 0, -0.3), new THREE.Vector3(0.35, 2.0, 0.3))
    expect(perspectiveFitDistance(wide, camera)).toBeGreaterThan(1.1)
    expect(perspectiveFitDistance(tall, camera)).toBeGreaterThan(2.2)
  })

  it('pushes an orbit camera out of an equipment collision volume', () => {
    const box = new THREE.Box3(new THREE.Vector3(-0.5, 0, -0.5), new THREE.Vector3(0.5, 1.5, 0.5))
    const obstacles = [{ code: 'RACK', box }]
    const start = new THREE.Vector3(0, 0.8, 0)
    expect(cameraInsideAnyBox(start, obstacles)).toBe(true)
    const result = resolveCameraPenetration(start, obstacles, 0.16)
    expect(result.moved).toBe(true)
    expect(cameraInsideAnyBox(result.position, obstacles)).toBe(false)
  })


  it('reports selected-object screen coverage for automated visual framing checks', () => {
    const camera = new THREE.PerspectiveCamera(40, 16 / 9, 0.03, 100)
    camera.position.set(0, 1, 3)
    camera.lookAt(0, 0.7, 0)
    camera.updateMatrixWorld(true)
    const box = new THREE.Box3(new THREE.Vector3(-0.35, 0, -0.15), new THREE.Vector3(0.35, 1.4, 0.15))
    const coverage = projectedBoxCoverage(box, camera)
    expect(coverage.width).toBeGreaterThan(0.05)
    expect(coverage.height).toBeGreaterThan(0.1)
    expect(coverage.area).toBeGreaterThan(0.01)
    expect(coverage.area).toBeLessThan(0.8)
  })

  it('prefers an alternate focus angle when the straight-on view is blocked', () => {
    const camera = new THREE.PerspectiveCamera(40, 16 / 9, 0.03, 100)
    const selected = new THREE.Box3(new THREE.Vector3(-0.35, 0, -0.15), new THREE.Vector3(0.35, 1.2, 0.15))
    const blocker = new THREE.Box3(new THREE.Vector3(-0.55, 0, 0.55), new THREE.Vector3(0.55, 1.8, 1.25))
    const obstacles = [
      { code: 'SELECTED', box: selected },
      { code: 'BLOCKER', box: blocker },
    ]
    const straight = new THREE.Vector3(0, 0.6, 2.5)
    const target = selected.getCenter(new THREE.Vector3())
    expect(occlusionCount(straight, target, obstacles, 'SELECTED')).toBeGreaterThan(0)
    const pose = chooseFocusPose({
      box: selected,
      facingRadians: 0,
      camera,
      obstacles,
      selectedCode: 'SELECTED',
    })
    expect(pose).toBeTruthy()
    expect(pose.occlusions).toBeLessThanOrEqual(1)
  })
})
