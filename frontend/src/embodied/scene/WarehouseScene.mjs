/** Fixed art direction + procedural geometry; all collision outlines derive from world.v3.json.
 * Units metres. Map (x,y,yaw) -> Three (x-W/2,height,H/2-y), yaw unchanged.
 * Dependencies injected to use the application's pinned Three/OrbitControls.
 */
export class EmbodiedWarehouseScene {
  constructor(host, world, { THREE, OrbitControls, onSelect = () => {}, onFrame = () => {} }) {
    this.T = THREE
    this.host = host
    this.world = world
    this.onSelect = onSelect
    this.onFrame = onFrame
    this.owned = new Set()
    this.materials = new Map()
    this.models = new Map()
    this.pickables = []
    this.drawers = []
    this.disposed = false
    this.dirty = true
    this.labelsVisible = true
    this.frameCount = 0
    const T = THREE
    if (T.ColorManagement) {
      if ('legacyMode' in T.ColorManagement) T.ColorManagement.legacyMode = false
      else T.ColorManagement.enabled = true
    }
    this.scene = new T.Scene()
    this.scene.background = new T.Color('#eef3f5')
    this.renderer = new T.WebGLRenderer({
      antialias: true,
      alpha: false,
      powerPreference: 'high-performance',
    })
    this.renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 1.7))
    this.renderer.shadowMap.enabled = true
    this.renderer.shadowMap.type = T.PCFSoftShadowMap
    if ('outputColorSpace' in this.renderer) this.renderer.outputColorSpace = T.SRGBColorSpace
    else this.renderer.outputEncoding = T.sRGBEncoding
    this.renderer.toneMapping = T.ACESFilmicToneMapping
    this.renderer.toneMappingExposure = 0.93
    this.renderer.domElement.setAttribute('aria-label', 'Three.js 三维仓库，可拖动旋转并选择设备')
    host.appendChild(this.renderer.domElement)
    this.camera = new T.PerspectiveCamera(36, 1, 0.1, 150)
    this.controls = new OrbitControls(this.camera, this.renderer.domElement)
    this.controls.target.set(0, 0.4, 0)
    this.controls.minDistance = 4
    this.controls.maxDistance = 62
    this.controls.maxPolarAngle = Math.PI * 0.46
    this.controls.enableDamping = true
    this.controls.dampingFactor = 0.075
    this.controls.addEventListener('change', () => (this.dirty = true))
    this.unit = this.own(new T.BoxGeometry(1, 1, 1))
    this.cyl = this.own(new T.CylinderGeometry(1, 1, 1, 20))
    this.plane = this.own(new T.PlaneGeometry(1, 1))
    this.sphere = this.own(new T.SphereGeometry(1, 16, 12))
    this.scene.add(new T.HemisphereLight('#f9fdff', '#93a1a7', 1.0))
    const sun = new T.DirectionalLight('#fffaf1', 1.65)
    sun.position.set(-9, 22, 14)
    sun.castShadow = true
    sun.shadow.mapSize.set(2048, 2048)
    sun.shadow.camera.left = -20
    sun.shadow.camera.right = 20
    sun.shadow.camera.top = 20
    sun.shadow.camera.bottom = -20
    sun.shadow.camera.far = 65
    sun.shadow.normalBias = 0.035
    sun.shadow.bias = -0.00015
    this.scene.add(sun)
    const fill = new T.DirectionalLight('#d2eef3', 0.6)
    fill.position.set(18, 9, -10)
    this.scene.add(fill)
    this.environment = new T.Group()
    this.scene.add(this.environment)
    this.assetsGroup = new T.Group()
    this.scene.add(this.assetsGroup)
    this.routeGroup = new T.Group()
    this.scene.add(this.routeGroup)
    this.dynamicGroup = new T.Group()
    this.scene.add(this.dynamicGroup)
    this.spatialGroup = new T.Group()
    this.scene.add(this.spatialGroup)
    this.labelGroup = new T.Group()
    this.scene.add(this.labelGroup)
    this.floor()
    world.assets.forEach((a, i) => this.asset(a, i))
    this.robot = this.makeRobot()
    this.scene.add(this.robot)
    this.setRobot(world.home)
    this.selection = new T.BoxHelper(undefined, '#3c839c')
    this.selection.visible = false
    this.scene.add(this.selection)
    this.owned.add(this.selection.geometry)
    this.owned.add(this.selection.material)
    this.ray = new T.Raycaster()
    this.pointer = new T.Vector2()
    this.down = null
    this.handleDown = (e) => {
      this.down = [e.clientX, e.clientY]
    }
    this.handleUp = (e) => {
      if (!this.down || Math.hypot(e.clientX - this.down[0], e.clientY - this.down[1]) > 5) return
      const r = this.renderer.domElement.getBoundingClientRect()
      this.pointer.set(
        ((e.clientX - r.left) / r.width) * 2 - 1,
        (-(e.clientY - r.top) / r.height) * 2 + 1,
      )
      this.ray.setFromCamera(this.pointer, this.camera)
      const hit = this.ray.intersectObjects(this.pickables, false)[0]
      if (hit) {
        let id = hit.object.userData.assetId
        this.select(id)
        this.onSelect(id)
      }
    }
    this.renderer.domElement.addEventListener('pointerdown', this.handleDown)
    this.renderer.domElement.addEventListener('pointerup', this.handleUp)
    this.resizeObserver = new ResizeObserver(() => this.resize())
    this.resizeObserver.observe(host)
    this.resize()
    this.preset('overview')
    this.loop(performance.now())
  }
  own(o) {
    this.owned.add(o)
    return o
  }
  mat(color, metal = 0.05, rough = 0.72) {
    const key = [color, metal, rough].join()
    if (!this.materials.has(key))
      this.materials.set(
        key,
        this.own(new this.T.MeshStandardMaterial({ color, metalness: metal, roughness: rough })),
      )
    return this.materials.get(key)
  }
  box(g, x, y, z, w, h, d, color, metal = 0.05) {
    const m = new this.T.Mesh(this.unit, this.mat(color, metal))
    m.position.set(x, y, z)
    m.scale.set(w, h, d)
    m.castShadow = true
    m.receiveShadow = true
    g.add(m)
    return m
  }
  cylinder(g, x, y, z, r, h, color, rotation = 0) {
    const m = new this.T.Mesh(this.cyl, this.mat(color, 0.2))
    m.position.set(x, y, z)
    m.scale.set(r, h, r)
    m.rotation.z = rotation
    m.castShadow = true
    m.receiveShadow = true
    g.add(m)
    return m
  }
  map(p, z = 0) {
    return new this.T.Vector3(p.x - this.world.width / 2, z, this.world.height / 2 - p.y)
  }
  text(
    g,
    str,
    x,
    y,
    z,
    width = 2,
    height = 0.35,
    { floor = false, color = '#536973', bg = null } = {},
  ) {
    const T = this.T,
      c = document.createElement('canvas')
    c.width = 1024
    c.height = Math.max(128, Math.round((1024 * height) / width))
    const ctx = c.getContext('2d')
    if (bg) {
      ctx.fillStyle = bg
      ctx.fillRect(0, 0, c.width, c.height)
    }
    ctx.fillStyle = color
    ctx.font = `600 ${Math.floor(c.height * 0.52)}px "Arial", "Microsoft YaHei", sans-serif`
    ctx.textAlign = 'center'
    ctx.textBaseline = 'middle'
    ctx.fillText(str, c.width / 2, c.height / 2, c.width * 0.94)
    const tex = this.own(new T.CanvasTexture(c))
    if ('colorSpace' in tex) tex.colorSpace = T.SRGBColorSpace
    else tex.encoding = T.sRGBEncoding
    const mat = this.own(
      new T.MeshBasicMaterial({
        map: tex,
        transparent: true,
        depthWrite: false,
        side: T.DoubleSide,
      }),
    )
    const m = new T.Mesh(this.plane, mat)
    m.position.set(x, y, z)
    m.scale.set(width, height, 1)
    if (floor) m.rotation.x = -Math.PI / 2
    g.add(m)
    return m
  }
  sprite(str, pos, width = 1.35) {
    const T = this.T,
      c = document.createElement('canvas')
    c.width = 512
    c.height = 144
    const ctx = c.getContext('2d')
    ctx.fillStyle = 'rgba(249,253,254,.96)'
    ctx.beginPath()
    ctx.roundRect(8, 8, 496, 128, 27)
    ctx.fill()
    ctx.strokeStyle = '#b7cdd6'
    ctx.lineWidth = 3
    ctx.stroke()
    ctx.fillStyle = '#395761'
    ctx.font = '600 54px Arial'
    ctx.textAlign = 'center'
    ctx.textBaseline = 'middle'
    ctx.fillText(str, 256, 78, 460)
    const tex = this.own(new T.CanvasTexture(c))
    if ('colorSpace' in tex) tex.colorSpace = T.SRGBColorSpace
    else tex.encoding = T.sRGBEncoding
    const mat = this.own(new T.SpriteMaterial({ map: tex, depthTest: true, depthWrite: false }))
    const sp = new T.Sprite(mat)
    sp.position.copy(pos)
    sp.scale.set(width, width * 0.281, 1)
    this.labelGroup.add(sp)
    return sp
  }
  floor() {
    const T = this.T,
      W = this.world.width,
      H = this.world.height,
      g = this.environment
    this.box(g, 0, -0.23, 0, W + 0.5, 0.4, H + 0.5, '#d5dfe4')
    this.box(g, 0, -0.014, 0, W, 0.026, H, '#e7edee')
    for (let x = 0; x <= W; x += 2) this.box(g, x - W / 2, 0.002, 0, 0.012, 0.003, H, '#d8e2e5')
    for (let y = 0; y <= H; y += 2) this.box(g, 0, 0.002, H / 2 - y, W, 0.003, 0.012, '#d8e2e5')
    for (const z of this.world.zones) {
      this.box(g, z.x - W / 2, 0.007, H / 2 - z.y, z.width, 0.006, z.depth, z.color)
      if (z.kind === 'keepout') {
        for (let x = -z.width / 2 + 0.2; x < z.width / 2; x += 0.35) {
          const m = this.box(
            g,
            z.x - W / 2 + x,
            0.018,
            H / 2 - z.y,
            0.095,
            0.008,
            z.depth * 0.85,
            '#cba992',
          )
          m.rotation.y = -0.3
        }
      }
    }
    // Cut-away wall heights retain spatial context without hiding warehouse equipment.
    this.box(g, -W / 2 - 0.08, 0.72, 0, 0.16, 1.44, H, '#d3e0e6')
    this.box(g, 0, 0.72, -H / 2 - 0.08, W, 1.44, 0.16, '#dce5e9')
    this.box(g, -W / 2 + 0.015, 1.4, 0, 0.045, 0.035, H, '#fcffff')
    this.box(g, 0, 1.4, -H / 2 + 0.015, W, 0.035, 0.045, '#fcffff')
    for (let x = -10; x < 10; x += 4.8) {
      this.box(g, x, 1.05, -H / 2 + 0.02, 2.4, 0.36, 0.025, '#c5dfe9')
      this.box(g, x, 1.05, -H / 2 + 0.045, 0.025, 0.36, 0.02, '#f5fafb')
    }
    this.text(
      g,
      'M A T E R I A L B R A I N   /   L O G I S T I C S   L A B',
      0,
      0.025,
      H / 2 - 0.43,
      13,
      0.29,
      { floor: true, color: '#6d848e' },
    )
    this.text(g, '01  ELECTRONICS', -10, 0.024, -5, 3, 0.4, { floor: true, color: '#7793a1' })
    this.text(g, '02  STORAGE', -2, 0.024, -0.1, 3.8, 0.48, { floor: true, color: '#819997' })
    this.text(g, '03  INBOUND', 8, 0.023, 4.6, 3.6, 0.5, { floor: true, color: '#97866c' })
    this.text(g, '04  HANDOFF', 0, 0.024, 4.7, 3.2, 0.4, { floor: true, color: '#71958f' })
    this.text(g, 'AMR • DOCK', 9.5, 0.03, -6.3, 2, 0.3, { floor: true, color: '#548d8f' })
    // Raised white safety line around receiving; does not create navigational obstacles.
    for (const [x, z, w, d] of [
      [4.8, 1.5, 0.025, 6.0],
      [11.4, 1.5, 0.025, 6.0],
      [8.1, 4.5, 6.6, 0.025],
    ])
      this.box(g, x, 0.02, z, w, 0.014, d, '#fffdfa')
    const goals = this.world.goals
    for (const goal of goals) {
      const p = this.map(goal.pose, 0.025)
      const ring = new T.Mesh(
        this.own(new T.RingGeometry(0.29, 0.32, 32)),
        this.own(
          new T.MeshBasicMaterial({
            color: '#91bfc7',
            transparent: true,
            opacity: 0.8,
            side: T.DoubleSide,
          }),
        ),
      )
      ring.rotation.x = -Math.PI / 2
      ring.position.copy(p)
      g.add(ring)
    }
  }
  asset(a, index, { dynamic = false } = {}) {
    const T = this.T,
      g = new T.Group()
    g.position.copy(this.map(a))
    g.rotation.y = ((a.yaw_deg || 0) * Math.PI) / 180
    g.userData.assetId = a.id
    ;(dynamic ? this.dynamicGroup : this.assetsGroup).add(g)
    if (!dynamic) this.models.set(a.id, g)
    const w = a.width,
      d = a.depth,
      h = a.height
    const frame = '#506675',
      light = '#c0d4de',
      steel = '#99afba',
      blue = '#9bc4d8',
      mint = '#b0d5cc',
      dark = '#354753',
      white = '#edf4f6',
      wood = '#c2a780',
      carton = '#c9b393'
    const b = (x, y, z, ww, hh, dd, c, m) => this.box(g, x, y, z, ww, hh, dd, c, m)
    const plate = (title, yy, ww = w * 0.83) =>
      this.text(g, title, 0, yy, d / 2 + 0.008, ww, 0.13, { bg: '#eef4f6', color: '#426176' })
    const legs = (yy, top, depth = d) => {
      for (const x of [-w / 2 + 0.055, w / 2 - 0.055])
        for (const z of [-depth / 2 + 0.055, depth / 2 - 0.055])
          b(x, yy, z, 0.06, top, 0.06, frame, 0.45)
    }
    const boxes = (yy, level = 0, amount = 3) => {
      const gap = 0.09,
        bw = (w - 0.22 - gap * (amount - 1)) / amount
      for (let k = 0; k < amount; k++) {
        const x = -w / 2 + 0.11 + bw / 2 + k * (bw + gap),
          seed = index * 31 + level * 7 + k,
          cc = [carton, blue, mint, '#d9d9ce'][seed % 4]
        if (seed % 7 === 0 && level > 1) continue
        const hh = Math.min(0.25 + 0.07 * (seed % 3), h * 0.12)
        b(x, yy + hh / 2 + 0.025, 0.015, bw, hh, d * 0.76, cc)
        b(x, yy + hh * 0.65, d * 0.385, bw * 0.39, 0.095, 0.01, white)
        if (seed % 4 === 0) {
          b(x, yy + hh / 2, 0.012, 0.03, hh + 0.004, d * 0.765, '#9daaa6')
        } else {
          b(x, yy + hh * 0.75, d * 0.39, bw * 0.27, 0.014, 0.014, '#7896a1')
        }
      }
    }
    if (a.kind === 'drawer100') {
      b(0, h / 2, 0, w, h, d, dark, 0.28)
      b(0, h / 2, d / 2 + 0.002, w - 0.1, h - 0.14, 0.018, '#283a46')
      const cols = 5,
        rows = 20,
        cw = (w - 0.16) / cols,
        ch = (h - 0.25) / rows,
        geom = this.unit,
        mats = [light, blue, '#dde8e9'].map((c) => this.mat(c, 0.05))
      for (let c = 0; c < 3; c++) {
        const points = []
        for (let row = 0; row < rows; row++)
          for (let col = 0; col < cols; col++)
            if ((row * 3 + col + index) % 3 === c) points.push({ row, col })
        const mesh = new T.InstancedMesh(geom, mats[c], points.length)
        const obj = new T.Object3D()
        points.forEach(({ row, col }, i) => {
          obj.position.set(
            -w / 2 + 0.08 + cw * (col + 0.5),
            h - 0.14 - ch * (row + 0.5),
            d / 2 + 0.018,
          )
          obj.scale.set(cw - 0.014, ch - 0.011, 0.047)
          obj.updateMatrix()
          mesh.setMatrixAt(i, obj.matrix)
        })
        mesh.castShadow = true
        mesh.receiveShadow = true
        mesh.userData.assetId = a.id
        g.add(mesh)
        this.pickables.push(mesh)
      }
      // Label strips and handles are instanced, keeping 100 drawers visible without 1000 draw calls.
      const handles = new T.InstancedMesh(geom, this.mat('#748e9c', 0.4), 100),
        obj = new T.Object3D()
      for (let i = 0; i < 100; i++) {
        const row = Math.floor(i / 5),
          col = i % 5
        obj.position.set(
          -w / 2 + 0.08 + cw * (col + 0.5),
          h - 0.14 - ch * (row + 0.3),
          d / 2 + 0.048,
        )
        obj.scale.set(cw * 0.36, 0.012, 0.015)
        obj.updateMatrix()
        handles.setMatrixAt(i, obj.matrix)
      }
      g.add(handles)
      plate(`${a.id}  /  100 DRAWERS`, h - 0.07)
    } else if (['organizer56', 'organizerMix'].includes(a.kind)) {
      legs(h * 0.43, h * 0.84)
      b(0, h * 0.83, 0, w, 0.08, d, wood)
      b(0, h * 0.2, 0, w - 0.1, 0.04, d - 0.12, light)
      const count = a.kind === 'organizerMix' ? 2 : 1
      for (let k = 0; k < count; k++) {
        const bw = (w - 0.18) / count - 0.06,
          cx = (k - (count - 1) / 2) * (bw + 0.06)
        b(cx, h * 0.93, 0.005, bw, 0.13, d * 0.79, frame)
        const rows = 7,
          cols = a.kind === 'organizerMix' ? 4 : 8,
          cw = (bw - 0.05) / cols,
          cd = (d * 0.79 - 0.07) / rows
        for (let r = 0; r < rows; r++)
          for (let c = 0; c < cols; c++)
            b(
              cx - bw / 2 + 0.025 + cw * (c + 0.5),
              h * 0.93 + 0.045,
              -d * 0.395 + 0.035 + cd * (r + 0.5),
              cw - 0.012,
              0.052,
              cd - 0.012,
              (r + c) % 6 === 0 ? mint : '#d9e5e9',
            )
        const lid = new T.Group()
        lid.position.set(cx, h * 0.95, -d * 0.4)
        lid.rotation.x = -0.52
        g.add(lid)
        this.box(lid, 0, 0.005, -d * 0.14, bw, 0.018, d * 0.28, '#b9d5dc')
      }
      plate(`${a.id} / 56 BINS`, h * 0.82)
    } else if (['shelf6', 'flowrack'].includes(a.kind)) {
      legs(h / 2, h)
      b(0, h / 2, -d / 2 + 0.04, w, 0.03, 0.035, frame)
      const levels = a.kind === 'shelf6' ? 6 : 4
      for (let l = 0; l < levels; l++) {
        const yy = 0.1 + (l * (h - 0.2)) / levels
        b(0, yy, 0, w, 0.046, d, steel, 0.35)
        b(0, yy + 0.03, d / 2, w, 0.085, 0.06, l % 2 ? blue : light)
        boxes(yy, l, Math.max(2, Math.round(w / 0.72)))
      }
      const brace = (x) => {
        const m = b(x, h * 0.5, 0, 0.025, Math.hypot(h * 0.7, d * 0.8), 0.025, '#a4b4ba')
        m.rotation.x = Math.atan2(d * 0.8, h * 0.7)
      }
      brace(w / 2 - 0.03)
      brace(-w / 2 + 0.03)
      plate(`${a.id} / ${levels} LEVELS`, h - 0.08)
    } else if (a.kind === 'pallet') {
      for (let z of [-d * 0.35, 0, d * 0.35]) b(0, 0.065, z, w, 0.13, d * 0.14, wood)
      for (let x = -w * 0.43; x < w * 0.5; x += w / 6) b(x, 0.16, 0, w * 0.115, 0.07, d, '#d0b895')
      const bw = (w - 0.1) / 2,
        bd = (d - 0.1) / 2
      for (let layer = 0; layer < Math.floor((h - 0.2) / 0.35); layer++)
        for (let c = 0; c < 2; c++)
          for (let r = 0; r < 2; r++) {
            if (layer === 2 && c === 1 && index % 2) continue
            const x = (c - 0.5) * bw,
              z = (r - 0.5) * bd
            b(
              x,
              0.22 + 0.17 + layer * 0.34,
              z,
              bw - 0.025,
              0.32,
              bd - 0.025,
              index % 2 ? carton : '#c1b59f',
            )
            b(x, 0.22 + 0.17 + layer * 0.34, z, 0.04, 0.323, bd - 0.02, '#ddd0b8')
          }
      plate('INBOUND', h * 0.6, w * 0.4)
    } else if (['cart', 'totes'].includes(a.kind)) {
      b(0, 0.18, 0, w, 0.1, d, frame, 0.4)
      if (a.kind === 'cart') {
        for (const x of [-w * 0.4, w * 0.4])
          for (const z of [-d * 0.34, d * 0.34]) {
            this.cylinder(g, x, 0.11, z, 0.105, 0.055, '#34434c', Math.PI / 2)
          }
        for (const z of [-d * 0.4, d * 0.4])
          b(-w * 0.45, h * 0.62, z, 0.038, h * 0.65, 0.038, steel)
        b(-w * 0.45, h * 0.94, 0, 0.045, 0.04, d * 0.82, frame)
      }
      const levels = a.kind === 'totes' ? 4 : 2
      for (let l = 0; l < levels; l++) {
        b(0, 0.27 + l * 0.25, 0, w * 0.78, 0.23, d * 0.79, l % 2 ? mint : blue)
        b(0, 0.38 + l * 0.25, d * 0.405, w * 0.65, 0.025, 0.025, frame)
        b(0, 0.31 + l * 0.25, d * 0.404, w * 0.23, 0.055, 0.012, white)
      }
    } else if (a.kind === 'workbench') {
      legs(0.43, 0.86)
      b(0, 0.88, 0, w, 0.095, d, wood)
      b(-w * 0.3, 0.49, 0, w * 0.26, 0.72, d * 0.8, light)
      for (let j = 0; j < 4; j++)
        b(-w * 0.3, 0.24 + j * 0.14, d * 0.405, w * 0.19, 0.018, 0.018, steel)
      b(0, 1.23, -d * 0.4, w * 0.9, 0.59, 0.045, '#b5c6cd')
      for (let x = -w * 0.35; x < w * 0.4; x += 0.13)
        for (let y = 1.03; y < 1.5; y += 0.1) b(x, y, -d * 0.37, 0.016, 0.016, 0.009, '#7c929e')
      b(w * 0.22, 1.12, -0.02, 0.47, 0.3, 0.06, frame)
      b(w * 0.22, 1.12, 0.014, 0.42, 0.245, 0.012, '#afd9e0')
      b(w * 0.22, 0.975, 0.02, 0.06, 0.16, 0.07, frame)
      b(-w * 0.2, 0.99, 0.05, 0.5, 0.1, 0.3, mint)
      plate(`${a.id} / WORKSTATION`, 0.87)
    } else if (a.kind === 'cage') {
      legs(h / 2, h)
      b(0, 0.06, 0, w, 0.1, d, steel)
      for (let yy = 0.25; yy < h; yy += 0.2)
        for (const z of [-d / 2, d / 2]) b(0, yy, z, w, 0.014, 0.014, '#94a9b2')
      for (let x = -w * 0.45; x < w * 0.49; x += 0.18)
        for (const z of [-d / 2, d / 2]) b(x, h / 2, z, 0.012, h, 0.012, '#9aadb4')
      boxes(0.1, 0, 3)
      plate('QC / HOLD', h * 0.65, w * 0.5)
    } else if (a.kind === 'charger') {
      b(0, h / 2, 0, w, h, d, frame)
      b(0, h * 0.65, d * 0.51, w * 0.6, h * 0.32, 0.03, '#d9ede9')
      b(0, h * 0.65, d * 0.535, w * 0.24, 0.018, 0.02, '#639da0')
      b(0, 0.1, d * 0.15, w * 0.9, 0.06, d * 0.9, '#9fbbc4')
      plate('MB • DOCK', h * 0.95)
    } else if (a.kind === 'toolchest') {
      b(0, h * 0.53, 0, w, h * 0.87, d, blue)
      for (let i = 0; i < 6; i++) {
        b(0, 0.23 + i * 0.115, d * 0.505, w * 0.88, 0.012, 0.01, '#526f7b')
        b(0, 0.25 + i * 0.115, d * 0.53, w * 0.6, 0.019, 0.025, '#c4d4dc')
      }
      b(0, h * 0.99, 0, w + 0.01, 0.035, d + 0.01, wood)
    } else if (a.kind === 'column') {
      b(0, h / 2, 0, w, h, d, '#bfcdd4')
      b(0, 0.12, 0, w + 0.07, 0.24, d + 0.07, '#b6a688')
      b(0, h - 0.15, 0, w + 0.03, 0.1, d + 0.03, white)
    } else if (a.kind === 'terminal') {
      b(0, 0.05, 0, w, 0.1, d, frame)
      b(0, 0.65, 0, 0.09, 1.25, 0.09, steel)
      const m = b(0, 1.25, 0, w * 0.94, 0.38, 0.09, frame)
      m.rotation.x = -0.12
      b(0, 1.25, 0.055, w * 0.85, 0.29, 0.01, blue)
    } else {
      b(0, h / 2, 0, w, h, d, '#c9aa8e')
    }
    this.batchAsset(g)
    g.traverse((o) => {
      if (o.isMesh && !o.isInstancedMesh && o.geometry === this.unit) {
        o.userData.assetId = a.id
        this.pickables.push(o)
      }
    })
    if (!dynamic && !['column'].includes(a.kind)) {
      const sp = this.sprite(a.id, this.map(a, h + 0.24), 0.82)
      sp.userData.assetId = a.id
    }
    return g
  }
  batchAsset(g) {
    // Collapse repeated static boxes/cylinders per asset; preserve asset-id ray picking.
    const T = this.T,
      groups = new Map()
    g.updateMatrixWorld(true)
    const inv = g.matrixWorld.clone().invert()
    g.traverse((o) => {
      if (o.isMesh && !o.isInstancedMesh && (o.geometry === this.unit || o.geometry === this.cyl)) {
        const key = o.geometry.uuid + o.material.uuid
        if (!groups.has(key)) groups.set(key, [])
        groups.get(key).push(o)
      }
    })
    for (const objects of groups.values()) {
      if (objects.length < 3) continue
      const im = new T.InstancedMesh(objects[0].geometry, objects[0].material, objects.length)
      objects.forEach((o, i) => {
        im.setMatrixAt(i, new T.Matrix4().multiplyMatrices(inv, o.matrixWorld))
        o.parent.remove(o)
      })
      im.castShadow = true
      im.receiveShadow = true
      im.userData.assetId = g.userData.assetId
      g.add(im)
      this.pickables.push(im)
    }
  }
  makeRobot() {
    const T = this.T,
      g = new T.Group()
    this.box(g, 0, 0.25, 0, 0.76, 0.3, 0.58, '#dde8ec', 0.3)
    this.box(g, 0, 0.1, 0, 0.71, 0.12, 0.53, '#344750')
    this.box(g, 0, 0.425, 0, 0.65, 0.045, 0.49, '#8dbdcd')
    this.box(g, 0, 0.535, 0, 0.48, 0.19, 0.37, '#bad5d9')
    this.box(g, 0, 0.64, 0, 0.52, 0.026, 0.41, '#819fae')
    for (const z of [-0.29, 0.29])
      this.cylinder(g, 0, 0.16, z, 0.16, 0.06, '#263a45').rotation.x = Math.PI / 2
    this.cylinder(g, 0.23, 0.46, 0, 0.105, 0.06, '#4b6573')
    this.box(g, 0.374, 0.27, 0, 0.012, 0.052, 0.4, '#8fddd7')
    this.text(g, 'M', 0, 0.665, 0, 0.28, 0.18, { floor: true, color: '#486f7c' })
    return g
  }
  setRobot(p) {
    const q = this.map(p)
    if (
      q.distanceToSquared(this.robot.position) > 1e-12 ||
      Math.abs(this.robot.rotation.y - p.yaw) > 1e-8
    ) {
      if (this.cameraMode === 'robot') {
        const delta = q.clone().sub(this.robot.position)
        this.camera.position.add(delta)
        this.controls.target.add(delta)
      }
      this.robot.position.copy(q)
      this.robot.rotation.y = p.yaw
      this.dirty = true
    }
  }
  setPlan(plan, { baseline = null } = {}) {
    this.clearGroup(this.routeGroup)
    if (!plan?.segments) return
    const T = this.T
    const draw = (p, color, width, offset) => {
      const pts = []
      for (const s of p.segments)
        for (const q of s.poses) {
          if (!pts.length || Math.hypot(q.x - pts.at(-1).x, q.y - pts.at(-1).y) > 0.0001)
            pts.push(q)
        }
      const count = Math.max(0, pts.length - 1)
      if (!count) return
      const im = new T.InstancedMesh(this.cyl, this.mat(color, 0.05), count),
        ob = new T.Object3D()
      for (let i = 1; i < pts.length; i++) {
        const a = this.map(pts[i - 1], offset),
          b = this.map(pts[i], offset),
          v = b.clone().sub(a)
        ob.position.copy(a).add(b).multiplyScalar(0.5)
        ob.scale.set(width, v.length(), width)
        ob.quaternion.setFromUnitVectors(new T.Vector3(0, 1, 0), v.normalize())
        ob.updateMatrix()
        im.setMatrixAt(i - 1, ob.matrix)
      }
      this.routeGroup.add(im)
    }
    if (baseline) draw(baseline, '#b6bfc6', 0.02, 0.035)
    draw(plan, '#3c9aad', 0.039, 0.06)
    plan.goal_ids.forEach((id, i) => {
      const goal = this.world.goals.find((g) => g.id === id)
      if (goal) {
        const pos = this.map(goal.pose, 0.085)
        const ring = new T.Mesh(
          this.own(new T.RingGeometry(0.32, 0.39, 32)),
          this.own(new T.MeshBasicMaterial({ color: '#337f99', side: T.DoubleSide })),
        )
        ring.rotation.x = -Math.PI / 2
        ring.position.copy(pos)
        this.routeGroup.add(ring)
        this.text(this.routeGroup, String(i + 1), pos.x, 0.089, pos.z, 0.33, 0.33, {
          floor: true,
          color: '#236276',
        })
      }
    })
    this.dirty = true
  }
  setDynamic(obstacles) {
    const removed = new Set()
    this.dynamicGroup.traverse((o) => removed.add(o))
    this.pickables = this.pickables.filter((o) => !removed.has(o))
    this.clearGroup(this.dynamicGroup)
    for (const o of obstacles) this.asset(o, 1, { dynamic: true })
    this.dirty = true
  }
  setSpatialLayers(snapshot, layers = [], mission = null, execution = null) {
    this.clearGroup(this.spatialGroup)
    if (!mission && this.spatialPlanApplied) {
      this.setPlan(null)
      this.spatialPlanApplied = false
    }
    const T = this.T
    const active = new Set(layers)
    const colours = {
      esd: '#78bcae',
      human_mixed: '#d5b777',
      slow: '#d5b777',
      keepout: '#c78c80',
      charging: '#75bba5',
      handoff: '#7db9c8',
      receiving: '#a1c1cb',
      area: '#9abfc4',
    }
    const polygon = (geometry, colour) => {
      if (geometry?.type !== 'Polygon' || !geometry.coordinates?.[0]?.length) return
      const shape = new T.Shape()
      geometry.coordinates[0].forEach(([x, y], index) => {
        const point = [x - this.world.width / 2, y - this.world.height / 2]
        if (index === 0) shape.moveTo(...point)
        else shape.lineTo(...point)
      })
      const mesh = new T.Mesh(
        this.own(new T.ShapeGeometry(shape)),
        this.own(
          new T.MeshBasicMaterial({
            color: colour,
            transparent: true,
            opacity: 0.16,
            depthWrite: false,
            side: T.DoubleSide,
          }),
        ),
      )
      mesh.rotation.x = -Math.PI / 2
      mesh.position.y = 0.026
      this.spatialGroup.add(mesh)
    }
    if (active.has('semantics'))
      for (const zone of snapshot?.zones || [])
        polygon(zone.polygon, colours[zone.kind] || '#9abfc4')
    if (active.has('dynamic'))
      for (const overlay of snapshot?.dynamic_overlays || []) {
        if (!overlay.expires_at || Date.parse(overlay.expires_at) > Date.now())
          polygon(overlay.geometry, '#c78c80')
      }
    if (active.has('rules') || active.has('cost')) {
      const nodes = new Map((snapshot?.route_graph?.nodes || []).map((node) => [node.id, node]))
      const buckets = new Map()
      for (const edge of snapshot?.route_graph?.edges || []) {
        const from = nodes.get(edge.from),
          to = nodes.get(edge.to)
        if (!from || !to) continue
        const colour = active.has('cost') && edge.risk_level > 0.4 ? '#c7aa78' : '#83b4be'
        if (!buckets.has(colour)) buckets.set(colour, [])
        buckets.get(colour).push(this.map(from, 0.032), this.map(to, 0.032))
      }
      for (const [colour, points] of buckets) {
        const geometry = this.own(new T.BufferGeometry().setFromPoints(points))
        const material = this.own(
          new T.LineBasicMaterial({ color: colour, transparent: true, opacity: 0.22 }),
        )
        this.spatialGroup.add(new T.LineSegments(geometry, material))
      }
    }
    if (mission) {
      this.spatialPlanApplied = true
      this.setPlan(
        active.has('trajectory')
          ? { segments: mission.segments || [], goal_ids: mission.ordered_goal_ids || [] }
          : null,
      )
    }
    if (execution?.current_pose) this.setRobot(execution.current_pose)
    this.spatialLayers = [...active]
    this.dirty = true
  }
  setCostmap(planner, visible) {
    if (this.costmap) {
      this.costmap.material.dispose()
      this.owned.delete(this.costmap.material)
      this.costmap.dispose?.()
      this.scene.remove(this.costmap)
      this.costmap = null
    }
    if (!visible) {
      this.dirty = true
      return
    }
    const T = this.T,
      cells = []
    for (let i = 0; i < planner.n; i++) if (planner.clear[i] < planner.r + 0.4) cells.push(i)
    const m = new T.InstancedMesh(
        this.unit,
        this.own(new T.MeshBasicMaterial({ transparent: true, opacity: 0.38, depthWrite: false })),
        cells.length,
      ),
      ob = new T.Object3D(),
      col = new T.Color()
    cells.forEach((idx, k) => {
      const p = planner.point(idx),
        pos = this.map(p, 0.019)
      ob.position.copy(pos)
      ob.scale.set(planner.res * 0.94, 0.01, planner.res * 0.94)
      ob.updateMatrix()
      m.setMatrixAt(k, ob.matrix)
      col.set(planner.clear[idx] <= planner.r ? '#cc8f7f' : '#d7b16f')
      m.setColorAt(k, col)
    })
    this.scene.add(m)
    this.costmap = m
    this.dirty = true
  }
  select(id, { focus = false } = {}) {
    if (focus) this.cameraMode = 'focus'
    const model = this.models.get(id)
    if (!model) return
    this.selected = id
    const a0 = this.world.assets.find((a) => a.id === id),
      ang = ((a0.yaw_deg || 0) * Math.PI) / 180,
      bw = Math.abs(Math.cos(ang)) * a0.width + Math.abs(Math.sin(ang)) * a0.depth,
      bd = Math.abs(Math.sin(ang)) * a0.width + Math.abs(Math.cos(ang)) * a0.depth
    const temp = new this.T.Mesh(this.unit, this.mat('#ffffff'))
    temp.position.copy(this.map(a0, a0.height / 2))
    temp.scale.set(bw + 0.06, a0.height + 0.05, bd + 0.06)
    temp.updateMatrixWorld()
    this.selection.setFromObject(temp)
    this.selection.visible = true
    if (focus) {
      const a = this.world.assets.find((a) => a.id === id),
        p = this.map(a, 0.7)
      this.controls.target.copy(p)
      const yaw = ((a.yaw_deg || 0) * Math.PI) / 180
      this.camera.position
        .copy(p)
        .add(new this.T.Vector3(Math.sin(yaw) * 5 + 2, 4.3, Math.cos(yaw) * 5 + 2))
      this.controls.update()
    }
    this.dirty = true
  }
  preset(mode) {
    this.cameraMode = mode
    const T = this.T
    if (mode === 'robot') {
      const target = this.robot.position.clone()
      this.controls.target.copy(target).add(new T.Vector3(0, 0.3, 0))
      this.camera.position.copy(target).add(new T.Vector3(3.8, 3.7, 4.8))
    } else {
      this.controls.target.set(0, 0.3, 0)
      if (mode === 'top')
        this.camera.position.set(0, Math.max(28, 32 / Math.min(this.camera.aspect, 1.4)), 0.02)
      else if (mode === 'aisle') this.camera.position.set(15, 9, 18)
      else {
        let lo = 12,
          hi = 90
        const direction = new T.Vector3(0.46, 0.77, 0.78).normalize()
        const corners = []
        for (const x of [-this.world.width / 2 - 0.4, this.world.width / 2 + 0.4])
          for (const z of [-this.world.height / 2 - 0.4, this.world.height / 2 + 0.4])
            for (const y of [0, 3.35]) corners.push(new T.Vector3(x, y, z))
        for (let k = 0; k < 18; k++) {
          const d = (lo + hi) / 2
          this.camera.position.copy(this.controls.target).addScaledVector(direction, d)
          this.camera.lookAt(this.controls.target)
          this.camera.updateMatrixWorld()
          const fits = corners.every((c) => {
            const p = c.clone().project(this.camera)
            return Math.abs(p.x) < 0.96 && Math.abs(p.y) < 0.91
          })
          if (fits) hi = d
          else lo = d
        }
        this.camera.position.copy(this.controls.target).addScaledVector(direction, hi)
      }
    }
    this.controls.update()
    this.dirty = true
  }
  zoom(factor) {
    const d = this.camera.position.clone().sub(this.controls.target)
    d.setLength(
      Math.max(this.controls.minDistance, Math.min(this.controls.maxDistance, d.length() * factor)),
    )
    this.camera.position.copy(this.controls.target).add(d)
    this.controls.update()
    this.dirty = true
  }
  resize() {
    const w = this.host.clientWidth,
      h = this.host.clientHeight
    if (!w || !h) return
    this.renderer.setSize(w, h, false)
    this.camera.aspect = w / h
    this.camera.updateProjectionMatrix()
    this.dirty = true
  }
  loop(now) {
    if (this.disposed) return
    const dt = Math.max(0, Math.min(0.1, (now - (this.lastFrame || now)) / 1000))
    this.lastFrame = now
    this.onFrame(dt)
    this.controls.update()
    if (this.dirty) {
      this.renderer.render(this.scene, this.camera)
      this.dirty = false
      this.frameCount++
    }
    this.raf = requestAnimationFrame((t) => this.loop(t))
  }
  clearGroup(g) {
    const protectedGeometries = new Set([this.unit, this.cyl, this.plane, this.sphere])
    const protectedMaterials = new Set(this.materials.values())
    const released = new Set()
    const release = (r) => {
      if (r && !released.has(r)) {
        r.dispose?.()
        this.owned.delete(r)
        released.add(r)
      }
    }
    g.traverse((o) => {
      if (o.geometry && !protectedGeometries.has(o.geometry)) release(o.geometry)
      if (o.material && !protectedMaterials.has(o.material)) {
        if (o.material.map) release(o.material.map)
        release(o.material)
      }
      if (o.isInstancedMesh) o.dispose?.()
    })
    g.clear()
  }
  diagnostics() {
    return {
      three_revision: this.T.REVISION,
      renderer: 'THREE.WebGLRenderer',
      frames: this.frameCount,
      assets: this.models.size,
      geometries: this.renderer.info.memory.geometries,
      textures: this.renderer.info.memory.textures,
      draw_calls: this.renderer.info.render.calls,
      triangles: this.renderer.info.render.triangles,
      camera: this.camera.position.toArray(),
      selected: this.selected || null,
      spatial_layers: this.spatialLayers || [],
    }
  }
  dispose() {
    this.disposed = true
    cancelAnimationFrame(this.raf)
    this.resizeObserver.disconnect()
    this.controls.dispose()
    this.renderer.domElement.removeEventListener('pointerdown', this.handleDown)
    this.renderer.domElement.removeEventListener('pointerup', this.handleUp)
    for (const r of this.owned) r.dispose?.()
    this.owned.clear()
    this.renderer.dispose()
    this.renderer.forceContextLoss()
    this.renderer.domElement.remove()
    this.models.clear()
    this.pickables = []
  }
}
