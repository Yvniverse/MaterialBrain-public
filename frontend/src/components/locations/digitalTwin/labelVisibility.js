import * as THREE from 'three';

const _world = new THREE.Vector3();
const _camera = new THREE.Vector3();
const _delta = new THREE.Vector3();
const _hit = new THREE.Vector3();
const _frustum = new THREE.Frustum();
const _matrix = new THREE.Matrix4();
const _ray = new THREE.Ray();

export function labelOccluded(cameraPosition, targetPosition, obstacles, ignoreCode = null, targetPadding = 0.06) {
  _delta.copy(targetPosition).sub(cameraPosition);
  const distance = _delta.length();
  if (distance < 1e-5) return false;
  _ray.set(cameraPosition, _delta.normalize());
  for (const item of obstacles) {
    if (item.code === ignoreCode) continue;
    if (!_ray.intersectBox(item.box, _hit)) continue;
    if (_hit.distanceTo(cameraPosition) < distance - targetPadding) return true;
  }
  return false;
}

export function labelInFrustum(sprite, camera) {
  _matrix.multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse);
  _frustum.setFromProjectionMatrix(_matrix);
  sprite.getWorldPosition(_world);
  return _frustum.containsPoint(_world);
}

export function updateLabelVisibility(labels, camera, obstacles, {
  selectedCode = null,
  maxVisible = 9,
} = {}) {
  camera.getWorldPosition(_camera);
  const visible = [];
  const diagnostics = { visible: 0, hiddenOccluded: 0, hiddenDistance: 0, hiddenFrustum: 0, hiddenDetail: 0, hiddenBudget: 0 };
  for (const item of labels) {
    const { sprite, ownerCode = null, kind = 'asset', maxDistance = 12 } = item;
    sprite.getWorldPosition(_world);
    const distance = _world.distanceTo(_camera);
    const selected = ownerCode && ownerCode === selectedCode;
    if (kind === 'detail' && !selected) {
      sprite.visible = false; diagnostics.hiddenDetail += 1; continue;
    }
    if (!selected && distance > maxDistance) {
      sprite.visible = false; diagnostics.hiddenDistance += 1; continue;
    }
    if (!labelInFrustum(sprite, camera)) {
      sprite.visible = false; diagnostics.hiddenFrustum += 1; continue;
    }
    if (labelOccluded(_camera, _world, obstacles, ownerCode)) {
      sprite.visible = false; diagnostics.hiddenOccluded += 1; continue;
    }
    sprite.visible = true;
    visible.push({ item, selected, distance });
  }
  visible.sort((a, b) => Number(b.selected) - Number(a.selected) || a.distance - b.distance);
  for (let i = maxVisible; i < visible.length; i += 1) {
    visible[i].item.sprite.visible = false;
    diagnostics.hiddenBudget += 1;
  }
  diagnostics.visible = Math.min(maxVisible, visible.length);
  return diagnostics;
}
