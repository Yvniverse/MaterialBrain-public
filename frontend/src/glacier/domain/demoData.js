/** Synthetic preview fixtures only. Never imported by the production router or data services. */
export function makePreviewData(){
 const materialSeed=[
 {id:1,mpn:'TPS54360',name:'降压稳压器 · 电源候选',package:'HSSOP-8',category:'电源管理',available:126,reserved:18,quantity:144,location:'IC-01 / A05',location_id:1021,unit:'个',role:'降压支路',value:'输入范围待核对',note:'优先使用现有库存'},
 {id:2,mpn:'LMR36510',name:'同步降压稳压器',package:'HSOIC-8',category:'电源管理',available:48,reserved:12,quantity:60,location:'IC-01 / C07',location_id:1033,unit:'个',role:'降压支路',value:'输入范围待核对',note:'核对频率、负载与散热'},
 {id:3,mpn:'LMR33630',name:'同步降压稳压器',package:'VQFN',category:'电源管理',available:32,reserved:0,quantity:32,location:'IC-01 / B12',location_id:1057,unit:'个',role:'降压支路',value:'输入范围待核对',note:'核对封装与外围要求'},
 {id:4,mpn:'STM32G473RCT6',name:'微控制器',package:'LQFP-64',category:'控制与接口',available:16,reserved:4,quantity:20,location:'IC-02 / B06',location_id:1227,unit:'个'},
 {id:5,mpn:'100 nF / 50 V',name:'多层陶瓷电容',package:'0603',category:'被动器件',available:860,reserved:140,quantity:1000,location:'PASS-01 / A01',location_id:2001,unit:'个'},
 {id:6,mpn:'4.7 μH',name:'功率电感',package:'5 × 5 mm',category:'被动器件',available:92,reserved:8,quantity:100,location:'PASS-01 / D04',location_id:2028,unit:'个'},
 {id:7,mpn:'JST-XH-4P',name:'线对板连接器',package:'2.54 mm',category:'连接器',available:73,reserved:0,quantity:73,location:'CONN-01 / B03',location_id:2211,unit:'个'},
 {id:8,mpn:'实验导线套装',name:'测试线材',package:'套装',category:'连接器',available:12,reserved:2,quantity:14,location:'SHELF-01 / L03',location_id:3103,unit:'套'},
 ];
 const mats=materialSeed.map(m=>({...m,code:'DEMO-'+String(m.id).padStart(3,'0'),reserved_quantity:String(m.reserved),available_quantity:String(m.available),quantity:String(m.quantity),unit_price:'0',min_stock:5,notes:'合成演示物料',attributes:{package:m.package},location_record:{id:m.location_id,full_path:'研发仓 / '+m.location}}));
 const map={id:1,code:'GLACIER-DEMO',name:'研发仓 · 一层',version:1,graph_hash:'synthetic-map-v2',width_m:8,height_m:6,nodes:[],edges:[]};
 const nodeSeed=[['ENTRY',4,.55],['C1',4,1.5],['C2',2.1,1.5],['C3',2.1,3.8],['IC-STOP',1.1,3.8],['IC2-STOP',2.6,3.8],['SHELF-STOP',5,3.8],['C4',4,3.8],['PASS-STOP',3.2,1.5],['CONN-STOP',4.6,1.5],['RIGHT-STOP',5.9,2.2]];
 map.nodes=nodeSeed.map(([code,x_m,y_m])=>({code,x_m,y_m,label:code,node_type:code==='ENTRY'?'entry':'waypoint'}));
 const links=[['ENTRY','C1'],['C1','C2'],['C2','C3'],['C3','IC-STOP'],['C3','IC2-STOP'],['C3','C4'],['C4','SHELF-STOP'],['C1','PASS-STOP'],['C1','CONN-STOP'],['C1','RIGHT-STOP']];
 map.edges=links.map(([from_node,to_node])=>{const a=map.nodes.find(n=>n.code===from_node),b=map.nodes.find(n=>n.code===to_node);return{code:from_node+'--'+to_node,from_node,to_node,distance_m:Math.hypot(a.x_m-b.x_m,a.y_m-b.y_m),enabled:true,bidirectional:true}});
 const spec=[
 ['IC-01','集成电路柜 A','drawer_rack_100',10,1.1,5.1,1001,'IC-STOP','south'],
 ['IC-02','集成电路柜 B','drawer_rack_100',12,2.6,5.1,1201,'IC2-STOP','south'],
 ['SHELF-01','模块与工具货架','shelf_rack_6',30,5,5.08,3001,'SHELF-STOP','south'],
 ['PASS-01','阻容感元件盒','standard_56',20,3.2,2.55,2001,'PASS-STOP','south'],
 ['CONN-01','连接器元件盒','standard_56',22,4.6,2.55,2201,'CONN-STOP','south'],
 ['IC-03','接口器件柜','drawer_rack_100',14,1.1,2.3,1401,'C2','south'],
 ['SHELF-02','线缆与备件货架','shelf_rack_6',32,6.95,2.8,3201,'RIGHT-STOP','west']
 ];
 const assets=spec.map(([code,name,style,location_id,x_m,y_m,start,pick_node_code,facing])=>{
 const rack=style==='drawer_rack_100',shelf=style==='shelf_rack_6';const cols=rack?5:shelf?1:8,rows=rack?20:shelf?6:7;
 const slots=Array.from({length:cols*rows},(_,i)=>{const col=i%cols,row=Math.floor(i/cols),id=start+i,n=rack?String.fromCharCode(65+col)+String(row+1).padStart(2,'0'):shelf?'L'+String(row+1).padStart(2,'0'):String.fromCharCode(65+row)+String(col+1).padStart(2,'0');let materials=mats.filter(m=>m.location_id===id||shelf&&m.location_id===id+100);if(!materials.length&&(i+start)%13===0)materials=[{name:rack?'通用器件 '+n:'备料包 '+n,mpn:'',quantity:String(8+i%29),unit:shelf?'套':'个'}];const q={};for(const m of materials)q[m.unit]=(q[m.unit]||0)+Number(m.quantity);return{name:n,location_id:id,descendant_ids:shelf?[id+100]:[],geometry:{x_norm:(col+.5)/cols,y_norm:(row+.5)/rows,width_norm:1/cols,height_norm:1/rows},material_kind_count:materials.length,materials:materials.map(m=>({name:m.mpn||m.name,quantity:m.quantity,unit:m.unit})),quantities_by_unit:q}});
 const quantities_by_unit={};for(const s of slots)for(const [u,n]of Object.entries(s.quantities_by_unit))quantities_by_unit[u]=(quantities_by_unit[u]||0)+n;
 return{code,name,style,location_id,x_m,y_m,facing,dimensions:rack?{width:.94,height:1.82,depth:.46}:shelf?{width:1.62,height:1.87,depth:.59}:{width:.75,height:.14,depth:.49},footprint:rack?{width_m:.94,depth_m:.46}:shelf?{width_m:facing==='west'?.59:1.62,depth_m:facing==='west'?1.62:.59}:{width_m:.95,depth_m:.68},pick_node_code,zone:'研发仓',full_path:'研发仓 / '+name,slots,material_kind_count:slots.reduce((n,s)=>n+s.material_kind_count,0),quantities_by_unit,storage_boxes:shelf?Array.from({length:18},(_,i)=>({level:Math.floor(i/3)+1,code:`BOX-${i+1}`,name:`物料箱 ${i+1}`,location_id:start+100+i})):[]}
 });
 const snapshot={schema_version:'1',map,assets,route:null,task:null,closed_edge_codes:[],decorations:[{kind:'pack',x_m:6.65,y_m:1}],metadata:{source:'synthetic',label:'合成演示仓库'}};
 function location(id,name,type,parent_id,extra={}){return{id,parent_id,code:type.toUpperCase()+'-'+id,name,type,full_path:'研发仓 / '+name,manager:'',notes:'',is_active:true,...extra}}
 const cabinet=location(10,'集成电路柜 A','organizer',1,{organizer_style:'drawer_rack_100'});
 const drawerBins=assets[0].slots.map(s=>location(s.location_id,s.name,'bin',10,{bin_material_name:s.materials[0]?.name||'',bin_quantity:s.materials[0]?Number(s.materials[0].quantity):null,bin_content_notes:''}));
 const box=location(20,'阻容感元件盒','organizer',1,{organizer_style:'standard_56',organizer_left_module:'small',organizer_right_module:'small'});
 const boxBins=assets[3].slots.map(s=>location(s.location_id,s.name,'bin',20,{bin_material_name:s.materials[0]?.name||'',bin_quantity:s.materials[0]?Number(s.materials[0].quantity):null}));
 const shelf=location(30,'模块与工具货架','organizer',1,{organizer_style:'shelf_rack_6'});
 const levels=Array.from({length:6},(_,i)=>location(3001+i,'L'+String(i+1).padStart(2,'0'),'shelf',30));
 const storageBoxes=Array.from({length:12},(_,i)=>location(3101+i,'收纳箱 '+String(i+1).padStart(2,'0'),'storage_box',3001+Math.floor(i/2),{organizer_style:'shelf_storage_box'}));
 const items=storageBoxes.map((b,i)=>location(4001+i,'示例内容 '+(i+1),'bin',b.id,{bin_material_name:['测试导线','传感器模块','连接器','工具配件'][i%4],bin_quantity:3+i,bin_content_notes:'合成演示内容'}));
 return{materials:mats,snapshot,legacy:{cabinet,drawerBins,box,boxBins,shelf,levels,storageBoxes,items},summary:{material_count:'1,248',available_quantity:'38,462',reserved_quantity:'2,180',inventory_value:'86,240',today_inbound:326,today_outbound:148},projects:[{name:'双路电源控制板',note:'工程选型 · 3 个候选',status:'进行中',tone:''},{name:'传感器采集节点',note:'BOM 核对 · 12 项物料',status:'待备料',tone:'amber'},{name:'桌面设备原型',note:'物料齐套 · 8 项物料',status:'已齐套',tone:'mint'}],activities:[{id:1,name:'TPS54360',action:'入库',quantity:'+ 24 个',location:'IC-01 / A05',time:'10 分钟前'},{id:2,name:'4.7 μH 功率电感',action:'项目预留',quantity:'− 8 个可用',location:'双路电源控制板',time:'35 分钟前'},{id:3,name:'JST-XH-4P',action:'库位调整',quantity:'73 个',location:'CONN-01 / B03',time:'1 小时前'}]};
}
export function previewRoute(snapshot, locationIds){
 const by=new Map(snapshot.map.nodes.map(n=>[n.code,n])); const edges=snapshot.map.edges.filter(e=>e.enabled&&!snapshot.closed_edge_codes?.includes(e.code));
 const targets=[...new Set(snapshot.assets.filter(a=>locationIds.includes(a.location_id)||a.slots.some(s=>locationIds.includes(s.location_id)||s.descendant_ids.some(id=>locationIds.includes(id)))).map(a=>a.pick_node_code))];
 if(!targets.length)return null;
 let from='ENTRY',total=0;const segments=[];
 for(const to of targets){const dist=new Map([...by.keys()].map(k=>[k,Infinity])),prev=new Map(),todo=new Set(by.keys());dist.set(from,0);while(todo.size){let cur=[...todo].sort((a,b)=>dist.get(a)-dist.get(b))[0];if(!Number.isFinite(dist.get(cur)))break;todo.delete(cur);if(cur===to)break;for(const e of edges){const n=e.from_node===cur?e.to_node:e.bidirectional&&e.to_node===cur?e.from_node:null;if(!n||!todo.has(n))continue;const d=dist.get(cur)+e.distance_m;if(d<dist.get(n)){dist.set(n,d);prev.set(n,cur)}}}if(!Number.isFinite(dist.get(to)))throw new Error('所选位置当前不可达');let path=[to],x=to;while(x!==from){x=prev.get(x);if(!x)throw new Error('路线不可达');path.unshift(x)}segments.push({from_node:from,to_node:to,path_nodes:path,distance_m:dist.get(to)});total+=dist.get(to);from=to}
 return{graph_hash:snapshot.map.graph_hash,strategy:'preview_dijkstra',total_distance_m:total,ordered_stop_nodes:targets,segments};
}
