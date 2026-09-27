// @vitest-environment node
import { readFileSync } from 'node:fs'
import * as THREE from 'three'
import { describe, expect, it } from 'vitest'
import {
  minimumOrbitDistance,
  projectedBoxCoverage,
  visuallyFramed,
} from '../src/components/locations/digitalTwin/cameraSafety.js'
import { labelOccluded } from '../src/components/locations/digitalTwin/labelVisibility.js'

const css = readFileSync(new URL('../src/components/locations/digitalTwin/twin.css', import.meta.url), 'utf8')
const scene = readFileSync(new URL('../src/components/locations/digitalTwin/scene.js', import.meta.url), 'utf8')

describe('digital twin visual policy', () => {
  it('keeps visible UI typography at 11px or larger', () => {
    const sizes = [...css.matchAll(/font-size:\s*([0-9.]+)px/g)].map(match => Number(match[1]))
    expect(sizes.length).toBeGreaterThan(10)
    expect(Math.min(...sizes)).toBeGreaterThanOrEqual(11)
    expect(css).toContain('font-size:14px;line-height:1.45')
  })

  it('uses a floor ribbon route instead of large 3D cone arrows', () => {
    expect(scene).not.toContain('ConeGeometry(')
    expect(scene).toContain("this.box(this.routeGroup,.048,.008,length")
    expect(scene).toContain('routeArrowGeometry')
  })

  it('uses larger depth-aware scene labels and limits visible clutter', () => {
    expect(scene).toContain('700 40px')
    expect(scene).toContain('depthTest: true, depthWrite: false')
    expect(scene).toContain('updateLabelVisibility')
    expect(scene).toContain('maxVisible:this.selectedCode?10:7')
  })

  it('detects label occlusion through obstacle boxes', () => {
    const camera = new THREE.Vector3(0, 1, 4)
    const target = new THREE.Vector3(0, 1, -4)
    const wall = new THREE.Box3(new THREE.Vector3(-2, 0, -0.1), new THREE.Vector3(2, 2, 0.1))
    expect(labelOccluded(camera, target, [{ code: 'wall', box: wall }])).toBe(true)
    expect(labelOccluded(camera, target, [{ code: 'SELF', box: wall }], 'SELF')).toBe(false)
  })

  it('records clipping-aware framing metrics and safe orbit distance', () => {
    const camera = new THREE.PerspectiveCamera(40, 16 / 9, 0.03, 100)
    camera.position.set(0, 1, 4)
    camera.lookAt(0, 1, 0)
    camera.updateProjectionMatrix(); camera.updateMatrixWorld(true)
    const normal = new THREE.Box3(new THREE.Vector3(-.6, .2, -.3), new THREE.Vector3(.6, 1.8, .3))
    const metrics = projectedBoxCoverage(normal, camera)
    expect(metrics.clipped).toBe(false)
    expect(visuallyFramed(metrics)).toBe(true)
    expect(minimumOrbitDistance(normal, camera)).toBeGreaterThanOrEqual(.9)

    const huge = new THREE.Box3(new THREE.Vector3(-10, -10, 2), new THREE.Vector3(10, 10, 3.9))
    const bad = projectedBoxCoverage(huge, camera)
    expect(bad.clipped || bad.area > .72).toBe(true)
    expect(visuallyFramed(bad)).toBe(false)
  })
})
