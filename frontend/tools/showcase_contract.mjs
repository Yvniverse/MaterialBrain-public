export const requiredShots = [
  { name: '01-warehouse-twin.png', route: '/warehouse-twin', kind: 'warehouse', width: 1728, height: 1080 },
  { name: '02-embodied-lab.png', route: '/warehouse-twin?workspace=robot-lab', kind: 'laboratory', width: 1728, height: 1080 },
  { name: '03-material-brain.png', route: '/agent', kind: 'agent', width: 1600, height: 1000 },
  { name: '04-dashboard.png', route: '/dashboard', kind: 'dashboard', width: 1600, height: 1000 },
  { name: '05-storage-equipment.png', route: '/locations', kind: 'storage', width: 1600, height: 1000 },
  { name: '06-task-recovery.png', route: '/warehouse-twin?workspace=robot-lab', kind: 'recovery', width: 1600, height: 1000 },
]

export const responsiveViewports = [
  { width: 390, height: 844 },
  { width: 768, height: 1024 },
  { width: 1024, height: 900 },
  { width: 1440, height: 900 },
  { width: 1728, height: 1080 },
  { width: 1920, height: 1080 },
]

export function assertRuntimeIdentity(runtime, sourceCommit) {
  const sha = /^[a-f0-9]{40}$/
  if (!sha.test(sourceCommit)) throw new Error('Source commit must be a full Git revision')
  if (!sha.test(runtime.frontend_build_sha) || !sha.test(runtime.backend_build_sha)) {
    throw new Error('The actual frontend and backend must report full build revisions')
  }
  if (runtime.frontend_build_sha !== sourceCommit || runtime.backend_build_sha !== sourceCommit) {
    throw new Error('Frontend, backend and source revisions differ')
  }
}

export function assertRenderedScene(scene, kind) {
  if (!scene || scene.renderer !== 'THREE.WebGLRenderer') {
    throw new Error('The production Three.js WebGL renderer is not ready')
  }
  const draws = scene.drawCalls ?? scene.draw_calls ?? 0
  const assets = scene.assetCount ?? scene.assets ?? 0
  if (draws < 10 || scene.triangles < 100 || assets < 3) {
    throw new Error('The scene has not rendered the warehouse geometry')
  }
  if (scene.cameraInsideAsset === true) throw new Error('The camera is inside warehouse equipment')
  if (kind === 'warehouse' && scene.selectedCoverage?.area === 0) {
    throw new Error('The selected warehouse equipment is outside the rendered frame')
  }
  if (kind !== 'warehouse' && (!Number.isFinite(scene.frames) || scene.frames < 1)) {
    throw new Error('The laboratory has not rendered an actual frame')
  }
}

export function assertLayout(metrics, hasScene) {
  const issues = []
  if (metrics.documentWidth > metrics.viewport.width + 2) issues.push('Horizontal document overflow')
  if (!metrics.shellLight) issues.push('The Glacier shell is missing or has a dark background')
  if (metrics.obscuredPrimaryControls?.length) issues.push('A primary action is obscured')
  if (metrics.overlappingBotControls?.length) issues.push('The floating robot overlaps an action')
  if (hasScene) {
    const stage = metrics.stage
    if (!stage || stage.width < Math.min(300, metrics.viewport.width - 32) || stage.height < 280) {
      issues.push('The WebGL stage is too small')
    } else {
      const fraction = stage.visibleArea / (metrics.viewport.width * metrics.viewport.height)
      if (metrics.viewport.width >= 1440 && fraction < 0.24) issues.push('The WebGL stage is not a desktop focal area')
      if (metrics.viewport.width >= 1024 && stage.top >= metrics.viewport.height * 0.6) {
        issues.push('The WebGL stage starts too far below the first viewport')
      }
    }
  }
  if (issues.length) throw new Error(issues.join('; '))
}

