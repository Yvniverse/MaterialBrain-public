import { expect, it } from 'vitest'
import { mountWarehouseUI } from '../src/components/locations/digitalTwin/ui.js'
import type { TwinSnapshot } from '../src/components/locations/digitalTwin/types'
import fixture from './fixtures/twin_snapshot.json'

it('initializes the route panel while preserving an exact deep-linked slot', () => {
  const host = document.createElement('div')
  document.body.append(host)
  const data = fixture as unknown as TwinSnapshot
  const asset = data.assets.find(a => a.code === 'PORT-PWR-6')!
  const slot = asset.slots.find(s => s.name === 'L06')!
  const controller = mountWarehouseUI(host, data, { initialMode: '2d', focusLocationId: slot.location_id })
  expect(host.querySelector('[data-ref=slot]')?.textContent).toBe('L06')
  expect(host.querySelector('[data-ref=step]')?.textContent).toMatch(/1 \/ \d/)
  expect(host.querySelector('[data-ref=material]')?.textContent).not.toBe('')
  expect(host.querySelector('[data-ref=taskStatus]')?.textContent).toBe('路径预览')
  const diagnostics: { stats?: Record<string, unknown> } = {}
  host.dispatchEvent(new CustomEvent('warehouse-twin:diagnostics', { detail: diagnostics }))
  expect(diagnostics.stats).toEqual(controller.stats())
  const labels = [...host.querySelectorAll('[data-ref=plan] g')]
  expect(labels).toHaveLength(data.assets.length)
  for (const label of labels) {
    expect(label.querySelector('text')!.textContent!.length).toBeLessThanOrEqual(9)
    expect(label.querySelector('title')!.textContent).toBe(label.getAttribute('aria-label'))
  }
  controller.dispose()
  const disposedDiagnostics = {}
  host.dispatchEvent(new CustomEvent('warehouse-twin:diagnostics', { detail: disposedDiagnostics }))
  expect(disposedDiagnostics).toEqual({})
  expect(host.childElementCount).toBe(0)
  host.remove()
})


it('renders a completed task as completed instead of an active route stop', () => {
  const host = document.createElement('div')
  document.body.append(host)
  const data = structuredClone(fixture) as unknown as TwinSnapshot
  ;(data as unknown as { task: Record<string, unknown> }).task = {
    id: 7,
    pick_task_no: 'PK-COMPLETE',
    status: 'completed',
    allocations: [],
  }
  const controller = mountWarehouseUI(host, data, { initialMode: '2d' })
  expect(host.querySelector('[data-ref=step]')?.textContent).toBe('已完成')
  expect(host.querySelector('[data-ref=taskStatus]')?.textContent).toBe('已完成')
  expect(host.querySelector('[data-ref=taskHeading]')?.textContent).toBe('已完成任务')
  expect((host.querySelector('[data-act=prev]') as HTMLButtonElement).hidden).toBe(true)
  expect((host.querySelector('[data-act=tour]') as HTMLButtonElement).hidden).toBe(true)
  expect((host.querySelector('[data-act=next]') as HTMLButtonElement).hidden).toBe(true)
  controller.dispose()
  host.remove()
})
