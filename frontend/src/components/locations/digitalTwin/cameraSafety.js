import * as THREE from 'three';

const UP = new THREE.Vector3(0, 1, 0);

export function worldBox(object, padding = 0) {
  object.updateWorldMatrix(true, false);
  const box = new THREE.Box3().setFromObject(object, true);
  if (padding) box.expandByScalar(padding);
  return box;
}

export function perspectiveFitDistance(box, camera, margin = 1.34) {
  const size = box.getSize(new THREE.Vector3());
  const vertical = THREE.MathUtils.degToRad(camera.fov);
  const horizontal = 2 * Math.atan(Math.tan(vertical / 2) * Math.max(0.25, camera.aspect || 1));
  const byHeight = size.y / (2 * Math.tan(vertical / 2));
  const byWidth = Math.max(size.x, size.z) / (2 * Math.tan(horizontal / 2));
  return Math.max(0.75, byHeight, byWidth, Math.max(size.x, size.z) * 0.72) * margin + 0.18;
}

export function occlusionCount(position, target, obstacles, ignoreCode = null) {
  const delta = target.clone().sub(position);
  const distance = delta.length();
  if (distance < 1e-5) return 0;
  const ray = new THREE.Ray(position, delta.normalize());
  const hit = new THREE.Vector3();
  let count = 0;
  for (const item of obstacles) {
    if (item.code === ignoreCode) continue;
    if (!ray.intersectBox(item.box, hit)) continue;
    if (hit.distanceTo(position) < distance - 0.12) count += 1;
  }
  return count;
}

export function chooseFocusPose({ box, facingRadians, camera, obstacles = [], selectedCode = null, topSurface = false }) {
  const target = box.getCenter(new THREE.Vector3());
  const size = box.getSize(new THREE.Vector3());
  // Aim slightly above the visual center for low organizer cases and below tall-rack labels.
  target.y = box.min.y + size.y * (size.y < 0.8 ? 0.58 : 0.48);
  const distance = perspectiveFitDistance(box, camera);
  const elevation = topSurface ? distance * 0.68 : Math.max(0.28, Math.min(0.78, size.y * 0.28));
  const horizontalDistance = Math.sqrt(Math.max(0.3, distance * distance - elevation * elevation));
  const base = new THREE.Vector3(0, 0, 1).applyAxisAngle(UP, facingRadians);
  const angles = [0, -28, 28, -52, 52].map(THREE.MathUtils.degToRad);
  let best = null;
  for (let index = 0; index < angles.length; index += 1) {
    const direction = base.clone().applyAxisAngle(UP, angles[index]);
    const position = target
      .clone()
      .addScaledVector(direction, horizontalDistance)
      .add(new THREE.Vector3(0, elevation, 0));
    const occlusions = occlusionCount(position, target, obstacles, selectedCode);
    const score = occlusions * 100 + index;
    if (!best || score < best.score) best = { position, target: target.clone(), occlusions, score };
    if (occlusions === 0 && index === 0) break;
  }
  return best;
}

export function resolveCameraPenetration(position, obstacles, radius = 0.18, minHeight = 0.12) {
  const resolved = position.clone();
  let moved = false;
  for (let pass = 0; pass < 4; pass += 1) {
    let passMoved = false;
    for (const item of obstacles) {
      const box = item.box.clone().expandByScalar(radius);
      if (!box.containsPoint(resolved)) continue;
      const candidates = [
        { axis: 'x', value: box.min.x - 0.01, distance: Math.abs(resolved.x - box.min.x) },
        { axis: 'x', value: box.max.x + 0.01, distance: Math.abs(box.max.x - resolved.x) },
        { axis: 'y', value: box.max.y + 0.01, distance: Math.abs(box.max.y - resolved.y) },
        { axis: 'z', value: box.min.z - 0.01, distance: Math.abs(resolved.z - box.min.z) },
        { axis: 'z', value: box.max.z + 0.01, distance: Math.abs(box.max.z - resolved.z) },
      ];
      candidates.sort((a, b) => a.distance - b.distance);
      const nearest = candidates[0];
      resolved[nearest.axis] = nearest.value;
      passMoved = true;
      moved = true;
    }
    if (!passMoved) break;
  }
  if (resolved.y < minHeight) {
    resolved.y = minHeight;
    moved = true;
  }
  return { position: resolved, moved };
}

export function cameraInsideAnyBox(position, obstacles, radius = 0.08) {
  return obstacles.some(item => item.box.clone().expandByScalar(radius).containsPoint(position));
}
export function projectedBoxCoverage(box, camera) {
  const points = [];
  for (const x of [box.min.x, box.max.x])
    for (const y of [box.min.y, box.max.y])
      for (const z of [box.min.z, box.max.z]) points.push(new THREE.Vector3(x, y, z).project(camera));
  const xs = points.map(point => point.x);
  const ys = points.map(point => point.y);
  const rawMinX = Math.min(...xs), rawMaxX = Math.max(...xs);
  const rawMinY = Math.min(...ys), rawMaxY = Math.max(...ys);
  const minX = Math.max(-1, rawMinX), maxX = Math.min(1, rawMaxX);
  const minY = Math.max(-1, rawMinY), maxY = Math.min(1, rawMaxY);
  const width = Math.max(0, (maxX - minX) / 2);
  const height = Math.max(0, (maxY - minY) / 2);
  const margin = Math.min(rawMinX + 1, 1 - rawMaxX, rawMinY + 1, 1 - rawMaxY) / 2;
  const clipped = rawMinX < -1 || rawMaxX > 1 || rawMinY < -1 || rawMaxY > 1;
  return { width, height, area: width * height, clipped, margin,
    raw: { minX: rawMinX, maxX: rawMaxX, minY: rawMinY, maxY: rawMaxY } };
}

export function minimumOrbitDistance(box, camera, ratio = 0.82) {
  return Math.max(0.9, perspectiveFitDistance(box, camera, 1.0) * ratio);
}

export function visuallyFramed(coverage, { maxArea = 0.72, maxWidth = 0.94, maxHeight = 0.94 } = {}) {
  return Boolean(coverage) && !coverage.clipped && coverage.area <= maxArea
    && coverage.width <= maxWidth && coverage.height <= maxHeight;
}
