import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { worldPoint, facingRadians, projectSlot, flattenRoute, labelWorldWidth } from './model.js';
import { cameraInsideAnyBox, chooseFocusPose, minimumOrbitDistance, occlusionCount, projectedBoxCoverage, resolveCameraPenetration, visuallyFramed, worldBox } from './cameraSafety.js';
import { updateLabelVisibility } from './labelVisibility.js';

/** Own every GPU object exactly once. Never share disposable resources across instances. */
class Resources {
  constructor() { this.items = new Set(); }
  own(item) { this.items.add(item); return item; }
  dispose() { for (const item of this.items) item.dispose?.(); this.items.clear(); }
}
export class WarehouseScene {
  constructor(host, snapshot, { onSelect = () => {}, onError = () => {}, compact = false } = {}) {
    this.host = host; this.data = snapshot; this.onSelect = onSelect; this.onError = onError;
    this.compact = compact; this.resources = new Resources(); this.assetObjects = new Map(); this.collisionObjects = new Map(); this.staticOccluders = [];
    this.clickables = []; this.frame = 0; this.closed = false; this.dirty = true;
    this.tween = null; this.selectedCode = null; this.slotName = null; this.activeNode = null;
    this.handles = []; this.fronts = new Map(); this.startPointer = null; this.tour = null; this.labels = [];
    this.scene = new THREE.Scene(); this.scene.background = new THREE.Color('#eaf0f4');
    this.camera = new THREE.PerspectiveCamera(40, 1, .03, 100);
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false, powerPreference: 'high-performance' });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping; this.renderer.toneMappingExposure = 1.16;
    this.renderer.shadowMap.enabled = true; this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    host.appendChild(this.renderer.domElement); this.renderer.domElement.className = 'mb-twin-canvas';
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true; this.controls.dampingFactor = .1;
    this.controls.minDistance = .95; this.controls.maxDistance = 24; this.controls.maxPolarAngle = Math.PI * .475;
    this.controls.addEventListener('start', () => { this.tween = null; });
    this.controls.addEventListener('change', () => { this.dirty = true; });
    this.ray = new THREE.Raycaster(); this.pointer = new THREE.Vector2();
    try {
    this.bindEvents(); this.makeLights(); this.makeRoom();
    for (const asset of snapshot.assets) this.makeAsset(asset);
    this.makeRoute(); this.setPreset('overview', true); this.resize();
    this.observer = new ResizeObserver(() => this.resize()); this.observer.observe(host);
    this.loop = this.loop.bind(this); this.frame = requestAnimationFrame(this.loop);
    } catch (error) { this.dispose(); throw error; }
  }
  material(color, options = {}) {
    return this.resources.own(new THREE.MeshStandardMaterial({ color, roughness: .52, metalness: .15, ...options }));
  }
  box(parent, w, h, d, x, y, z, material, shadows = true) {
    if (!this.cube) this.cube = this.resources.own(new THREE.BoxGeometry(1, 1, 1));
    const m = new THREE.Mesh(this.cube, material); m.scale.set(w,h,d); m.position.set(x,y,z);
    m.castShadow = shadows; m.receiveShadow = true; parent.add(m); return m;
  }
  label(parent, text, x, y, z, width = 1, options = {}) {
    const { dark = false, ownerCode = null, kind = 'asset', maxDistance = 11, targetPixels = 220 } = options;
    const canvas = document.createElement('canvas'); canvas.width = 768; canvas.height = 128;
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = dark ? '#173b58' : '#f9fcfe'; ctx.beginPath(); ctx.roundRect(3,3,762,122,22); ctx.fill();
    ctx.strokeStyle = dark ? '#3d86a6' : '#c5d3de'; ctx.lineWidth = 4; ctx.stroke();
    ctx.fillStyle = dark ? '#eefaff' : '#223f58';
    ctx.font = '700 40px "Microsoft YaHei", "PingFang SC", sans-serif';
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText(text,384,64,720);
    const tex = this.resources.own(new THREE.CanvasTexture(canvas)); tex.colorSpace = THREE.SRGBColorSpace;
    tex.minFilter = THREE.LinearFilter; tex.magFilter = THREE.LinearFilter;
    const mat = this.resources.own(new THREE.SpriteMaterial({ map: tex, depthTest: true, depthWrite: false, transparent: true }));
    const sprite = new THREE.Sprite(mat); sprite.position.set(x,y,z); sprite.scale.set(width,width*128/768,1);
    this.labels.push({ sprite, width, ownerCode, kind, maxDistance, targetPixels });
    parent.add(sprite); return sprite;
  }
  wallSign(text, x, y, z, width = 2.25, height = .34) {
    const canvas = document.createElement('canvas'); canvas.width = 1024; canvas.height = 160;
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#153a58'; ctx.beginPath(); ctx.roundRect(4,4,1016,152,22); ctx.fill();
    ctx.strokeStyle = '#3f83a2'; ctx.lineWidth = 5; ctx.stroke();
    ctx.fillStyle = '#eef9ff'; ctx.font = '700 44px "Microsoft YaHei", "PingFang SC", sans-serif';
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText(text,512,80,940);
    const texture = this.resources.own(new THREE.CanvasTexture(canvas)); texture.colorSpace = THREE.SRGBColorSpace;
    const geometry = this.resources.own(new THREE.PlaneGeometry(width,height));
    const material = this.resources.own(new THREE.MeshBasicMaterial({ map:texture, transparent:true, depthTest:true, depthWrite:false }));
    const sign = new THREE.Mesh(geometry,material); sign.position.set(x,y,z); this.scene.add(sign); return sign;
  }
  makeLights() {
    this.scene.add(new THREE.HemisphereLight(0xf4f9fd,0x7f8f9d,1.45));
    const key = new THREE.DirectionalLight(0xfffbf2,2.35); key.position.set(-3,9,4); key.castShadow = true;
    key.shadow.mapSize.set(2048,2048); key.shadow.camera.left=-8; key.shadow.camera.right=8;
    key.shadow.camera.top=8; key.shadow.camera.bottom=-8; key.shadow.normalBias=.022;
    this.scene.add(key); this.shadowLight = key;
    key.shadow.radius=3.2;
    const fill = new THREE.DirectionalLight(0xb8dbf4,.58); fill.position.set(5,4,-4); this.scene.add(fill);
    const frontFill=new THREE.DirectionalLight(0xffffff,.35);frontFill.position.set(0,3,7);this.scene.add(frontFill);
  }
  makeRoom() {
    const map=this.data.map, w=map.width_m, d=map.height_m;
    const floor=this.material('#e5ebef',{roughness:.94,metalness:.02}), wall=this.material('#f5f7f9',{roughness:.96});
    this.box(this.scene,w,.10,d,0,-.065,0,floor,false);
    // Cutaway walls: do not hide the scene when the orbit camera changes direction.
    const northWall=this.box(this.scene,w,1.7,.08,0,.85,-d/2,wall,false);
    const westWall=this.box(this.scene,.08,.32,d,-w/2,.16,0,wall,false);
    const eastWall=this.box(this.scene,.08,.32,d,w/2,.16,0,wall,false);
    this.staticOccluders.push({code:'__wall_north',object:northWall},{code:'__wall_west',object:westWall},{code:'__wall_east',object:eastWall});
    const strips=this.material('#c7d2db',{roughness:.92});
    for(let x=-w/2+.5;x<w/2;x+=.5) this.box(this.scene,.005,.006,d,x,.001,0,strips,false);
    for(let z=-d/2+.5;z<d/2;z+=.5) this.box(this.scene,w,.006,.005,0,.001,z,strips,false);
    const line=this.material('#e2b85f',{roughness:.9});
    this.box(this.scene,.025,.01,d-.7,-w/2+.38,.013,0,line,false);
    this.box(this.scene,.025,.01,d-.7,w/2-.38,.013,0,line,false);
    this.wallSign('MATERIALBRAIN  /  研发仓',0,1.26,-d/2+.043,2.15,.34);
    for(const deco of this.data.decorations || []) {
      const g=new THREE.Group(); g.position.set(...worldPoint(map,deco.x_m,deco.y_m)); this.scene.add(g);
      if(deco.kind==='pack') {
        const wood=this.material('#c9ac83'), metal=this.material('#344c60'), blue=this.material('#3179aa');
        this.box(g,1.4,.07,.70,0,.88,0,wood);
        for(const x of [-.62,.62])for(const z of [-.27,.27]) this.box(g,.045,.84,.045,x,.43,z,metal);
        this.box(g,.42,.26,.025,.24,1.10,-.10,metal); this.box(g,.35,.22,.01,.24,1.10,-.083,blue);
        this.box(g,.035,.18,.04,.24,.96,-.11,metal); this.box(g,.23,.02,.15,.24,.91,-.11,metal);
        this.box(g,.36,.13,.27,-.40,.98,.06,blue); this.label(g,'PACK  /  备料工作台',0,1.42,0,1.2,{kind:'landmark',maxDistance:14,targetPixels:210});
      }
    }
  }
  instanced(parent, placements, material) {
    if(!placements.length) return null;
    const mesh=this.resources.own(new THREE.InstancedMesh(this.cube,material,placements.length));
    const dummy=new THREE.Object3D();
    placements.forEach((p,i) => {dummy.position.set(p.x,p.y,p.z);dummy.scale.set(p.w,p.h,p.d);dummy.updateMatrix();mesh.setMatrixAt(i,dummy.matrix);});
    mesh.instanceMatrix.needsUpdate=true; mesh.castShadow=true; mesh.receiveShadow=true; parent.add(mesh); return mesh;
  }
  makeAsset(a) {
    const map=this.data.map, g=new THREE.Group(); g.position.set(...worldPoint(map,a.x_m,a.y_m));
    g.userData.assetCode=a.code; g.rotation.y=facingRadians(a.facing); this.scene.add(g); this.assetObjects.set(a.code,g);
    const {width:w,height:h,depth:d}=a.dimensions;
    const frame=this.material('#697d8d',{metalness:.55}), dark=this.material('#34495e'), front=this.material('#becdda',{metalness:.3});
    const handle=this.material('#667f93',{metalness:.4}), white=this.material('#edf4f8',{roughness:.8});
    const isCase = a.style === 'standard_56' || a.style === 'split_configurable';
    const collisionHeight=isCase?Math.max(1.40,.96+d*Math.sin(Math.PI/3)+.04):h;
    const hit=this.box(g,isCase?w+.14:w,collisionHeight,isCase?d+.14:Math.max(d,.25),0,
      collisionHeight/2,0,this.resources.own(new THREE.MeshBasicMaterial({visible:false})),false);
    hit.userData={assetCode:a.code};this.clickables.push(hit);this.collisionObjects.set(a.code,hit);
    const bases=[], handles=[], cards=[];
    if(a.style==='drawer_rack_100') {
      this.box(g,w,h,d,0,h/2,0,frame);
      this.box(g,w+.035,.035,d+.025,0,h+.018,0,dark);
      this.box(g,w+.035,.075,d*.92,0,.038,0,dark);
      for(const x of [-w*.40,w*.40]) this.box(g,.055,.075,d*.76,x,.038,0,dark);
      this.box(g,w*.94,h-.14,.02,0,h/2,d/2+.006,dark);
      for(const slot of a.slots) {
        const p=projectSlot(a,slot); if(!p)continue;
        bases.push({x:p.x,y:p.y,z:p.z+.01,w:p.width,h:p.height,d:.027});
        handles.push({x:p.x,y:p.y-.006,z:p.z+.032,w:p.width*.38,h:.006,d:.018});
        cards.push({x:p.x,y:p.y+.011,z:p.z+.03,w:p.width*.54,h:.009,d:.002});
      }
      const fronts=this.instanced(g,bases,front);this.instanced(g,handles,handle);this.instanced(g,cards,white);
      fronts.userData={assetCode:a.code,slotNames:a.slots.filter(s=>projectSlot(a,s)).map(s=>s.name)};
      this.clickables.push(fronts);
      this.fronts.set(a.code,{mesh:fronts,placements:bases});
      for(let i=1;i<4;i++) this.box(g,w*.96,.009,.012,0,.10+i*(h-.2)/4,d/2+.035,frame);
      for(const x of [-w*.39,w*.39]) this.box(g,.04,.075,d*.68,x,.038,0,dark);
      this.label(g,a.name,0,h+.17,0,1.04,{ownerCode:a.code,kind:'asset',maxDistance:10,targetPixels:230});
    } else if(a.style==='standard_56'||a.style==='split_configurable') {
      // V2 organizer case: shallow body + framed translucent lid on a proper workbench.
      const benchW=Math.max(.88,w+.20),benchD=Math.max(.56,d+.16),benchTopY=.76;
      const benchTop=this.material('#8ba0af',{metalness:.32,roughness:.58});
      const caseBody=this.material('#36566f',{metalness:.20,roughness:.52});
      const cellMat=this.material('#85b9da',{roughness:.42,metalness:.08});
      const dividerMat=this.material('#d7e4ed',{roughness:.72,metalness:.04});
      this.box(g,benchW,.055,benchD,0,benchTopY,0,benchTop);
      for(const x of [-benchW*.43,benchW*.43]) for(const z of [-benchD*.40,benchD*.40])
        this.box(g,.055,.72,.055,x,.37,z,dark);
      this.box(g,benchW*.88,.04,.045,0,.39,-benchD*.40,dark);
      this.box(g,.045,.04,benchD*.72,-benchW*.43,.39,0,dark);
      this.box(g,.045,.04,benchD*.72,benchW*.43,.39,0,dark);
      const caseBaseY=benchTopY+.055;
      this.box(g,w,.060,d,0,caseBaseY,0,caseBody);
      this.box(g,w,.035,.035,0,caseBaseY+.055,d/2-.018,frame);
      this.box(g,w,.035,.035,0,caseBaseY+.055,-d/2+.018,frame);
      this.box(g,.035,.035,d,-w/2+.018,caseBaseY+.055,0,frame);
      this.box(g,.035,.035,d,w/2-.018,caseBaseY+.055,0,frame);
      for(const slot of a.slots) {
        const p=projectSlot(a,slot);if(!p)continue;
        bases.push({x:p.x,y:caseBaseY+.065,z:p.z,w:p.width,h:.018,d:p.depth});
      }
      const cells=this.instanced(g,bases,cellMat);
      if(cells){cells.userData={assetCode:a.code,slotNames:a.slots.filter(s=>projectSlot(a,s)).map(s=>s.name)};this.clickables.push(cells);}
      // Thin divider ribs, visually closer to the existing OrganizerBox3D grid.
      for(const slot of a.slots) {
        const p=projectSlot(a,slot);if(!p)continue;
        handles.push({x:p.x-p.width*.55,y:caseBaseY+.083,z:p.z,w:.0035,h:.028,d:p.depth*1.08});
        cards.push({x:p.x,y:caseBaseY+.083,z:p.z-p.depth*.55,w:p.width*1.08,h:.028,d:.0035});
      }
      this.instanced(g,handles,dividerMat);this.instanced(g,cards,dividerMat);
      // Hinged lid: light frame, real hinge blocks and a low-opacity panel.
      const lid=new THREE.Group();lid.position.set(0,caseBaseY+.075,-d*.50);lid.rotation.x=-THREE.MathUtils.degToRad(58);g.add(lid);
      const lidFrame=this.material('#48677d',{metalness:.28,roughness:.45});
      for(const x of [-w/2+.008,w/2-.008]) this.box(lid,.016,.018,d,x,.009,d*.50,lidFrame);
      for(const z of [.008,d-.008]) this.box(lid,w,.018,.016,0,.009,z,lidFrame);
      this.box(lid,w-.035,.012,d-.035,0,.014,d*.50,this.material('#bed7e7',{transparent:true,opacity:.18,roughness:.20,depthWrite:false}),false);
      for(const x of [-w*.34,w*.34]) this.box(g,.075,.028,.035,x,caseBaseY+.078,-d*.50,dark);
      this.box(lid,.12,.025,.022,0,.018,d-.015,dark);
      this.label(g,a.name,0,1.28,0,1.10,{ownerCode:a.code,kind:'asset',maxDistance:9,targetPixels:230});
    } else if(a.style==='shelf_rack_6') {
      for(const x of [-w/2+.023,w/2-.023])for(const z of [-d/2+.023,d/2-.023])this.box(g,.045,h,.045,x,h/2,z,frame);
      for(let l=0;l<6;l++) {
        const y=.12+l*(h-.24)/5;
        const shelf=this.box(g,w,.028,d,0,y,0,front);
        shelf.userData={assetCode:a.code,slotName:'L'+String(l+1).padStart(2,'0')};this.clickables.push(shelf);
        this.box(g,w,.044,.022,0,y-.018,d/2,frame);
        this.label(g,'L'+String(l+1).padStart(2,'0'),-w*.56,y+.02,d/2,.20,{ownerCode:a.code,kind:'detail',maxDistance:5,targetPixels:150});
        const boxes=(a.storage_boxes||[]).filter(b=>b.level===l+1);
        for(let i=0;i<boxes.length;i++) {
          const bx=-w*.37+(i+.5)*(w*.74/Math.max(1,boxes.length));
          const bw=Math.min(.24,w*.68/boxes.length),bh=.16,bd=d*.70;
          const mat=this.material(i%2?'#8ca5b8':'#477b9f');
          this.box(g,bw,bh,bd,bx,y+bh/2+.025,0,mat);
          this.box(g,bw*.80,.025,bd*.72,bx,y+bh+.02,0,dark);
          this.box(g,bw*.64,.045,.003,bx,y+.09,bd/2+.002,white,false);
        }
      }
      this.label(g,a.name,0,h+.18,0,1.12,{ownerCode:a.code,kind:'asset',maxDistance:11,targetPixels:230});
    } else { this.box(g,w,h,d,0,h/2,0,frame); this.label(g,a.name,0,h+.18,0,1.04,{ownerCode:a.code,kind:'asset',maxDistance:10,targetPixels:230}); }
    // Selection border is separate; never scale real geometry when selected.
    const outlineMat=this.resources.own(new THREE.LineBasicMaterial({color:0x2689ed}));
    const boundGeom=this.resources.own(new THREE.BoxGeometry(w+.07,a.style==='standard_56'||a.style==='split_configurable'?1.32:h+.04,Math.max(d,.25)+.07));
    const outline=new THREE.LineSegments(this.resources.own(new THREE.EdgesGeometry(boundGeom)),outlineMat);
    outline.position.y=(a.style==='standard_56'||a.style==='split_configurable'?1.32:h+.04)/2;outline.visible=false;g.add(outline);g.userData.outline=outline;
    const highlight=this.box(g,1,1,1,0,0,0,this.material('#ffc24b',{emissive:0xb96b08,emissiveIntensity:.55,transparent:true,opacity:.88}),false);
    highlight.visible=false;g.userData.highlight=highlight;
  }
  makeRoute() {
    this.routeGroup=new THREE.Group();this.scene.add(this.routeGroup);
    const points=flattenRoute(this.data.map,this.data.route).map(n=>new THREE.Vector3(...worldPoint(this.data.map,n.x_m,n.y_m,.018)));
    this.routePoints=points;
    const mat=this.material('#2d98d5',{emissive:0x123d61,emissiveIntensity:.34,roughness:.62,transparent:true,opacity:.90});
    const arrowMat=this.resources.own(new THREE.MeshBasicMaterial({color:0x2187c2,transparent:true,opacity:.92,side:THREE.DoubleSide,depthWrite:false}));
    if(!this.routeArrowGeometry){
      const geometry=new THREE.BufferGeometry();
      geometry.setAttribute('position',new THREE.Float32BufferAttribute([-.055,0,-.075,.055,0,-.075,0,0,.085],3));
      geometry.computeVertexNormals();this.routeArrowGeometry=this.resources.own(geometry);
    }
    for(let i=1;i<points.length;i++) {
      const p=points[i-1],q=points[i],delta=q.clone().sub(p),length=delta.length();if(length<1e-5)continue;
      const mid=p.clone().add(q).multiplyScalar(.5),angle=Math.atan2(delta.x,delta.z);
      const ribbon=this.box(this.routeGroup,.048,.008,length,mid.x,.018,mid.z,mat,false);ribbon.rotation.y=angle;
      const fractions=length>1.45?[.30,.70]:length>.48?[.54]:[];
      for(const fraction of fractions){
        const arrow=new THREE.Mesh(this.routeArrowGeometry,arrowMat);arrow.position.copy(p).lerp(q,fraction);arrow.position.y=.028;arrow.rotation.y=angle;this.routeGroup.add(arrow);
      }
    }
    const by=new Map(this.data.map.nodes.map(n=>[n.code,n]));
    (this.data.route?.ordered_stop_nodes||[]).forEach((code,i)=>{const n=by.get(code);if(n)this.label(this.routeGroup,String(i+1),...worldPoint(this.data.map,n.x_m,n.y_m,.18),.25,{dark:true,kind:'route',maxDistance:16,targetPixels:170});});
    this.marker=new THREE.Mesh(this.resources.own(new THREE.CylinderGeometry(.075,.075,.028,24)),this.material('#ffbd57',{emissive:0x7c4d0d,emissiveIntensity:.52,roughness:.5}));
    this.marker.visible=false;this.routeGroup.add(this.marker);
    const closed=new Set(this.data.closed_edge_codes||[]);
    if(closed.size){
      const nodes=new Map(this.data.map.nodes.map(n=>[n.code,n]));
      const blocked=this.material('#d85b55',{emissive:0x7d2725,emissiveIntensity:.45,roughness:.55});
      for(const edge of this.data.map.edges){
        if(!closed.has(edge.code))continue;
        const a=nodes.get(edge.from_node),b=nodes.get(edge.to_node);if(!a||!b)continue;
        const p=new THREE.Vector3(...worldPoint(this.data.map,a.x_m,a.y_m,.035));
        const q=new THREE.Vector3(...worldPoint(this.data.map,b.x_m,b.y_m,.035));
        const mid=p.clone().lerp(q,.5);
        const bar1=this.box(this.routeGroup,.30,.022,.045,mid.x,.038,mid.z,blocked,false);bar1.rotation.y=Math.PI/4;
        const bar2=this.box(this.routeGroup,.30,.022,.045,mid.x,.038,mid.z,blocked,false);bar2.rotation.y=-Math.PI/4;
      }
    }
  }
  collisionBoxes(padding = .04) {
    const boxes=[];
    for(const [code,object] of this.collisionObjects) boxes.push({code,box:worldBox(object,padding)});
    return boxes;
  }
  occlusionBoxes(padding = .01) {
    const boxes=this.collisionBoxes(padding);
    for(const item of this.staticOccluders) boxes.push({code:item.code,box:worldBox(item.object,padding)});
    return boxes;
  }
  select(code, slotName = null, focus = false) {
    this.selectedCode=code;this.slotName=slotName;
    for(const [key,g] of this.assetObjects){g.userData.outline.visible=key===code;g.userData.highlight.visible=false;}
    const asset=this.data.assets.find(a=>a.code===code),group=this.assetObjects.get(code);if(!asset||!group)return;
    const slot=asset.slots.find(s=>s.name===slotName),p=slot&&projectSlot(asset,slot);
    if(p){const hi=group.userData.highlight;hi.scale.set(p.width,p.height,p.depth);hi.position.set(p.x,p.y+(p.plane==='front'?0:.025),p.z+(p.plane==='front'?.030:0));hi.visible=true;}
    if(focus){
      const collision=this.collisionObjects.get(code);
      if(collision){
        const obstacles=this.collisionBoxes(.08),focusBox=worldBox(collision,.06);
        const topSurface=asset.style==='standard_56'||asset.style==='split_configurable';
        const pose=chooseFocusPose({box:focusBox,facingRadians:facingRadians(asset.facing),camera:this.camera,obstacles,selectedCode:code,topSurface});
        // Keep a conservative fitted distance so the maximum permitted zoom remains
        // visually framed even after the operator rotates around the selected asset.
        this.controls.minDistance=minimumOrbitDistance(focusBox,this.camera,1.28);
        this.lastFocusDiagnostics={assetCode:code,occlusions:pose.occlusions,minDistance:this.controls.minDistance};
        this.goCamera(pose.position,pose.target);
      }
    }
    this.dirty=true;
  }
  setStop(nodeCode, animate = false) {
    const node=this.data.map.nodes.find(n=>n.code===nodeCode);if(!node)return;
    this.activeNode=nodeCode;
    const dst=new THREE.Vector3(...worldPoint(this.data.map,node.x_m,node.y_m,.17));
    if(animate&&this.marker.visible){this.moveMarker={from:this.marker.position.clone(),to:dst,start:performance.now()};}else this.marker.position.copy(dst);
    this.marker.visible=true;this.dirty=true;
  }
  setTour(progress) {
    if(this.routePoints.length<2)return;
    const lengths=this.routePoints.slice(1).map((p,i)=>p.distanceTo(this.routePoints[i]));
    let remaining=Math.max(0,Math.min(1,progress))*lengths.reduce((a,b)=>a+b,0);
    for(let i=0;i<lengths.length;i++){
      if(remaining<=lengths[i]||i===lengths.length-1){this.marker.position.copy(this.routePoints[i]).lerp(this.routePoints[i+1],Math.min(1,remaining/lengths[i]));this.marker.position.y=.17;break;}remaining-=lengths[i];
    }
    this.marker.visible=true;this.dirty=true;
  }
  goCamera(position,target,instant=false){
    if(instant){this.camera.position.copy(position);this.controls.target.copy(target);this.controls.update();}
    else this.tween={from:this.camera.position.clone(),fromTarget:this.controls.target.clone(),to:position,target,start:performance.now()};
    this.dirty=true;
  }
  setPreset(name,instant=false){
    this.controls.minDistance=.95;
    const span=Math.max(this.data.map.width_m,this.data.map.height_m),target=new THREE.Vector3(0,.20,0);
    const p=name==='top'?new THREE.Vector3(0,span*1.35,.01):name==='route'?new THREE.Vector3(0,span*.65,span*.97):new THREE.Vector3(span*.70,span*.95,span*.97);
    this.goCamera(p,target,instant);
  }
  bindEvents(){
    this.down=e=>{this.startPointer=[e.clientX,e.clientY];};
    this.up=e=>{
      if(!this.startPointer||Math.hypot(e.clientX-this.startPointer[0],e.clientY-this.startPointer[1])>5)return;
      this.startPointer=null;const r=this.renderer.domElement.getBoundingClientRect();
      this.pointer.set((e.clientX-r.left)/r.width*2-1,-(e.clientY-r.top)/r.height*2+1);
      this.ray.setFromCamera(this.pointer,this.camera);const hits=this.ray.intersectObjects(this.clickables,false);const first=hits[0];
      if(first){const code=first.object.userData.assetCode;const hit=hits.find(h=>h.object.userData.assetCode===code&&(h.object.userData.slotNames||h.object.userData.slotName))||first;const slot=hit.object.userData.slotName||hit.object.userData.slotNames?.[hit.instanceId]||null;this.onSelect(code,slot);this.select(code,slot,false);}
    };
    this.lost=e=>{e.preventDefault();this.onError(new Error('3D 视图暂不可用，已切换平面图'));};
    this.renderer.domElement.addEventListener('pointerdown',this.down);
    this.renderer.domElement.addEventListener('pointerup',this.up);
    this.renderer.domElement.addEventListener('webglcontextlost',this.lost);
  }
  resize(){if(this.closed)return;const r=this.host.getBoundingClientRect(),w=Math.max(1,r.width),h=Math.max(1,r.height);this.renderer.setSize(w,h,false);this.camera.aspect=w/h;this.camera.updateProjectionMatrix();this.dirty=true;}
  loop(now){
    if(this.closed)return;this.frame=requestAnimationFrame(this.loop);if(document.hidden)return;
    if(this.tween){const t=Math.min(1,(now-this.tween.start)/650),u=1-(1-t)**3;this.camera.position.lerpVectors(this.tween.from,this.tween.to,u);this.controls.target.lerpVectors(this.tween.fromTarget,this.tween.target,u);if(t===1)this.tween=null;this.dirty=true;}
    if(this.moveMarker){const m=this.moveMarker,t=Math.min(1,(now-m.start)/500);this.marker.position.lerpVectors(m.from,m.to,t);if(t===1)this.moveMarker=null;this.dirty=true;}
    this.controls.update();
    const resolved=resolveCameraPenetration(this.camera.position,this.collisionBoxes(.03),.16,.12);
    if(resolved.moved){this.camera.position.copy(resolved.position);this.controls.update();this.dirty=true;}
    if(this.selectedCode&&!this.tween){
      const selected=this.collisionObjects.get(this.selectedCode);
      if(selected){
        const selectedBox=worldBox(selected,.06);
        this.camera.updateMatrixWorld();
        const coverage=projectedBoxCoverage(selectedBox,this.camera);
        if(coverage.clipped||coverage.area>.72){
          const direction=this.camera.position.clone().sub(this.controls.target);
          if(direction.lengthSq()<1e-6) direction.set(0,0,1);
          direction.normalize();
          const safeDistance=minimumOrbitDistance(selectedBox,this.camera,1.28);
          this.camera.position.copy(this.controls.target).addScaledVector(direction,safeDistance);
          this.controls.update();
          this.dirty=true;
        }
      }
    }
    if(this.dirty){
      this.camera.updateMatrixWorld();
      this.labelDiagnostics=updateLabelVisibility(this.labels,this.camera,this.occlusionBoxes(.015),{selectedCode:this.selectedCode,maxVisible:this.selectedCode?10:7});
      const position = new THREE.Vector3();
      for (const item of this.labels) {
        const { sprite, width, targetPixels = 220 } = item;if(!sprite.visible)continue;
        sprite.getWorldPosition(position);position.applyMatrix4(this.camera.matrixWorldInverse);
        const size=labelWorldWidth(width,Math.max(.01,-position.z),this.camera.fov,this.host.clientHeight,targetPixels);
        sprite.scale.set(size,size*128/768,1);
      }
      this.renderer.render(this.scene,this.camera);this.dirty=false;
    }
  }
  stats(){
    this.camera.updateMatrixWorld();
    const obstacles=this.collisionBoxes(.02);
    const selected=this.selectedCode?obstacles.find(item=>item.code===this.selectedCode):null;
    const coverage=selected?projectedBoxCoverage(selected.box,this.camera):null;
    return {renderer:'THREE.WebGLRenderer',revision:THREE.REVISION,drawCalls:this.renderer.info.render.calls,triangles:this.renderer.info.render.triangles,geometries:this.renderer.info.memory.geometries,textures:this.renderer.info.memory.textures,assetCount:this.assetObjects.size,cameraInsideAsset:cameraInsideAnyBox(this.camera.position,obstacles,.05),focusOcclusions:selected?occlusionCount(this.camera.position,this.controls.target,obstacles,this.selectedCode):null,selectedCoverage:coverage,selectedFramed:coverage?visuallyFramed(coverage):null,visibleLabelCount:this.labels.filter(item=>item.sprite.visible).length,labelDiagnostics:this.labelDiagnostics||null};
  }
  dispose(){if(this.closed)return;this.closed=true;cancelAnimationFrame(this.frame);this.observer?.disconnect();this.controls.dispose();
    const c=this.renderer.domElement;c.removeEventListener('pointerdown',this.down);c.removeEventListener('pointerup',this.up);c.removeEventListener('webglcontextlost',this.lost);
    this.resources.dispose();this.shadowLight?.shadow.map?.dispose();this.renderer.forceContextLoss();this.renderer.dispose();c.remove();this.assetObjects.clear();this.collisionObjects.clear();this.clickables=[];
  }
}