export function assertCaptureEvidence(kind, evidence) {
  if (evidence.kind !== kind || evidence.verified !== true) throw new Error('Missing verified UI evidence')
  if (['warehouse', 'laboratory', 'recovery'].includes(kind)) assertRenderedScene(evidence.scene, kind)
  if (kind === 'warehouse' && !evidence.selected_equipment) throw new Error('Select actual warehouse equipment')
  if (kind === 'laboratory' && (evidence.scene.goal_ids?.length ?? 0) < 2) {
    throw new Error('Show a real multi-stop route in the laboratory')
  }
  if (kind === 'agent' && !evidence.result_selector) throw new Error('The Agent has no actual grounded result')
  if (kind === 'dashboard' && !evidence.summary_values?.some(value => Number(value.replace(/[^\d.]/g, '')) > 0)) {
    throw new Error('The dashboard is empty')
  }
  if (kind === 'storage' && (!evidence.equipment_selector || !evidence.compartment_selector)) {
    throw new Error('Open an actual storage device and compartment')
  }
  if (kind === 'recovery') {
    const execution = evidence.scene?.spatial_execution
    if (!execution || execution.execution_boundary !== 'ros2_nav2_simulation' || execution.hardware_control !== false) {
      throw new Error('Recovery requires the actual server Nav2 simulation execution')
    }
    const events = new Set(evidence.recovery_events?.map(event => event.type) || [])
    if (!events.has('handoff_verified') || !events.has('obstacle_added') || (!events.has('replanning') && !events.has('recovery'))) {
      throw new Error('The observed Nav2 handoff, obstacle and recovery trace is missing')
    }
    if (!execution.completed_goal_ids?.length) throw new Error('The recovery must preserve a verified handoff')
    const source = evidence.recovery_source
    if (source?.kind !== 'server_episode' || source.mission_id !== execution.mission_id
      || source.source !== 'observed_execution_events'
      || source.execution_boundary !== 'ros2_nav2_simulation' || source.inventory_written !== false
      || source.path !== `/api/v1/spatial/missions/${encodeURIComponent(execution.mission_id)}/episode`
      || !/^[a-f0-9]{64}$/.test(source.response_sha256 || '')) {
      throw new Error('Recovery must be grounded in this mission server episode response')
    }
    if (evidence.recovery_events.some(event => event.mission_id !== execution.mission_id
      || !Number.isInteger(event.sequence) || !event.event_id || !event.timestamp
      || event.details?.execution_boundary !== 'ros2_nav2_simulation'
      || event.details?.hardware_control !== false)) {
      throw new Error('The recovery event identity or Nav2 boundary is invalid')
    }
  }
}

export function episodeRecoveryEvidence(episode, response) {
  if (episode?.mission_id !== response.mission_id || episode?.source !== 'observed_execution_events'
    || episode?.execution_boundary !== 'ros2_nav2_simulation' || episode?.inventory_written !== false
    || !Array.isArray(episode.steps)) throw new Error('The actual server episode is missing or belongs to another mission')
  const types = new Set(['handoff_verified', 'obstacle_added', 'obstacle_removed', 'replanning', 'recovery'])
  const events = episode.steps.filter(step => step.source === 'observed_execution_event' && types.has(step.outcome?.type))
    .map(step => {
      const event = step.outcome
      return {
        event_id: event.event_id, mission_id: event.mission_id, sequence: event.sequence,
        timestamp: event.timestamp, type: event.type, goal_id: event.goal_id,
        details: {
          execution_boundary: event.details?.execution_boundary, hardware_control: event.details?.hardware_control,
          source: event.details?.source, completed_goal_ids: event.details?.completed_goal_ids,
        },
      }
    })
  return {
    recovery_source: {
      kind: 'server_episode', ...response, source: episode.source,
      execution_boundary: episode.execution_boundary, inventory_written: episode.inventory_written,
      episode_id: episode.episode_id, map_revision: episode.map_revision,
      observed_event_count: episode.final_metrics?.observed_event_count,
    },
    recovery_events: events,
  }
}
