import { normalizeSnapshot, STYLE_LABELS, quantityLabel, groupStops, findAssetForLocation, routeStopFractions } from './model.js';

function dom(tag, attrs = {}, text = '') {
  const node=document.createElement(tag);
  for(const [key,value] of Object.entries(attrs)) node.setAttribute(key,String(value));
  if(text)node.textContent=text;return node;
}
function svg(tag, attrs = {}) {
  const node=document.createElementNS('http://www.w3.org/2000/svg',tag);
  for(const [key,value] of Object.entries(attrs))node.setAttribute(key,String(value));return node;
}
/** Shared UI used by the standalone page and the Vue integration. No business writes. */
export function mountWarehouseUI(host, raw, options = {}) {
  const data=normalizeSnapshot(raw), abort=new AbortController();
  let disposed=false, engine=null, mode=options.initialMode==='2d'?'2d':'3d', generation=0;
  let selected=data.assets[0]||null, currentIndex=0, selectedSlot=null, raf=0, tourStart=0, touring=false;
  let rendererStatus='not_loaded', rendererError='';
  const stops=groupStops(data.route,data.task?.allocations||[]);
  const stopFractions=routeStopFractions(data.map,data.route);
  const allocation=()=>{const items=stops[currentIndex]?.allocations||[];return items.find(a=>Number(a.remaining_quantity)>0&&a.status!=='cancelled')||items[0]||null;};
  host.classList.add('mb-twin');
  host.innerHTML=`
    <header class="tw-header"><div><div class="tw-kicker">MATERIALBRAIN / WAREHOUSE</div><h1>研发仓数字孪生</h1><p>空间导航与库位协同</p></div><div class="tw-version"><span class="tw-dot"></span><span data-ref="mapTitle"></span><button data-act="refresh" title="刷新仓库数据">刷新</button></div></header>
    <div class="tw-toolbar"><div class="tw-switch"><button data-act="3d" class="is-active">3D 空间</button><button data-act="2d">平面图</button></div><span class="tw-divider"></span><div class="tw-presets"><button data-preset="overview">总览</button><button data-preset="top">顶视</button><button data-preset="route">路线</button></div><div class="tw-search"><span>定位设备</span><select data-ref="assetSelect" aria-label="定位设备"></select></div><button data-act="focus">定位到设备</button><button data-act="closure" class="tw-closure">封闭通道</button><button data-act="full">全屏</button></div>
    <div class="tw-error" data-ref="error" hidden></div>
    <div class="tw-workspace"><section class="tw-main">
      <div class="tw-stage"><div class="tw-canvas" data-ref="canvas"></div><div class="tw-plan" data-ref="plan" hidden></div><div class="tw-stage-head"><strong>研发仓 / 1F</strong><span data-ref="stats"></span></div><div class="tw-compass"><b>N</b><i></i><span>W &nbsp;&nbsp; E</span></div><div class="tw-hints" data-ref="hints">拖动旋转 · 滚轮缩放 · 右键平移</div><span class="tw-engine" data-ref="engine">载入 3D 视图…</span></div>
      <section class="tw-route"><div><span>领料路径</span><strong data-ref="routeName"></strong></div><div><span>路径长度</span><strong data-ref="distance"></strong></div><div><span>当前站点</span><strong data-ref="step"></strong></div><div class="tw-route-actions"><button data-act="prev">上一站</button><button data-act="tour" class="primary">开始巡游</button><button data-act="next">下一站</button></div><div class="tw-stops" data-ref="stops"></div></section>
    </section><aside class="tw-side">
      <section class="tw-card"><header><span class="tw-card-icon">01</span><h2>当前库位</h2><span class="tw-pill" data-ref="type"></span></header><h3 data-ref="assetName"></h3><div class="tw-code" data-ref="assetCode"></div><dl><div><dt>物料种类</dt><dd data-ref="kinds"></dd></div><div><dt>物理库存</dt><dd data-ref="quantity"></dd></div><div><dt>所在区域</dt><dd data-ref="zone"></dd></div><div><dt>精确格口</dt><dd data-ref="slot" class="accent"></dd></div></dl><div class="tw-slot-tools"><span>选择格口</span><button data-act="clearSlot">清除</button></div><div class="tw-grid" data-ref="grid" aria-label="设备格口"></div><p class="tw-content" data-ref="content"></p><button class="tw-detail" data-act="location">打开库位详情 ↗</button></section>
      <section class="tw-card"><header><span class="tw-card-icon">02</span><h2 data-ref="taskHeading">当前路径</h2></header><h3 data-ref="material"></h3><p class="tw-code" data-ref="path"></p><dl><div><dt>待取数量</dt><dd data-ref="remaining"></dd></div><div><dt>任务状态</dt><dd data-ref="taskStatus"></dd></div></dl><button data-act="task" class="tw-detail" hidden>返回拣货任务 ↗</button></section>
      <details class="tw-card tw-meta"><summary>数据与版本</summary><dl><div><dt>地图版本</dt><dd data-ref="version"></dd></div><div><dt>校准状态</dt><dd data-ref="calibration"></dd></div><div><dt>路径算法</dt><dd data-ref="algorithm"></dd></div><div><dt>图指纹</dt><dd data-ref="hash"></dd></div><div><dt>坐标单位</dt><dd>米</dd></div><div><dt>数据连接</dt><dd data-ref="source"></dd></div></dl></details>
    </aside></div>`;
  const refs=Object.fromEntries([...host.querySelectorAll('[data-ref]')].map(n=>[n.dataset.ref,n]));
  const text=(key,value)=>{refs[key].textContent=String(value??'—');};
  const act=name=>host.querySelector(`[data-act="${name}"]`);
  const calibrationValue=String(data.map.calibration_status||'');
  const calibrationLabel=calibrationValue.startsWith('demo_')?'规划布局':({measured:'已测量',verified:'已验证'}[calibrationValue]||calibrationValue);
  text('calibration',calibrationLabel);
  text('source',data.metadata?.source==='database_snapshot'?'仓库接口':'本地数据集');
  text('mapTitle',data.map.name);text('version',data.map.version);text('hash',data.map.graph_hash?.slice(0,12));
  text('stats',`${data.assets.length} 台设备 · ${data.assets.reduce((n,a)=>n+a.slots.length,0)} 个格口`);
  text('algorithm',data.route?.strategy||'未规划');text('distance',data.route?`${Number(data.route.total_distance_m).toFixed(2)} m`:'—');
  text('routeName',data.task?.pick_task_no||'多点领料路线');
  for(const a of data.assets){const o=dom('option',{value:a.code},a.name);refs.assetSelect.appendChild(o);}
  const planAssets=new Map();
  function drawPlan(){
    const {width_m:w,height_m:h}=data.map, root=svg('svg',{viewBox:`-.5 -.45 ${w+1} ${h+.9}`,role:'img','aria-label':'仓库平面图'});
    root.append(svg('rect',{x:0,y:0,width:w,height:h,rx:.1,fill:'#f7fafc',stroke:'#a2b6c5','stroke-width':.025}));
    for(const edge of data.map.edges){if(!edge.enabled)continue;
      const a=data.map.nodes.find(n=>n.code===edge.from_node),b=data.map.nodes.find(n=>n.code===edge.to_node);if(!a||!b)continue;
      root.append(svg('line',{x1:a.x_m,y1:h-a.y_m,x2:b.x_m,y2:h-b.y_m,stroke:'#d6e3ec','stroke-width':.045}));
    }
    for(const seg of data.route?.segments||[]){const points=seg.path_nodes.map(c=>data.map.nodes.find(n=>n.code===c)).filter(Boolean).map(n=>`${n.x_m},${h-n.y_m}`).join(' ');
      root.append(svg('polyline',{points,fill:'none',stroke:'#248fd3','stroke-width':.06,'stroke-linejoin':'round'}));}
    for(const a of data.assets){
      const g=svg('g',{'data-code':a.code,tabindex:0,role:'button','aria-label':a.name});
      const w=a.footprint.width_m,d=a.footprint.depth_m;
      const rect=svg('rect',{x:a.x_m-w/2,y:h-a.y_m-d/2,width:w,height:d,rx:.035,fill:'#7e98aa',stroke:'#546d82','stroke-width':.025});g.append(rect);
      const title=svg('title');title.textContent=a.name;g.append(title);
      const label=svg('text',{x:a.x_m,y:h-a.y_m-.45,'text-anchor':'middle','font-size':.12,fill:'#28475f'});label.textContent=a.name.length>8?a.name.slice(0,8)+'…':a.name;
      g.append(label);
      g.addEventListener('click',()=>select(a.code),{signal:abort.signal});
      g.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();select(a.code);}},{signal:abort.signal});
      root.append(g);planAssets.set(a.code,rect);
    }
    stops.forEach((stop,i)=>{const n=data.map.nodes.find(n=>n.code===stop.node);if(!n)return;
      root.append(svg('circle',{cx:n.x_m,cy:h-n.y_m,r:.13,fill:'#167ab8'}));
      const t=svg('text',{x:n.x_m,y:h-n.y_m+.055,'text-anchor':'middle','font-size':.15,fill:'#fff'});t.textContent=String(i+1);root.append(t);
    });
    refs.plan.replaceChildren(root);
  }
  drawPlan();
  function select(code,slotName=null,focus=false){
    const asset=data.assets.find(a=>a.code===code);if(!asset)return;
    selected=asset;selectedSlot=slotName?asset.slots.find(s=>s.name===slotName)||null:null;
    refs.assetSelect.value=asset.code;text('assetName',asset.name);text('assetCode',asset.code);
    text('type',STYLE_LABELS[asset.style]||'设备');text('kinds',asset.material_kind_count??'—');text('quantity',quantityLabel(asset.quantities_by_unit));
    text('zone',asset.zone||'研发仓');text('slot',selectedSlot?.name||'未选择');
    refs.grid.replaceChildren();refs.grid.className=`tw-grid style-${asset.style}`;
    refs.grid.style.setProperty('--cols',asset.style==='drawer_rack_100'?5:asset.style==='shelf_rack_6'?1:8);
    const slots=asset.style==='shelf_rack_6'?[...asset.slots].reverse():asset.slots;
    for(const s of slots){const b=dom('button',{'data-slot':s.name,title:s.name,'aria-pressed':s.name===selectedSlot?.name?'true':'false'},s.name);
      if(s.material_kind_count>0)b.classList.add('has-content');
      b.addEventListener('click',()=>select(asset.code,s.name,true),{signal:abort.signal});if(asset.style==='split_configurable'&&s.geometry){const g=s.geometry;b.style.position='absolute';b.style.left=`${(g.x_norm-g.width_norm/2)*100}%`;b.style.top=`${(g.y_norm-g.height_norm/2)*100}%`;b.style.width=`calc(${g.width_norm*100}% - 3px)`;b.style.height=`calc(${g.height_norm*100}% - 3px)`;}refs.grid.appendChild(b);
    }
    const items=selectedSlot?.materials||[];
    text('content',selectedSlot?(items.length?items.map(i=>`${i.name} · ${i.quantity} ${i.unit}`).join('；'):selectedSlot.location_id?'该格口暂无物料记录':'该格口未分配库位'):'点击设备或格口查看存放信息');
    act('location').disabled=!selectedSlot?.location_id&&!asset.location_id;
    for(const [c,rect]of planAssets){rect.setAttribute('fill',c===asset.code?'#54a2d1':'#7e98aa');rect.setAttribute('stroke',c===asset.code?'#0677c2':'#546d82');}
    engine?.select(asset.code,selectedSlot?.name||null,focus);
  }
  function updateStop(index,focus=false){
    const taskComplete=data.task?.status==='completed';
    act('prev').hidden=taskComplete;act('tour').hidden=taskComplete;act('next').hidden=taskComplete;
    if(!stops.length){text('step',taskComplete?'已完成':'—');return;}
    currentIndex=(index+stops.length)%stops.length;const stop=stops[currentIndex];
    text('step',taskComplete?'已完成':`${currentIndex+1} / ${stops.length}`);
    const al=allocation(),a=data.assets.find(a=>a.pick_node_code===stop.node);
    if(al){const byLocation=findAssetForLocation(data,al.location_id);const slot=byLocation?.slots.find(s=>s.location_id===al.location_id||s.descendant_ids?.includes(al.location_id));if(byLocation)select(byLocation.code,slot?.name,focus);}
    else if(a)select(a.code,data.preview_slots?.[a.code]||null,focus);
    engine?.setStop(stop.node,false);
    for(const b of refs.stops.children)b.classList.toggle('is-active',Number(b.dataset.index)===currentIndex);
    text('taskHeading',taskComplete?'已完成任务':data.task?'当前任务':'当前路径');text('material',al?.material_name||a?.name||'待选择');
    text('path',al?.full_path||a?.full_path||'');text('remaining',taskComplete?'0':(al?.remaining_quantity??'—'));
    const states={draft:'待开始',ready:'待拣货',in_progress:'拣货中',completed:'已完成',cancelled:'已取消',needs_replan:'待重新规划'};
    text('taskStatus',data.task?(states[data.task.status]||data.task.status):'路径预览');
  }
  stops.forEach((stop,i)=>{const a=data.assets.find(a=>a.pick_node_code===stop.node);const b=dom('button',{'data-index':i},`${i+1} ${a?.name||stop.node}`);b.addEventListener('click',()=>{stopTour();updateStop(i,true);},{signal:abort.signal});refs.stops.appendChild(b);});
  async function setMode(next){
    mode=next;const token=++generation;
    act('3d').classList.toggle('is-active',mode==='3d');act('2d').classList.toggle('is-active',mode==='2d');
    refs.canvas.hidden=mode!=='3d';refs.plan.hidden=mode!=='2d';
    text('hints',mode==='3d'?'拖动旋转 · 滚轮缩放 · 右键平移':'点击设备选择库位');
    if(mode==='2d'){text('engine','平面图');return;}
    if(engine){engine.resize();text('engine','3D 视图');return;}
    text('engine','载入 3D 视图…');
    try{
      const module=await (options.loadRenderer?options.loadRenderer():import('./scene.js'));
      if(disposed||token!==generation||mode!=='3d')return;
      engine=new module.WarehouseScene(refs.canvas,data,{onSelect:(code,slot)=>select(code,slot),onError:err=>fallback(err)});
      rendererStatus='ready';text('engine','3D 视图');refs.error.hidden=true;
      if(selected)engine.select(selected.code,selectedSlot?.name,!!options.focusLocationId);if(stops[currentIndex])engine.setStop(stops[currentIndex].node);
    }catch(err){if(disposed||token!==generation)return;fallback(err);}
  }
  function fallback(err){rendererStatus='unavailable';rendererError=String(err?.message||err);engine?.dispose();engine=null;refs.canvas.replaceChildren();
    refs.error.hidden=false;refs.error.textContent='3D 视图暂不可用，已切换平面图。';void setMode('2d');}
  function stopTour(){touring=false;cancelAnimationFrame(raf);const button=act('tour');if(button)button.textContent='开始巡游';}
  function tick(now){if(!touring||disposed)return;const elapsed=(now-tourStart)/1000,progress=(elapsed%24)/24;
    engine?.setTour(progress);const next=stopFractions.findIndex(f=>progress<=f);const index=next<0?stops.length-1:next;
    if(index!==currentIndex){updateStop(index,false);engine?.setTour(progress);}raf=requestAnimationFrame(tick);}
  host.addEventListener('click',event=>{
    const b=event.target.closest('button');if(!b)return;
    const a=b.dataset.act,p=b.dataset.preset;
    if(p){engine?.setPreset(p);return;}
    if(a==='3d'||a==='2d'){void setMode(a);return;}
    if(a==='focus'&&selected)engine?.select(selected.code,selectedSlot?.name,true);
    if(a==='prev'||a==='next'){stopTour();updateStop(currentIndex+(a==='prev'?-1:1),true);}
    if(a==='clearSlot'&&selected)select(selected.code);
    if(a==='tour'){if(touring)stopTour();else if(stops.length){touring=true;tourStart=performance.now();act('tour').textContent='暂停巡游';raf=requestAnimationFrame(tick);}}
    if(a==='location')options.onOpenLocation?.(selectedSlot?.location_id||selected?.location_id);
    if(a==='task')options.onOpenTask?.(data.task?.id);
    if(a==='refresh')options.onRefresh?.();
    if(a==='closure')options.onToggleClosure?.();
    if(a==='full'){if(document.fullscreenElement)document.exitFullscreen?.();else host.requestFullscreen?.().catch(()=>{});}
  },{signal:abort.signal});
  refs.assetSelect.addEventListener('change',()=>{stopTour();select(refs.assetSelect.value,null,true);},{signal:abort.signal});
  act('refresh').hidden=!options.onRefresh;act('task').hidden=!data.task;
  act('closure').hidden=!options.onToggleClosure;
  if(options.onToggleClosure) act('closure').textContent=options.closureActive?'恢复通道':'封闭通道';
  const nextNode=data.task?.next_stop?.route_node_code;const start=stops.findIndex(s=>s.node===nextNode);updateStop(start<0?0:start);
  if(options.focusLocationId){const a=findAssetForLocation(data,options.focusLocationId);if(a){const s=a.slots.find(s=>s.location_id===options.focusLocationId||s.descendant_ids?.includes(options.focusLocationId));select(a.code,s?.name);}}
  if(!selected&&data.assets.length)select(data.assets[0].code);
  if(selected&&!refs.assetName.textContent)select(selected.code);
  void setMode(mode);
  const stats=()=>({mode,rendererStatus,rendererError,selected:selected?.code,slot:selectedSlot?.name||null,currentIndex,touring,...engine?.stats()});
  host.addEventListener('warehouse-twin:diagnostics',event=>{if(event.detail)event.detail.stats=stats();},{signal:abort.signal});
  return { select, setMode, updateStop,
    stats,
    dispose(){disposed=true;generation++;abort.abort();stopTour();engine?.dispose();engine=null;host.replaceChildren();host.classList.remove('mb-twin');},
  };
}
