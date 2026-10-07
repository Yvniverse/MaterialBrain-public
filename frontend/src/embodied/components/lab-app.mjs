import { MetricPlanner } from '../core/planner.mjs'
import { MissionRunner } from '../core/mission.mjs'
import { EmbodiedWarehouseScene } from '../scene/WarehouseScene.mjs'
const iconPaths = {
  cube: 'M12 3 3 8v9l9 5 9-5V8L12 3Zm0 10 9-5M12 13 3 8m9 5v9',
  home: 'm3 10 9-7 9 7M5 9v12h14V9M9 21v-7h6v7',
  brain:
    'M9 3a3 3 0 0 0-5 2 4 4 0 0 0-1 7 4 4 0 0 0 2 7 3 3 0 0 0 6 0V5a2 2 0 0 0-2-2Zm6 0a3 3 0 0 1 5 2 4 4 0 0 1 1 7 4 4 0 0 1-2 7 3 3 0 0 1-6 0V5a2 2 0 0 1 2-2ZM7 9h4m2 6h4',
  route:
    'M5 3a2 2 0 1 0 0 4 2 2 0 0 0 0-4Zm14 14a2 2 0 1 0 0 4 2 2 0 0 0 0-4ZM5 7v8a3 3 0 0 0 3 3h3a3 3 0 0 0 0-6h2a3 3 0 0 0 3-3V3',
  shelf: 'M4 3v18M20 3v18M4 7h16M4 13h16M4 19h16M7 4h3v3m3 2h4v4m-8 2h6v4',
  chip: 'M7 7h10v10H7zM3 8h4m-4 4h4m-4 4h4m10-8h4m-4 4h4m-4 4h4M8 3v4m4-4v4m4-4v4M8 17v4m4-4v4m4-4v4',
  list: 'M9 6h12M9 12h12M9 18h12M3 6h1m-1 6h1m-1 6h1',
  graph: 'M3 3v18h18M6 15l5-5 4 3 6-8',
  search: 'M10 3a7 7 0 1 0 0 14 7 7 0 0 0 0-14Zm5 12 6 6',
  robot: 'M5 7h14v13H5zM12 7V3m-2 0h4M8 11v2m8-2v2m-8 4h8M2 10v6m20-6v6',
  play: 'm8 4 12 8-12 8V4Z',
  pause: 'M7 4v16M17 4v16',
  plus: 'M12 4v16M4 12h16',
  minus: 'M4 12h16',
  expand: 'M8 3H3v5m13-5h5v5M3 16v5h5m8 0h5v-5',
  close: 'm5 5 14 14M5 19 19 5',
  arrow: 'M4 12h16m-6-6 6 6-6 6',
  refresh: 'M20 7v5h-5M4 17v-5h5M5 7a8 8 0 0 1 14-1M19 17A8 8 0 0 1 5 18',
  download: 'M12 3v12m-5-5 5 5 5-5M4 17v4h16v-4',
  grid: 'M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z',
  check: 'm5 12 4 4L19 6',
  alert: 'm12 3 10 18H2L12 3Zm0 6v5m0 3v1',
  bell: 'M6 9a6 6 0 0 1 12 0v7l2 2H4l2-2V9Zm4 12h4',
  link: 'm8 16 8-8M9 6l2-2a5 5 0 0 1 7 7l-2 2M8 11l-2 2a5 5 0 0 0 7 7l2-2',
}
const ic = (key) =>
  `<svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><path d="${iconPaths[key] || iconPaths.cube}"/></svg>`
const esc = (v) =>
  String(v ?? '').replace(
    /[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c],
  )
