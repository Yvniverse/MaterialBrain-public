import * as THREE from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import { mountLabApp } from './lab-app.mjs'

export function mountProductionLabApp(root, world, options) {
  return mountLabApp(root, world, { ...options, THREE, OrbitControls })
}
