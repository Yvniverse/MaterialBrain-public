/** Minimal glTF 2.0 export of owned static models. No external assets, font files or textures.
 * Geometry is baked in the Three world frame; labels/routes/robot omitted intentionally.
 * This is a viewing artifact, while world.v3.json remains the authoritative collision source.
 */
export function exportStaticGLB(view) {
  const T = view.T,
    groups = new Map(),
    matrix = new T.Matrix4(),
    normalMatrix = new T.Matrix3(),
    p = new T.Vector3(),
    n = new T.Vector3()
  const collect = (mesh, transform) => {
    const geom = mesh.geometry,
      mat = mesh.material
    if (!geom?.attributes.position || Array.isArray(mat) || mat.map) return
    const color = mat.color?.toArray() || [0.7, 0.8, 0.85],
      key = [...color, mat.metalness || 0, mat.roughness ?? 0.7].join(',')
    if (!groups.has(key))
      groups.set(key, {
        positions: [],
        normals: [],
        color,
        metalness: mat.metalness || 0,
        roughness: mat.roughness ?? 0.7,
      })
    const g = groups.get(key)
    const pa = geom.attributes.position,
      na = geom.attributes.normal,
      index = geom.index,
      count = index ? index.count : pa.count
    normalMatrix.getNormalMatrix(transform)
    for (let i = 0; i < count; i++) {
      const idx = index ? index.getX(i) : i
      p.fromBufferAttribute(pa, idx).applyMatrix4(transform)
      g.positions.push(p.x, p.y, p.z)
      if (na) n.fromBufferAttribute(na, idx).applyMatrix3(normalMatrix).normalize()
      else n.set(0, 1, 0)
      g.normals.push(n.x, n.y, n.z)
    }
  }
  for (const root of [view.environment, view.assetsGroup]) {
    root.updateMatrixWorld(true)
    root.traverse((o) => {
      if (o.isInstancedMesh) {
        const instance = new T.Matrix4()
        for (let i = 0; i < o.count; i++) {
          o.getMatrixAt(i, instance)
          matrix.multiplyMatrices(o.matrixWorld, instance)
          collect(o, matrix)
        }
      } else if (o.isMesh) collect(o, o.matrixWorld)
    })
  }
  const binParts = [],
    bufferViews = [],
    accessors = [],
    materials = [],
    primitives = []
  let bytes = 0
  const append = (array, type, withBounds = false) => {
    const arr = new Float32Array(array),
      index = bufferViews.length
    bufferViews.push({ buffer: 0, byteOffset: bytes, byteLength: arr.byteLength, target: 34962 })
    binParts.push(new Uint8Array(arr.buffer))
    bytes += arr.byteLength
    const acc = { bufferView: index, componentType: 5126, count: array.length / 3, type }
    if (withBounds) {
      acc.min = [Infinity, Infinity, Infinity]
      acc.max = [-Infinity, -Infinity, -Infinity]
      for (let i = 0; i < array.length; i++) {
        acc.min[i % 3] = Math.min(acc.min[i % 3], array[i])
        acc.max[i % 3] = Math.max(acc.max[i % 3], array[i])
      }
    }
    accessors.push(acc)
    return accessors.length - 1
  }
  for (const g of groups.values()) {
    const pos = append(g.positions, 'VEC3', true),
      norm = append(g.normals, 'VEC3')
    materials.push({
      name: 'MB-' + materials.length,
      pbrMetallicRoughness: {
        baseColorFactor: [...g.color, 1],
        metallicFactor: g.metalness,
        roughnessFactor: g.roughness,
      },
    })
    primitives.push({
      attributes: { POSITION: pos, NORMAL: norm },
      material: materials.length - 1,
      mode: 4,
    })
  }
  const gltf = {
    asset: { version: '2.0', generator: 'MaterialBrain owned procedural warehouse exporter' },
    scene: 0,
    scenes: [{ nodes: [0] }],
    nodes: [{ mesh: 0, name: 'MB-EMB-LAB-03' }],
    meshes: [{ primitives }],
    materials,
    buffers: [{ byteLength: bytes }],
    bufferViews,
    accessors,
    extras: {
      world_id: view.world.id,
      world_revision: view.world.revision_sha256,
      frame: 'Three: x east, y up, z south; origin centered',
      provenance: 'synthetic_lab',
    },
  }
  let json = new TextEncoder().encode(JSON.stringify(gltf))
  const padded = (json.length + 3) & ~3,
    total = 12 + 8 + padded + 8 + bytes,
    buffer = new ArrayBuffer(total),
    dv = new DataView(buffer),
    out = new Uint8Array(buffer)
  dv.setUint32(0, 0x46546c67, true)
  dv.setUint32(4, 2, true)
  dv.setUint32(8, total, true)
  dv.setUint32(12, padded, true)
  dv.setUint32(16, 0x4e4f534a, true)
  out.fill(32, 20, 20 + padded)
  out.set(json, 20)
  let offset = 20 + padded
  dv.setUint32(offset, bytes, true)
  dv.setUint32(offset + 4, 0x004e4942, true)
  offset += 8
  for (const part of binParts) {
    out.set(part, offset)
    offset += part.byteLength
  }
  return buffer
}