const fmt = (v, d = 1) => (Number.isFinite(v) ? v.toFixed(d) : '—')
const stateNames = {
  IDLE: '待规划',
  READY: '路线就绪',
  RUNNING: '执行中',
  PAUSED: '已暂停',
  WAITING_HANDOFF: '等待扫码交接',
  REPLANNING: '重新规划中',
  BLOCKED: '任务受阻',
  COMPLETED: '任务完成',
  CANCELLED: '已取消',
  NEEDS_CHARGE: '需要先充电',
  SPLIT_REQUIRED: '需要分批',
  CAPABILITY_MISMATCH: '能力不匹配',
}
export function mountLabApp(
  root,
  world,
  {
    THREE,
    OrbitControls,
    botUrl,
    showNavigation = true,
    showBot = true,
    onNavigate = () => {},
    onOpenLocation = () => {},
    planningClient = null,
    initialGoalIds = null,
    initialScenario = 'baseline',
    initialExecution = null,
  } = {},
) {
  root.classList.add('mb-lab')
  let selectedGoals = [...(initialExecution?.goal_ids ?? initialGoalIds ?? world.default_goal_ids)],
    selectedAsset = 'A01',
    scenario = initialScenario,
    planner = null,
    plan = null,
    scene = null,
    externalExecution = null,
    costVisible = false,
    busy = false,
    speed = 4,
    planSequence = 0,
    disposed = false,
    botOpen = false,
    toastTimer,
    chat = [],
    taskDirty = false,
    showAllGoals = false
  const cleanups = []
  const navItems = [
    ['home', 'home', '工作概览'],
    ['brain', 'brain', '物料大脑'],
    ['twin', 'cube', '数字孪生仓库'],
    ['locations', 'shelf', '可视化库位'],
    ['materials', 'chip', '物料库'],
    ['projects', 'list', '项目与 BOM'],
    ['inventory', 'graph', '库存与流转'],
  ]
  root.innerHTML = `${showNavigation ? `<aside class="side"><div class="brand"><div class="brand-mark">${ic('cube')}</div><span class="brand-name">MaterialBrain<span style="color:#90b5c4">.</span></span></div><div class="nav-caption">WORKSPACE</div>${navItems.map(([id, ico, label]) => `<button class="nav-btn ${id === 'twin' ? 'active' : ''}" data-nav="${id}">${ic(ico)}<span>${label}</span>${id === 'twin' ? '<span class="count">3D</span>' : ''}</button>`).join('')}<div class="side-foot"><span class="tag mint">EMBODIED LAB</span><p style="margin-top:10px">从工程需求，到空间执行。<br>研发物流实验仓 · V3</p></div></aside>` : ''}<div class="app-main" ${showNavigation ? '' : 'style="margin-left:0"'}><header class="topbar"><div class="small flex"><span>工作空间</span><span style="color:#b8c8d1">/</span><strong id="mb-breadcrumb" style="color:#446675;font-weight:500">数字孪生仓库</strong></div><div class="flex"><input class="input search" id="mb-global-search" placeholder="搜索物料、柜子或任务  ⌘ K" aria-label="搜索物料或柜子"><span class="tag">合成实验仓</span><span class="user-dot">M</span></div></header><main class="page"><section id="mb-twin-page"><div class="page-heading"><div><h1>数字孪生仓库<span style="color:#8baab8">.</span></h1><p>看见每一个库位，让每一次移动都有依据。</p></div><div class="heading-right"><div class="tabs"><button class="active">研发物流实验仓</button><button data-action="live">真实仓库 ↗</button></div><button class="btn optional" data-action="export">${ic('download')}导出回放</button></div></div><div class="twin-layout"><section class="panel assets-panel"><div class="panel-title">设备与区域 <span class="tag">${world.assets.length}</span></div><div class="asset-search"><input class="input" id="mb-asset-filter" placeholder="名称 / 编号 / 物料" aria-label="筛选设备"></div><div class="asset-scroll" id="mb-assets"></div></section><section class="panel stage-panel"><div class="canvas-host" id="mb-scene"></div><div class="stage-toolbar"><div class="stage-info"><strong>研发物流实验仓</strong><span>24 × 18 m · 0.25 m NAV GRID</span></div><div class="stage-tools"><button class="btn icon" title="显示碰撞膨胀层" aria-label="碰撞膨胀层" data-action="costmap">${ic('grid')}</button><button class="btn icon" title="沉浸查看" aria-label="沉浸查看" data-action="immersive">${ic('expand')}</button><button class="btn icon optional" title="显示设备标签" aria-label="设备标签" data-action="labels">${ic('list')}</button><button class="btn icon" title="放大" aria-label="放大" data-action="zoom-in">${ic('plus')}</button><button class="btn icon" title="缩小" aria-label="缩小" data-action="zoom-out">${ic('minus')}</button><button class="btn icon" title="复位视角" aria-label="复位视角" data-action="reset-camera">${ic('expand')}</button></div></div><div class="stage-footer"><div class="legend"><span><i style="background:#439db0"></i>规划路线</span><span><i style="background:#b4cedb"></i>原可视化设备</span><span><i style="background:#cbb88f"></i>限速区域</span></div><div class="camera-tabs"><button data-camera="overview" class="active">总览</button><button data-camera="aisle">通道</button><button data-camera="top">俯视</button><button data-camera="robot">跟随视角</button></div></div></section><aside class="panel mission-panel"><div class="panel-title">机器人任务 <span class="tag mint" id="mb-state">待规划</span></div><div class="mission-scroll"><div class="robot-card"><div class="robot-icon">${ic('robot')}</div><div style="flex:1"><strong>MB–R01 <span class="small" style="font-size:10px">移动备料助手</span></strong><small>差速底盘 · 18 kg 载荷 · 仿真</small><div class="battery"><b id="mb-battery"></b></div></div></div><div class="flex between"><strong style="font-size:14px">控制板物料备料</strong><span class="small" id="mb-goal-count">6 站</span></div><div class="goal-list" id="mb-goals"></div><div class="flex between"><span class="section-label" style="margin:0">任务条件</span><button class="btn text" data-action="all-goals" style="font-size:11px">编辑停靠点</button></div><select class="scenario-select" id="mb-scenario" aria-label="选择验证场景">${world.scenarios.map((s) => `<option value="${s.id}">${esc(s.name)}</option>`).join('')}</select><div id="mb-metrics"><div class="plan-hint">正在构建米制碰撞地图与六站备料路线…</div></div><div class="section-label">执行与观察</div><div class="flex between" style="margin:9px 0;font-size:11px;color:#7e95a0"><label><input type="checkbox" id="mb-auto"> 仿真自动交接</label><select id="mb-speed" style="border:1px solid #d8e6ed;border-radius:6px;background:#fff;font-size:11px;padding:3px" aria-label="仿真速度"><option value="1">1×</option><option value="4" selected>4×</option><option value="12">12×</option><option value="32">32×</option></select></div><button class="btn text" style="font-size:12px" data-action="cancel">取消当前仿真</button><div class="event-log" id="mb-events">导航尚未启动。路线预览不会修改库存。</div><div class="tool-status" id="mb-diagnostics">THREE.JS · GEOMETRY / COLLISION SHARED</div></div><div class="mission-actions"><button class="btn primary wide" data-action="start" id="mb-start">${ic('play')}开始仿真任务</button><button class="btn" data-action="plan" id="mb-plan-btn">${ic('route')}重新规划</button><button class="btn" data-action="pause">${ic('pause')}暂停</button><button class="btn" data-action="obstacle">${ic('alert')}加入占道料车</button><button class="btn" data-action="detail">${ic('shelf')}库位详情</button><button class="btn soft wide hidden" id="mb-handoff" data-action="handoff">${ic('check')}扫码确认交接</button></div></aside></div><div class="bottom-strip"><span>实验地图与真实库存独立 · 货架 / 停靠点 / 碰撞外廓同源 · ${world.assets.length} 组设备</span><span id="mb-plan-footer">目标解析 → 路径规划 → 仿真执行 → 反馈回放</span></div></section><section id="mb-home-page" class="hidden"></section><section id="mb-brain-page" class="hidden"></section></main></div>${showBot ? `<button class="bot-launcher" id="mb-bot" aria-label="打开 MaterialBrain 悬浮助手"><img src="${esc(botUrl)}" alt="MaterialBrain 卡通机器人"><small>随时问我</small></button><section class="bot-panel hidden" id="mb-bot-panel" aria-label="悬浮物料大脑"><div class="bot-head"><img src="${esc(botUrl)}" alt=""><strong>MaterialBrain</strong><button class="btn text" data-action="continue-brain" title="在工作台继续">${ic('expand')}</button><button class="btn text" data-action="close-bot" title="关闭">${ic('close')}</button></div><div class="quick-prompts"><button data-prompt="PCM5102APWR 在哪里？">PCM5102APWR 在哪里？</button><button data-prompt="规划控制板备料路线">规划控制板备料路线</button><button data-prompt="中央通道被占用怎么办？">中央通道被占用怎么办？</button><button data-prompt="机器人可以自动取抽屉吗？">机器人可以自动取抽屉吗？</button></div><div class="messages" id="mb-chat-messages"></div><form class="chat-compose" id="mb-chat-form"><input class="input" id="mb-chat-input" placeholder="问物料，或告诉我你的任务…" autocomplete="off"><button class="btn primary" type="submit" aria-label="发送">${ic('arrow')}</button></form></section>` : ''}<div id="mb-modal-root"></div><div class="toast hidden" id="mb-toast" role="status"></div>`
  const $ = (id) => root.querySelector('#' + id)
  $('mb-scenario').value = scenario
  if (
    !world.scenarios.some((s) => s.id === scenario) ||
    selectedGoals.some((id) => !world.goals.some((g) => g.id === id))
  )
    throw new Error('INVALID_INITIAL_TASK')
  const runner = new MissionRunner(world, { onChange: () => refreshMission() })
  if (initialExecution) runner.restore(initialExecution)
  function toast(text) {
    $('mb-toast').textContent = text
    $('mb-toast').classList.remove('hidden')
    clearTimeout(toastTimer)
    toastTimer = setTimeout(() => $('mb-toast').classList.add('hidden'), 4000)
  }
  function renderAssets(query = '') {
    query = query.toLowerCase().trim()
    const groups = {
      A: 'A / 精密元件区',
      B: 'B / 交错储物区',
      C: 'C / 收货与缓冲',
      D: 'D / 备料与设施',
    }
    $('mb-assets').innerHTML =
      Object.entries(groups)
        .map(([zone, title]) => {
          const list = world.assets.filter(
            (a) =>
              a.zone === zone &&
              (
                a.id +
                ' ' +
                a.name +
                ' ' +
                world.goals
                  .filter((g) => g.asset_id === a.id)
                  .map((g) => g.sku)
                  .join(' ')
              )
                .toLowerCase()
                .includes(query),
          )
          return list.length
            ? `<div class="asset-group">${title}</div>${list.map((a) => `<button class="asset-row ${a.id === selectedAsset ? 'active' : ''}" data-asset="${a.id}"><span class="asset-thumb">${ic(a.kind === 'drawer100' ? 'grid' : a.kind.includes('organizer') ? 'chip' : a.kind === 'shelf6' ? 'shelf' : 'cube')}</span><span><small>${a.id} · ${a.kind === 'drawer100' ? '100 DRAWERS' : a.kind === 'shelf6' ? '6 LEVELS' : a.kind.includes('organizer') ? 'COMPONENT BINS' : a.kind.toUpperCase()}</small><strong>${esc(a.name.split(' · ')[0])}</strong></span></button>`).join('')}`
            : ''
        })
        .join('') || '<p class="small" style="padding:15px">没有匹配设备</p>'
  }
  function renderGoals() {
    const order = plan?.goal_ids || selectedGoals
    $('mb-goals').innerHTML = world.goals
      .filter((g) => showAllGoals || selectedGoals.includes(g.id))
      .map(
        (g) =>
          `<label class="goal"><input type="checkbox" value="${g.id}" ${selectedGoals.includes(g.id) ? 'checked' : ''}><span><span>${esc(g.label)}</span><br><span class="goal-code">${esc(g.sku)} · ${g.asset_id}</span></span><span class="slot">${order.includes(g.id) ? String(order.indexOf(g.id) + 1).padStart(2, '0') + ' · ' : ''}${esc(g.slot)}</span></label>`,
      )
      .join('')
    $('mb-goal-count').textContent = selectedGoals.length + ' 站'
  }
  function refreshMission() {
    if (!root.isConnected || disposed) return
    const status = runner.state === 'IDLE' && plan ? plan.status : runner.state
    $('mb-state').textContent = stateNames[status] || status
    $('mb-state').className =
      'tag ' + (['BLOCKED', 'NEEDS_CHARGE'].includes(status) ? 'amber' : 'mint')
    $('mb-start').disabled =
      busy ||
      taskDirty ||
      !plan ||
      plan.status !== 'READY' ||
      !['IDLE', 'READY', 'PAUSED', 'CANCELLED', 'COMPLETED'].includes(runner.state)
    $('mb-plan-btn').disabled = busy
    $('mb-handoff').classList.toggle('hidden', runner.state !== 'WAITING_HANDOFF')
    const evNames = {
      PLAN_READY: '路线就绪',
      START: '开始执行',
      ARRIVED: '已抵达',
      HANDOFF_CONFIRMED: '交接确认',
      REPLAN_REQUESTED: '停稳并请求重规划',
      REPLAN_READY: '新路线已就绪',
      REPLAN_BLOCKED: '目标不可达',
      COLLISION_STOP: '前方占用，保持停止',
      COMPLETED: '全部任务完成',
      SCAN_REJECTED: '库位扫码不一致',
      PAUSE: '任务暂停',
      CANCEL: '任务取消',
    }
    $('mb-events').innerHTML =
      runner.events
        .slice(-4)
        .map(
          (e) =>
            `<div><span style="color:#9aafb7">${fmt(e.t_sim_s, 1)}s</span> ${esc(evNames[e.type] || e.type)} ${e.goal ? esc(e.goal) : ''}</div>`,
        )
        .join('') || '导航尚未启动。路线预览不会修改库存。'
    if (plan && ['READY', 'NEEDS_CHARGE'].includes(plan.status))
      $('mb-battery').style.width = Math.max(0, Math.min(100, plan.battery_after_pct)) + '%'
  }
  function metricsHTML() {
    if (!plan) return '<div class="plan-hint">选择停靠点，然后生成路线。</div>'
    if (!['READY', 'NEEDS_CHARGE'].includes(plan.status))
      return `<div class="plan-hint"><strong>${esc(stateNames[plan.status] || plan.status)}</strong><br>${esc(plan.reason || '请调整任务点、载荷或机器人能力。')}<br><span class="small">已完成的站点会保留，不把失败当成成功。</span></div>`
    return `<div class="metrics"><div class="metric"><span>路线长度</span><strong>${fmt(plan.distance_m)}</strong><small>m</small></div><div class="metric"><span>预计用时</span><strong>${fmt(plan.eta_s / 60)}</strong><small>min</small></div><div class="metric"><span>最小外廓净空</span><strong>${fmt(plan.min_clearance_m * 100, 1)}</strong><small>cm</small></div><div class="metric"><span>预计载荷</span><strong>${fmt(plan.payload_kg)}</strong><small>kg</small></div></div><div class="benefit">${plan.status === 'NEEDS_CHARGE' ? '电量将低于返航阈值，先充电再启动。' : `综合代价较输入顺序减少 ${fmt(plan.improvement_pct)}%`}<br><span style="font-size:10px">转向 + 限速 + 净空代价；时间与电量为仿真估计。</span></div><div class="small" style="font-size:10px;margin-top:9px;line-height:1.8">朝向栅格 A* · ${plan.order_method === 'held_karp_exact' ? '≤8 站精确排序' : '近邻 + 2-opt'}<br>半径 ${fmt(planner.radius, 2)} m + ${fmt(world.robot.margin * 100, 0)} cm 余量 · 地图 v${world.revision}</div>`
  }
  async function planMission({ replan = false } = {}) {
    if (busy) return
    const generation = ++planSequence
    busy = true
    refreshMission()
    $('mb-metrics').innerHTML = '<div class="plan-hint">计算外廓碰撞、停靠朝向与多站任务顺序…</div>'
    await new Promise((r) => setTimeout(r, 35))
    const s = world.scenarios.find((s) => s.id === scenario),
      obs = s?.obstacles || [],
      goals = replan
        ? selectedGoals.filter((id) => !runner.completed.includes(id))
        : [...selectedGoals]
    try {
      const profile = { ...world.robot, battery_pct: s?.battery_pct ?? world.robot.battery_pct }
      const p = new MetricPlanner(world, { profile, obstacles: obs })
      const options = {
        start: replan ? { ...runner.pose } : world.home,
        end: world.home,
        optimize: true,
        battery_pct: replan
          ? Math.min(profile.battery_pct, runner.batteryPct)
          : profile.battery_pct,
        payload_kg: replan ? runner.cargo : 0,
      }
      const result = planningClient
        ? await planningClient({
            world_id: world.id,
            goal_ids: goals,
            scenario_id: scenario,
            start: options.start,
            payload_kg: options.payload_kg,
            battery_pct: options.battery_pct,
            world_revision: world.revision_sha256,
          })
        : p.plan(goals, options)
      if (disposed || generation !== planSequence) return
      planner = p
      plan = result
      taskDirty = false
      scene.setDynamic(obs)
      scene.setPlan(plan)
      if (costVisible) scene.setCostmap(planner, true)
      if (plan.status === 'READY') {
        runner.load(plan, { preserveProgress: replan, obstacles: obs })
        runner.autoHandoff = $('mb-auto').checked
      } else if (replan) runner.failReplan(plan.reason || plan.status)
      else {
        runner.reset()
        scene.setRobot(runner.pose)
      }
      $('mb-metrics').innerHTML = metricsHTML()
      $('mb-plan-footer').textContent =
        `${plan.status === 'READY' ? '可执行仿真' : '任务检查'} · ${world.revision_sha256.slice(0, 10)} · 不涉及库存扣减`
      renderGoals()
      renderBrain()
      if (replan && plan.status === 'READY')
        toast('已从当前位姿重新规划。已确认站点保留，点击开始继续。')
    } catch (e) {
      if (disposed) return
      plan = { status: 'BLOCKED', reason: e.message }
      scene.setPlan(plan)
      $('mb-metrics').innerHTML = metricsHTML()
      if (replan) runner.failReplan(e.message)
      toast('路线未生成：' + e.message)
    } finally {
      busy = false
      refreshMission()
    }
  }
  function selectAsset(id, focus = false) {
    selectedAsset = id
    scene.select(id, { focus })
    renderAssets($('mb-asset-filter').value)
  }
  function renderHome() {
    const count = world.assets.length
    $('mb-home-page').innerHTML =
      `<div class="page-heading"><div><h1>把需求，交给 MaterialBrain<span style="color:#8eabb9">.</span></h1><p>从找到物料，到让任务在仓库里真正走通。</p></div><span class="tag mint">工程与空间执行工作台</span></div><div class="home-hero"><div class="hero-copy"><div class="eyebrow">MATERIAL INTELLIGENCE / EMBODIED LAB</div><h2>理解物料。<br>也理解它所在的空间。</h2><p>证据化选型、可视化库位与机器人任务，<br>在一个连续的工作流里完成。</p><div class="flex"><button class="btn primary" data-nav="brain">${ic('brain')}开始一个工程任务</button><button class="btn" data-nav="twin">${ic('cube')}探索三维仓库</button></div></div><div class="hero-art"><img src="${esc(botUrl)}" alt="MaterialBrain 新形象"></div></div><div class="home-kpis"><div class="metric"><span>异构设备模型</span><strong>${count}</strong><small>组</small></div><div class="metric"><span>可验证任务停靠点</span><strong>${world.goals.length}</strong><small>站</small></div><div class="metric"><span>规划网格间距</span><strong>${world.resolution * 100}</strong><small>cm</small></div><div class="metric"><span>场景与任务回归</span><strong>4</strong><small>类</small></div></div><div class="home-grid"><section class="panel"><div class="panel-title">从这里继续 <span class="small">我的工作流</span></div><div class="feature-row"><span class="brand-mark">${ic('chip')}</span><div><strong>控制板备料与路线规划</strong><small>6 个停靠点 · 扫码核对 · 连续执行</small></div><button class="btn" data-nav="brain">继续 ${ic('arrow')}</button></div><div class="feature-row"><span class="brand-mark">${ic('shelf')}</span><div><strong>100 抽柜、元件盒与六层货架</strong><small>抽屉、盒格与货架层级一目了然</small></div><button class="btn" data-action="detail">查看 ${ic('arrow')}</button></div><div class="feature-row"><span class="brand-mark">${ic('route')}</span><div><strong>中央通道受阻演练</strong><small>从当前位姿重规划，不重做已完成站点</small></div><button class="btn" data-action="scenario-demo">演练 ${ic('arrow')}</button></div></section><section class="panel" style="padding:22px"><div class="eyebrow">ONE TASK / CONTINUOUS CONTEXT</div><h3 style="font-size:20px;margin:8px 0 14px">同一份任务，两个入口。</h3><p class="small" style="line-height:2">悬浮助手适合随时查找。完整物料大脑适合核对证据、比较方案和组织任务。两者共享会话，不重复提交。</p><div style="margin-top:20px;padding:14px;background:#f0f7f8;border-radius:13px"><span class="tag">示例提问</span><p style="font-size:14px;margin-top:9px">“PCM5102APWR 在哪里？把它加入控制板备料，规划一条机器人能走的路线。”</p></div><button class="btn soft" style="margin-top:17px" data-action="open-bot">${ic('robot')}问问 MaterialBrain</button></section></div>`
  }
  function renderBrain() {
    $('mb-brain-page').innerHTML =
      `<div class="page-heading"><div><h1>物料大脑<span style="color:#8eabb9">.</span></h1><p>工程结果与空间执行，留在同一份任务上下文。</p></div><div class="heading-right"><span class="tag mint">任务：控制板备料</span><button class="btn" data-nav="twin">${ic('route')}打开仓库执行</button></div></div><div class="brain-layout"><div class="panel brain-main"><div class="brain-task">请为研发控制板整理已有物料，核对库位，并生成可由移动机器人执行的六站备料路线。</div><div class="flex between"><strong>任务已整理</strong><span class="tag">规划工具结果</span></div><div class="brain-flow"><div class="brain-step"><b>01 / GROUND</b>物料与库位解析</div><div class="brain-step"><b>02 / VALIDATE</b>能力、载荷与电量</div><div class="brain-step"><b>03 / PLAN</b>停靠朝向与路线</div><div class="brain-step"><b>04 / EXECUTE</b>执行反馈与交接</div></div><table class="brain-table"><thead><tr><th>物料</th><th>库位</th><th>获取方式</th><th>空间操作</th></tr></thead><tbody>${world.goals
        .filter((g) => selectedGoals.includes(g.id))
        .map(
          (g) =>
            `<tr><td><strong style="font-weight:550">${esc(g.sku)}</strong><br><span class="small" style="font-size:11px">${esc(g.label)}</span></td><td>${g.asset_id} / ${g.slot}</td><td><span class="tag">扫码 + 人工交接</span></td><td><button class="btn text" style="font-size:12px" data-locate="${g.asset_id}">定位 ↗</button></td></tr>`,
        )
        .join(
          '',
        )}</tbody></table><div style="margin-top:17px">${metricsHTML()}</div><div id="mb-brain-messages" style="margin-top:18px">${chat
        .slice(-4)
        .map(
          (m) =>
            `<div class="message ${m.role === 'user' ? 'user' : ''}">${esc(m.content).replaceAll('\n', '<br>')}</div>`,
        )
        .join(
          '',
        )}</div><form class="brain-compose" id="mb-brain-form"><input id="mb-brain-input" placeholder="继续描述需求，或询问这份任务的证据…" aria-label="继续提问"><button class="btn primary" type="submit">${ic('arrow')}继续</button></form></div><aside class="panel context-panel"><div class="eyebrow">ENGINEERING CONTEXT</div><h3 style="font-size:19px;margin:10px 0 17px">不是只有一条路径。</h3><table class="detail-table"><tr><td>运行模式</td><td>独立实验仓</td></tr><tr><td>地图单位</td><td>米 / map</td></tr><tr><td>底盘模型</td><td>差速停转</td></tr><tr><td>可执行动作</td><td>导航、扫码、运箱</td></tr><tr><td>取货策略</td><td>到位后人工交接</td></tr><tr><td>库存写入</td><td>原事务流程</td></tr></table><p class="small" style="line-height:1.95;margin-top:20px">机器人不能打开所有抽屉，导航到位也不代表已经取到物料。任务状态会分别记录到达、扫码、交接和返回。</p><button class="btn soft" style="width:100%;margin-top:23px" data-nav="twin">进入仿真任务 ${ic('arrow')}</button><p style="font-size:11px;color:#8ca1ab;margin-top:18px">本页离线样例使用确定性意图示例，不调用大模型。正式接入沿用原 Agent 与完整工程卡片。</p></aside></div>`
    $('mb-brain-form').addEventListener('submit', (e) => {
      e.preventDefault()
      const text = $('mb-brain-input').value.trim()
      if (text) ask(text)
    })
  }
  function navigate(id) {
    if (!['twin', 'home', 'brain'].includes(id)) {
      onNavigate(id)
      toast('独立预览未连接业务数据库。请在部署站点打开此页面。')
      return
    }
    for (const p of ['twin', 'home', 'brain'])
      $('mb-' + p + '-page').classList.toggle('hidden', id !== p)
    root
      .querySelectorAll('[data-nav]')
      .forEach((b) => b.classList.toggle('active', b.dataset.nav === id))
    $('mb-breadcrumb').textContent = navItems.find((n) => n[0] === id)[2]
    if (id === 'home') renderHome()
    if (id === 'brain') renderBrain()
    if (id === 'twin') setTimeout(() => scene.resize(), 0)
  }
  function toggleBot(value = !botOpen) {
    if (!showBot) {
      if (value) onNavigate('floating-agent')
      return
    }
    botOpen = value
    $('mb-bot-panel').classList.toggle('hidden', !botOpen)
    renderChat()
  }
  function renderChat() {
    if (!showBot) return
    $('mb-chat-messages').innerHTML = chat.length
      ? chat
          .map(
            (m) =>
              `<div class="message ${m.role === 'user' ? 'user' : ''}">${m.role === 'assistant' ? '<strong>MaterialBrain</strong>' : ''}${esc(m.content).replaceAll('\n', '<br>')}</div>`,
          )
          .join('')
      : '<div class="message"><strong>你好，我是 MaterialBrain。</strong>可以帮你定位物料、整理任务，并把备料需求交给路径规划工具。<br><span class="small" style="font-size:11px">离线样例 · 与完整工作台共享上下文</span></div>'
    $('mb-chat-messages').scrollTop = $('mb-chat-messages').scrollHeight
  }
  async function ask(text) {
    chat.push({ role: 'user', content: text })
    let reply
    if (/在哪|哪里|PCM5102/i.test(text)) {
      selectedAsset = 'A01'
      reply =
        'PCM5102APWR 的实验库位是 A01 柜 / A05 抽屉。\n已将 A01 设为当前设备。你可以打开库位详情，或在三维仓库中聚焦。'
      scene.select('A01', { focus: true })
      renderAssets()
    } else if (/占用|堵|受阻/.test(text)) {
      reply =
        '先暂停当前动作，保留已交接站点，再从当前位姿重新规划剩余任务。\n仓库面板的“加入占道料车”会执行这段流程；不会让机器人穿过料车。'
    } else if (/自动取|抓取|抽屉/.test(text)) {
      reply =
        '这台底盘具备导航、扫码和运箱能力，当前没有自动开抽屉或抓取能力。\n因此计划中明确加入“人工交接”，到达与实际取料是不同状态。后续可通过能力注册接入机械臂技能。'
    } else if (/路径|路线|备料|规划/.test(text)) {
      await action('plan')
      reply =
        plan?.status === 'READY'
          ? `已通过规划工具生成 ${plan.goal_ids.length} 站路线，长度 ${fmt(plan.distance_m)} m，预计 ${fmt(plan.eta_s / 60)} 分钟（仿真估计）。\n考虑了外廓、停靠朝向、限速区、载荷和返航电量。打开三维仓库可开始执行。`
          : '任务需要先处理：' + (plan?.reason || plan?.status)
    } else
      reply =
        '这个离线样例演示物料定位、备料路线、受阻重规划和能力核对。\n正式项目接入原 MaterialBrain Agent 后，继续保留选型、证据与 BOM 等完整能力。'
    chat.push({ role: 'assistant', content: reply })
    renderChat()
    renderBrain()
    if (showBot) $('mb-chat-input').value = ''
  }
  function detail() {
    const a = world.assets.find((a) => a.id === selectedAsset)
    if (!a) return
    const goals = world.goals.filter((g) => g.asset_id === a.id),
      g = goals[0]
    const grid =
      a.kind === 'drawer100'
        ? `<div class="drawer-cabinet">${Array.from({ length: 100 }, (_, i) => {
            const code =
              String.fromCharCode(65 + (i % 5)) + String(Math.floor(i / 5) + 1).padStart(2, '0')
            return `<button data-slot="${code}" class="${code === (g?.slot || 'A05') ? 'active' : ''}">${code}</button>`
          }).join('')}</div>`
        : a.kind.startsWith('organizer')
          ? `<div class="drawer-cabinet" style="grid-template-columns:repeat(8,1fr);border-color:#95afbd">${Array.from(
              { length: 56 },
              (_, i) => {
                const code =
                  a.kind === 'organizerMix'
                    ? `${i < 28 ? 'L' : 'R'}-S${String((i % 28) + 1).padStart(2, '0')}`
                    : `${String.fromCharCode(65 + Math.floor(i / 8))}${String((i % 8) + 1).padStart(2, '0')}`
                return `<button data-slot="${code}" style="height:32px">${code}</button>`
              },
            ).join('')}</div>`
          : ['shelf6', 'flowrack'].includes(a.kind)
            ? `<div style="background:#eef3f6;padding:16px;border-radius:12px">${Array.from({ length: 6 }, (_, i) => `<button class="btn" data-slot="L0${6 - i}" style="width:100%;margin:7px 0;border-bottom:5px solid #93acb9;justify-content:space-between">L0${6 - i}<span style="color:#8ba1ac;font-size:11px">${i % 2 ? '元件箱  □ □' : '周转物料  ▣ ▣'}</span></button>`).join('')}</div>`
            : `<div class="panel" style="padding:25px"><h3>${esc(a.name)}</h3><p class="small" style="margin-top:12px">${esc(a.kind)} · ${a.width} × ${a.depth} m</p></div>`
    $('mb-modal-root').innerHTML =
      `<div class="modal-back"><section class="modal" role="dialog" aria-modal="true" aria-label="库位详情"><div class="modal-header"><div><span class="eyebrow">${a.id} / LOCATION DETAIL</span><h2 style="font-size:22px;margin-top:5px">${esc(a.name)}</h2></div><button class="btn icon" data-action="close-modal" aria-label="关闭详情">${ic('close')}</button></div><div class="modal-content"><div class="detail-grid"><div>${grid}<p class="small" style="font-size:11px;margin-top:10px">选择一个抽屉、盒格或货架层级。</p></div><div><h3 style="font-size:18px">当前库位 <span id="mb-detail-slot">${esc(g?.slot || 'L01')}</span></h3><table class="detail-table"><tr><td>物料示例</td><td>${esc(g?.sku || '按库位查询')}</td></tr><tr><td>设备编号</td><td>${a.id}</td></tr><tr><td>外廓尺寸</td><td>${a.width} × ${a.depth} × ${a.height} m</td></tr><tr><td>关联原设备</td><td style="overflow-wrap:anywhere">${esc(a.reference_code || '新增实验设备')}</td></tr><tr><td>数据来源</td><td>独立合成实验仓</td></tr></table><p class="small" style="margin:17px 0;line-height:1.9">打开关联库位，可继续查看实际物料并编辑。</p><button class="btn primary" data-action="original-location">打开原版库位 ${ic('arrow')}</button></div></div></div></section></div>`
    const first = $('mb-modal-root').querySelector('button')
    first?.focus()
  }
  function exportData() {
    const payload = runner.exportReplay()
    payload.request = { goal_ids: selectedGoals, scenario_id: scenario }
    payload.scene_diagnostics = scene.diagnostics()
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' }),
      url = URL.createObjectURL(blob),
      a = document.createElement('a')
    a.href = url
    a.download = 'materialbrain-navigation-replay.json'
    a.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
    toast('已导出任务计划、场景版本和仿真事件。')
  }
  async function addObstacle() {
    if (busy) return
    if (runner.state === 'WAITING_HANDOFF') {
      toast('请先完成当前站点交接，再进行受阻演练。')
      return
    }
    scenario = 'blocked-crossing'
    $('mb-scenario').value = scenario
    if (
      plan &&
      runner.state !== 'IDLE' &&
      runner.state !== 'COMPLETED' &&
      runner.state !== 'CANCELLED'
    ) {
      runner.beginReplan(world.scenarios.find((s) => s.id === scenario).obstacles)
      await planMission({ replan: true })
    } else await planMission()
  }
  async function action(name) {
    if (
      externalExecution &&
      [
        'plan',
        'start',
        'pause',
        'cancel',
        'obstacle',
        'handoff',
        'confirm-scan',
        'scenario-demo',
      ].includes(name)
    ) {
      toast('当前空间任务请使用任务卡片操作；开启新会话后可继续实验仓演练。')
      return
    }
    switch (name) {
      case 'plan':
        if (['RUNNING', 'PAUSED', 'WAITING_HANDOFF'].includes(runner.state)) {
          if (runner.state === 'WAITING_HANDOFF') {
            toast('请先确认交接，再重新规划剩余任务。')
            return
          }
          runner.pause()
          runner.beginReplan(world.scenarios.find((s) => s.id === scenario)?.obstacles || [])
          await planMission({ replan: true })
        } else await planMission()
        break
      case 'start':
        if (!plan || plan.status !== 'READY') return
        if (['COMPLETED', 'CANCELLED', 'IDLE'].includes(runner.state)) runner.load(plan)
        runner.start()
        break
      case 'pause':
        runner.pause()
        break
      case 'cancel':
        runner.cancel()
        break
      case 'immersive':
        root.classList.toggle('immersive')
        setTimeout(() => scene.resize(), 20)
        break
      case 'obstacle':
        await addObstacle()
        break
      case 'costmap':
        costVisible = !costVisible
        if (planner) scene.setCostmap(planner, costVisible)
        break
      case 'labels':
        scene.labelGroup.visible = !scene.labelGroup.visible
        scene.dirty = true
        break
      case 'zoom-in':
        scene.zoom(0.85)
        break
      case 'zoom-out':
        scene.zoom(1.17)
        break
      case 'reset-camera':
        scene.preset('overview')
        break
      case 'detail':
        detail()
        break
      case 'close-modal':
        $('mb-modal-root').innerHTML = ''
        break
      case 'original-location': {
        const a = world.assets.find((x) => x.id === selectedAsset)
        onOpenLocation({
          reference_code: a.reference_code,
          asset_id: a.id,
          slot: $('mb-detail-slot')?.textContent,
          world_id: world.id,
        })
        toast('正在打开关联库位。')
        break
      }
      case 'live':
        onNavigate('warehouse-live')
        toast('真实仓库入口保持原地图与库存，不使用实验坐标替换。')
        break
      case 'open-bot':
        toggleBot(true)
        break
      case 'close-bot':
        toggleBot(false)
        break
      case 'continue-brain':
        toggleBot(false)
        navigate('brain')
        break
      case 'export':
        exportData()
        break
      case 'handoff': {
        const goal = world.goals.find((g) => g.id === runner.currentGoal())
        if (!goal) return
        $('mb-modal-root').innerHTML =
          `<div class="modal-back"><section class="modal" role="dialog" aria-modal="true" style="max-width:420px"><div class="modal-header"><h3>扫码与交接</h3><button class="btn icon" data-action="close-modal">${ic('close')}</button></div><div class="modal-content"><p style="font-size:14px;margin-bottom:13px">${goal.asset_id} / ${goal.slot} · ${esc(goal.sku)}</p><p class="small" style="margin-bottom:15px">实验交接：输入目标库位码 ${goal.slot}，不扣减库存。</p><input class="input" id="mb-scan" placeholder="输入库位码" autocomplete="off"><button class="btn primary" data-action="confirm-scan" style="margin-top:16px;width:100%">确认实验交接</button></div></section></div>`
        $('mb-scan').focus()
        break
      }
      case 'confirm-scan':
        if (runner.confirmHandoff({ scanCode: $('mb-scan').value.trim() })) {
          $('mb-modal-root').innerHTML = ''
        } else toast('库位码不匹配，保持等待。')
        break
      case 'all-goals':
        showAllGoals = !showAllGoals
        renderGoals()
        break
      case 'scenario-demo':
        navigate('twin')
        await addObstacle()
        break
    }
  }
  const click = (e) => {
    const btn = e.target.closest('button')
    if (!btn) return
    if (btn.dataset.nav) navigate(btn.dataset.nav)
    else if (btn.dataset.action) void action(btn.dataset.action)
    else if (btn.dataset.asset) selectAsset(btn.dataset.asset, true)
    else if (btn.dataset.locate) {
      navigate('twin')
      selectAsset(btn.dataset.locate, true)
    } else if (btn.dataset.camera) {
      scene.preset(btn.dataset.camera)
      root.querySelectorAll('[data-camera]').forEach((b) => b.classList.toggle('active', b === btn))
    } else if (btn.dataset.prompt) {
      toggleBot(true)
      void ask(btn.dataset.prompt)
    } else if (btn.dataset.slot) {
      root.querySelectorAll('[data-slot]').forEach((b) => b.classList.toggle('active', b === btn))
      $('mb-detail-slot').textContent = btn.dataset.slot
    }
  }
  root.addEventListener('click', click)
  cleanups.push(() => root.removeEventListener('click', click))
  $('mb-goals').addEventListener('change', (e) => {
    const id = e.target.value
    if (e.target.checked) selectedGoals = [...new Set([...selectedGoals, id])]
    else selectedGoals = selectedGoals.filter((g) => g !== id)
    $('mb-goal-count').textContent = selectedGoals.length + ' 站'
    taskDirty = true
    refreshMission()
    if (runner.state === 'RUNNING') runner.pause()
    toast('任务点已改变，请重新规划。')
  })
  $('mb-asset-filter').addEventListener('input', (e) => renderAssets(e.target.value))
  $('mb-global-search').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      navigate('twin')
      $('mb-asset-filter').value = e.target.value
      renderAssets(e.target.value)
    }
  })
  $('mb-scenario').addEventListener('change', (e) => {
    if (externalExecution) {
      e.target.value = scenario
      toast('请在空间任务面板选择并应用任务场景。')
      return
    }
    if (runner.state === 'WAITING_HANDOFF') {
      e.target.value = scenario
      toast('完成当前站交接后，可切换场景。')
      return
    }
    scenario = e.target.value
    if (runner.state === 'RUNNING') runner.pause()
    void planMission({ replan: ['PAUSED', 'BLOCKED', 'REPLANNING'].includes(runner.state) })
  })
  $('mb-auto').addEventListener('change', (e) => {
    runner.autoHandoff = e.target.checked
  })
  $('mb-speed').addEventListener('change', (e) => (speed = Number(e.target.value)))
  const keydown = (e) => {
    if (e.key === 'Escape') {
      root.classList.remove('immersive')
      setTimeout(() => scene.resize(), 20)
      $('mb-modal-root').innerHTML = ''
      toggleBot(false)
    }
    if (showNavigation && (e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
      e.preventDefault()
      $('mb-global-search').focus()
    }
  }
  document.addEventListener('keydown', keydown)
  cleanups.push(() => document.removeEventListener('keydown', keydown))
  if (showBot) {
    $('mb-chat-form').addEventListener('submit', (e) => {
      e.preventDefault()
      const t = $('mb-chat-input').value.trim()
      if (t) void ask(t)
    })
    let press = null,
      dragged = false
    const launch = $('mb-bot')
    launch.addEventListener('pointerdown', (e) => {
      const r = launch.getBoundingClientRect()
      press = { x: e.clientX, y: e.clientY, left: r.left, top: r.top }
      dragged = false
      launch.setPointerCapture(e.pointerId)
    })
    launch.addEventListener('pointermove', (e) => {
      if (!press) return
      const dx = e.clientX - press.x,
        dy = e.clientY - press.y
      if (Math.hypot(dx, dy) > 5) dragged = true
      if (dragged) {
        launch.style.left =
          Math.max(0, Math.min(innerWidth - launch.clientWidth, press.left + dx)) + 'px'
        launch.style.top =
          Math.max(0, Math.min(innerHeight - launch.clientHeight, press.top + dy)) + 'px'
        launch.style.right = 'auto'
        launch.style.bottom = 'auto'
      }
    })
    launch.addEventListener('pointerup', (e) => {
      if (!press) return
      press = null
      try {
        launch.releasePointerCapture(e.pointerId)
      } catch {
        /* Pointer capture may already have been released by the browser. */
      }
      if (!dragged) toggleBot()
      else
        try {
          localStorage.setItem(
            'mb-v3-preview-bot-position',
            JSON.stringify({ left: launch.offsetLeft, top: launch.offsetTop }),
          )
        } catch {
          /* Preview position storage is optional in restricted browsers. */
        }
    })
  }
  $('mb-goals').before($('mb-metrics'))
  renderAssets()
  renderGoals()
  renderHome()
  renderBrain()
  scene = new EmbodiedWarehouseScene($('mb-scene'), world, {
    THREE,
    OrbitControls,
    onSelect: (id) => {
      selectedAsset = id
      renderAssets($('mb-asset-filter').value)
    },
    onFrame: (dt) => {
      if (!externalExecution) runner.tick(dt * speed, planner)
      scene?.setRobot(externalExecution?.current_pose || runner.pose)
      if (scene && scene.frameCount % 180 === 0) {
        const d = scene.diagnostics()
        $('mb-diagnostics').textContent =
          `THREE r${d.three_revision} · ${d.assets} ASSETS · ${d.draw_calls} DRAWS`
      }
    },
  })
  scene.select(selectedAsset)
  let initialTimer = setTimeout(() => void planMission({ replan: !!initialExecution }), 40)
  return {
    scene,
    runner,
    world,
    get planner() {
      return planner
    },
    get plan() {
      return plan
    },
    get scenario() {
      return scenario
    },
    get chat() {
      return chat
    },
    navigate,
    planMission,
    ask,
    selectAsset,
    addObstacle,
    action,
    captureExecution: () => {
      runner.pause()
      return runner.snapshot(scenario)
    },
    setSpatialLayers: (snapshot, layers, mission = null, execution = null) => {
      externalExecution = execution
      if (execution) runner.pause()
      scene.setSpatialLayers(snapshot, layers, mission, execution)
    },
    diagnostics: () => ({
      ...scene.diagnostics(),
      state: externalExecution?.status || runner.state,
      completed: externalExecution?.completed_goal_ids || [...runner.completed],
      pose: { ...(externalExecution?.current_pose || runner.pose) },
      plan_status: plan?.status,
      goal_ids: plan?.goal_ids,
      battery_pct: externalExecution?.robot_state?.battery_pct ?? runner.batteryPct,
      payload_kg: externalExecution?.robot_state?.payload_kg ?? runner.cargo,
      scenario_id: scenario,
      current_goal: runner.currentGoal(),
      spatial_execution: externalExecution,
    }),
    dispose() {
      disposed = true
      planSequence++
      clearTimeout(initialTimer)
      clearTimeout(toastTimer)
      cleanups.forEach((f) => f())
      scene.dispose()
      root.innerHTML = ''
      root.classList.remove('mb-lab')
    },
  }
}
