import type {TwinAsset,TwinSnapshot,TwinRoute,TwinSlot,TwinNode,TwinAllocation} from './types'
export const STYLE_LABELS: Readonly<Record<string,string>>
export function finiteNumber(value: unknown, fallback?: number):number
export function labelWorldWidth(width:number,distance:number,fov:number,viewportHeight:number):number
export function uprightFocusDistance(height:number,fov:number):number
export function facingRadians(facing:string):number
export function worldPoint(map:{width_m:number;height_m:number},x:number,y:number,height?:number):[number,number,number]
export function projectSlot(asset:TwinAsset,slot:TwinSlot):{plane:string;x:number;y:number;z:number;width:number;height:number;depth:number}|null
export function normalizeSnapshot(raw:TwinSnapshot):TwinSnapshot
export function flattenRoute(map:TwinSnapshot['map'],route:TwinRoute|null):TwinNode[]
export function groupStops(route:TwinRoute|null,allocations?:TwinAllocation[]):{node:string;index:number;allocations:TwinAllocation[]}[]
export function findAssetForLocation(snapshot:TwinSnapshot,id:number):TwinAsset|null
export function quantityLabel(quantities:Record<string,string|number>|null):string
export function createRequestGate():{next:()=>number;current:(token:number)=>boolean;cancel:()=>number}
export function routeStopFractions(map:TwinSnapshot['map'],route:TwinRoute|null):number[]
