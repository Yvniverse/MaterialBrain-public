/** Pure spatial projection. Slots and routes arrive from the server, not from Three.js. */
export const STYLE_LABELS = Object.freeze({
  drawer_rack_100: '100 抽零件柜', standard_56: '56 格元件盒',
  split_configurable: '组合元件盒', shelf_rack_6: '六层箱式货架',
});
export function finiteNumber(value, fallback = 0) {
  const result = Number(value);
  return Number.isFinite(result) ? result : fallback;
}
export function labelWorldWidth(width, distance, fov, viewportHeight, targetPixels = 160) {
  return Math.min(width, 2 * distance * Math.tan(fov * Math.PI / 360) * targetPixels / Math.max(1, viewportHeight));
}
export function uprightFocusDistance(height, fov) {
  return height / (2 * Math.tan(fov * Math.PI / 360)) * 1.35;
}
export function worldPoint(map, x, y, height = 0) {
  return [finiteNumber(x) - map.width_m / 2, height, map.height_m / 2 - finiteNumber(y)];
}
export function facingRadians(facing) {
  const angles = { south: 0, east: Math.PI / 2, north: Math.PI, west: -Math.PI / 2 };
  if (!(facing in angles)) throw new Error(`Unsupported facing: ${facing}`);
  return angles[facing];
}
export function projectSlot(asset, slot) {
  const g = slot.geometry;
  if (!g) return null;
  const { width: w, height: h, depth: d } = asset.dimensions;
  const x = (g.x_norm - .5) * w * .9;
  if (asset.style === 'shelf_rack_6') {
    // Backend L01 is the lowest level; no reversed row convention here.
    const level = Math.max(0, Math.min(5, Math.round(g.y_norm * 6 - .5)));
    return { plane: 'shelf', x: 0, y: .12 + level * (h - .24) / 5 + .022,
      z: 0, width: w * .96, height: .035, depth: d * .98 };
  }
  if (asset.style === 'standard_56' || asset.style === 'split_configurable') {
    // A01 is far-left on the open box; G08 is near-right. The box sits on its bench.
    return { plane: 'top', x, y: .88, z: (g.y_norm - .5) * d * .84,
      width: g.width_norm * w * .84, height: .024, depth: g.height_norm * d * .78 };
  }
  return { plane: 'front', x, y: .105 + (1 - g.y_norm) * (h - .20), z: d / 2 + .02,
    width: g.width_norm * w * .84, height: g.height_norm * (h - .20) * .82, depth: .018 };
}
export function normalizeSnapshot(raw) {
  if (!raw || !raw.map || !Array.isArray(raw.assets)) throw new Error('仓库空间数据不完整');
  const map = { ...raw.map, width_m: finiteNumber(raw.map.width_m), height_m: finiteNumber(raw.map.height_m) };
  if (map.width_m <= 0 || map.height_m <= 0) throw new Error('地图尺寸不合法');
  const unique = new Set();
  const assets = raw.assets.map(a => {
    if (!a.code || unique.has(a.code)) throw new Error('设备编码缺失或重复');
    unique.add(a.code);
    facingRadians(a.facing);
    return { ...a, x_m: finiteNumber(a.x_m), y_m: finiteNumber(a.y_m), slots: a.slots || [],
      dimensions: { width: finiteNumber(a.dimensions.width), height: finiteNumber(a.dimensions.height), depth: finiteNumber(a.dimensions.depth) } };
  });
  for (const a of assets) {
    if (Object.values(a.dimensions).some(n => n <= 0)) throw new Error('设备尺寸不合法');
  }
  return { ...raw, map, assets };
}
export function flattenRoute(map, route) {
  if (!route) return [];
  if (route.graph_hash && map.graph_hash && route.graph_hash !== map.graph_hash)
    throw new Error('路线和地图版本不一致，请重新加载任务');
  const byCode = new Map(map.nodes.map(n => [n.code, n]));
  const list = [];
  for (const segment of route.segments || []) {
    for (const code of segment.path_nodes || []) {
      const node = byCode.get(code);
      if (!node) throw new Error(`路线节点不存在: ${code}`);
      if (list.at(-1)?.code !== code) list.push(node);
    }
  }
  return list;
}
export function groupStops(route, allocations = []) {
  const nodes = [...new Set(route?.ordered_stop_nodes || [])];
  return nodes.map((node, index) => ({ node, index,
    allocations: allocations.filter(a => a.route_node_code === node) }));
}
export function quantityLabel(quantities) {
  const entries = Object.entries(quantities || {});
  if (!entries.length) return '—';
  return entries.map(([unit, count]) => `${finiteNumber(count).toLocaleString('zh-CN', { maximumFractionDigits: 4 })} ${unit}`).join(' / ');
}
export function findAssetForLocation(snapshot, locationId) {
  return snapshot.assets.find(a => a.location_id === locationId || a.slots.some(s => s.location_id === locationId || s.descendant_ids?.includes(locationId))) || null;
}
/** Request generations prevent an older response overwriting a new map/task selection. */
export function createRequestGate() {
  let generation = 0;
  return { next: () => ++generation, current: token => token === generation, cancel: () => ++generation };
}

/** Distances along the displayed polyline, used only to synchronize the tour HUD. */
export function routeStopFractions(map, route) {
  const nodes = flattenRoute(map, route);
  const stops = route?.ordered_stop_nodes || [];
  const at = [0];
  for (let i = 1; i < nodes.length; i++)
    at.push(at[i - 1] + Math.hypot(nodes[i].x_m - nodes[i - 1].x_m, nodes[i].y_m - nodes[i - 1].y_m));
  const total = at.at(-1) || 1;
  let cursor = 0;
  return stops.map(code => {
    const index = nodes.findIndex((node, i) => i >= cursor && node.code === code);
    if (index < 0) throw new Error('路线停靠点与路径不一致');
    cursor = index + 1;
    return at[index] / total;
  });
}
