import { test } from 'vitest';
import assert from 'node:assert/strict';
import data from './fixtures/twin_snapshot.json';
import ui from '../src/components/locations/digitalTwin/ui.js?raw';
import { labelWorldWidth, uprightFocusDistance } from '../src/components/locations/digitalTwin/model.js';
import { normalizeSnapshot, projectSlot, facingRadians, worldPoint, flattenRoute, groupStops, findAssetForLocation, createRequestGate, quantityLabel, routeStopFractions } from '../src/components/locations/digitalTwin/model.js';
const asset=style=>data.assets.find(a=>a.style===style);
const slot=(a,name)=>a.slots.find(s=>s.name===name);
test('upright close-ups fit the full cabinet height including L06 and L01', () => {
 for (const h of [1.17, 1.85]) {
   const distance = uprightFocusDistance(h, 40);
   assert.ok(2 * distance * Math.tan(40 * Math.PI / 360) > h * 1.3);
 }
});
test('close-up device labels stay within 160 pixels without enlarging distant labels', () => {
 const width = labelWorldWidth(1.05, 1, 40, 500);
 assert.ok(width < .24);
 assert.ok(width / (2 * Math.tan(40 * Math.PI / 360)) * 500 <= 160.001);
 assert.equal(labelWorldWidth(1.05, 20, 40, 500), 1.05);
});

test('all 12 assets and all 728 slots are materialized from the server geometry contract',()=>{
 const d=normalizeSnapshot(data); assert.equal(d.assets.length,12);assert.equal(d.assets.reduce((n,a)=>n+a.slots.length,0),728);
 for(const a of d.assets)for(const s of a.slots){const p=projectSlot(a,s);assert.ok(p);for(const k of ['x','y','z','width','height','depth'])assert.ok(Number.isFinite(p[k]),`${a.code}/${s.name}/${k}`);}
});
test('100 drawers preserve 20 rows, five columns; A03 is above A20',()=>{
 const a=asset('drawer_rack_100');assert.equal(a.slots.length,100);
 const p=projectSlot(a,slot(a,'A03')), end=projectSlot(a,slot(a,'A20'));
 assert.equal(p.plane,'front');assert.ok(p.y>end.y);assert.ok(p.x<0);
 assert.ok(projectSlot(a,slot(a,'E03')).x>p.x);
});
test('56 compartments preserve seven rows and eight columns on the horizontal case top',()=>{
 const a=asset('standard_56'); assert.equal(a.slots.length,56);
 const first=projectSlot(a,slot(a,'A01')), last=projectSlot(a,slot(a,'G08'));
 assert.equal(last.plane,'top');assert.ok(last.x>first.x);assert.ok(last.z>first.z);assert.equal(first.y,last.y);
 assert.equal(slot(a,'G08').geometry.x_norm,7.5/8);assert.equal(slot(a,'G08').geometry.y_norm,6.5/7);
});
test('L01 is the bottom level; L06 is the top level',()=>{
 const a=asset('shelf_rack_6'),lower=projectSlot(a,slot(a,'L01')),upper=projectSlot(a,slot(a,'L06'));
 assert.equal(lower.plane,'shelf');assert.ok(upper.y>lower.y);assert.equal(a.slots.length,6);
});
test('mixed box projects 28 small left cells and eight large right cells',()=>{
 const a=asset('split_configurable');assert.equal(a.slots.length,36);
 const left=projectSlot(a,slot(a,'L-S01')), right=projectSlot(a,slot(a,'R-L08'));
 assert.ok(left.x<0&&right.x>0);assert.ok(right.width>left.width);assert.ok(right.depth>left.depth);
});
test('single facing transform and XY to XZ transform are deterministic',()=>{
 assert.equal(facingRadians('east'),Math.PI/2);assert.equal(facingRadians('west'),-Math.PI/2);
 assert.deepEqual(worldPoint({width_m:8,height_m:6},4,3,1),[0,1,0]);assert.throws(()=>facingRadians('unknown'));
});
test('task route graph hash cannot silently switch to an unrelated active map',()=>{
 assert.throws(()=>flattenRoute(data.map,{...data.route,graph_hash:'wrong'}),/不一致/);
 const nodes=flattenRoute(data.map,data.route);assert.ok(nodes.length>4);assert.equal(nodes[0].code,'PACK');
});
test('unknown path nodes are rejected, not drawn as straight-line shortcuts',()=>{
 assert.throws(()=>flattenRoute(data.map,{segments:[{path_nodes:['DOES_NOT_EXIST']}]}),/不存在/);
});
test('same cabinet allocations are one stop with multiple exact locations',()=>{
 const r={ordered_stop_nodes:['PF-A','PF-A','PF-B']}, al=[{route_node_code:'PF-A',location_id:1},{route_node_code:'PF-A',location_id:2}];
 const result=groupStops(r,al);assert.equal(result.length,2);assert.equal(result[0].allocations.length,2);
});
test('a late map response cannot overwrite the current request',()=>{
 const gate=createRequestGate(),first=gate.next(),second=gate.next();assert.equal(gate.current(first),false);assert.equal(gate.current(second),true);gate.cancel();assert.equal(gate.current(second),false);
});
test('inventory quantities retain their units instead of summing metres and pieces',()=>{
 assert.match(quantityLabel({pcs:'12',m:'2.5'}),/12 pcs \/ 2.5 m/);assert.equal(quantityLabel(null),'—');
});
test('deep location selection resolves an organizer without creating new IDs',()=>{
 const a=asset('drawer_rack_100'),s=slot(a,'A03');assert.equal(findAssetForLocation(data,s.location_id).code,a.code);assert.equal(findAssetForLocation(data,-100),null);
});
test('tour HUD stop fractions follow actual path lengths rather than equal-duration guesses',()=>{
 const result=routeStopFractions(data.map,data.route);assert.equal(result.length,4);
 assert.ok(result.every((n,i)=>n>0&&n<1&&(i===0||n>=result[i-1])));
});
test('primary UI exposes warehouse and data-version navigation',()=>{
 assert.ok(ui.includes('研发仓数字孪生'));assert.ok(ui.includes('数据与版本'));
});
