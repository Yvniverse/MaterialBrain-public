import type { AgentToolEvent } from '../types'

export const agentProcessLabels: Record<string, string> = {
  search_components_by_requirement: '核对工程候选器件',
  search_materials: '找到物料',
  get_material_detail: '核对物料资料',
  get_inventory_availability: '核对库存',
  find_material_locations: '核对存放位置',
  get_project_bom: '核对项目 BOM',
  search_projects: '找到项目',
  search_products: '找到产品',
  get_product_bom: '核对单台 BOM',
  analyze_product_build_readiness: '核对构建备料情况',
  analyze_project_bom_stock: '分析 BOM 缺料',
  get_low_stock_materials: '核对低库存',
  propose_inventory_reservation: '准备库存变更确认',
}

export function summarizeAgentProcess(events: AgentToolEvent[]): string {
  const tools = new Set(
    events.filter((event) => event.status === 'success').map((event) => event.tool),
  )
  if (tools.has('propose_inventory_reservation')) return '已准备库存变更建议'
  if (tools.has('analyze_project_bom_stock')) return '已核对项目和缺料情况'
  if (tools.has('get_project_bom')) return '已核对项目和 BOM'
  if (tools.has('analyze_product_build_readiness')) return '已核对产品和备料情况'
  if (tools.has('get_product_bom')) return '已核对产品和单台 BOM'
  if (tools.has('get_low_stock_materials')) return '已核对低库存物料'
  if (tools.has('search_components_by_requirement')) return '已核对工程候选器件'
  if (tools.has('get_inventory_availability') && tools.has('find_material_locations')) {
    return '已核对库存和库位'
  }
  if (tools.has('find_material_locations')) return '已核对物料和库位'
  if (tools.has('get_inventory_availability')) return '已核对库存'
  if (tools.has('search_projects')) return '已核对项目'
  if (tools.has('search_materials')) return '已核对物料'
  return events.length ? '已完成查询' : '等待查询'
}
