# Warehouse Agent 架构

## 目标与边界

Phase 1 把自然语言作为现有仓库系统的受控入口，而不是创建第二套库存逻辑。Agent 可检索物料、实时库存、物理库位、低库存和项目 BOM；唯一写意图是创建库存预留 Proposal。模型、工具层和前端都不能绕过 `InventoryService` 直接修改 `materials.quantity`、`materials.reserved_quantity` 或项目预留记录。

```text
AgentWorkbench
  -> POST /api/v1/agent/query (session + CSRF + material:view)
  -> WarehouseAgentService
  -> LangGraph: prepare -> Qwen -> approved tool -> Qwen -> normalize
  -> SQLAlchemy read tools / ProposalService

Proposal pending
  -> human approve (inventory:operate)
  -> lock proposal + policy re-check + lock all materials
  -> InventoryService.reserve_batch
  -> movements + reservations + proposal executed + audit
  -> one database commit
```

## Agent 运行时

`app/agent/graph.py` 使用 LangGraph 组织有限状态流程。Qwen 通过 DashScope 的 OpenAI 兼容端点调用，使用原生 `tools` / `tool_calls` 协议；不解析 Markdown JSON。每次工具调用都经过白名单、Pydantic 参数校验和 RBAC 校验，最多执行 `AGENT_MAX_TOOL_ROUNDS` 轮，并受 `AGENT_TOTAL_DEADLINE_SECONDS` 总时限控制。non-thinking 模式对明确的库存、库位与 BOM 事实问题启用确定性证据策略；所需事实工具成功后立即恢复 `tool_choice=auto`。权限错误是终止状态，不再把错误返回模型继续规划。

响应只暴露简短答案、结构化实体、`facts`、UI action、工具事件和有限的调用遥测。工具事件包含工具名、状态、摘要与耗时，不包含模型隐藏推理；遥测只包含 provider、model、finish reason、token 数、耗时和工具调用数。关键事实由 `GroundedResponseComposer` 从工具结果确定性生成，模型文本只能作为非关键叙述；一旦有结构化事实，冲突的模型文本会被丢弃。实时库存、库位和 BOM 类问题若没有对应工具的成功证据，事实守卫会拒绝输出猜测结果。模糊物料或项目会先返回候选项供用户确认。

输入安全守卫在调用模型前拒绝密钥提取、任意 SQL、删除数据库、绕过权限以及未支持的写操作。BOM 查询遇到多个版本且用户未明确版本时返回 `BOM_VERSION_REQUIRED`，不存在的版本返回 `BOM_VERSION_NOT_FOUND`，不得自动挑选“看起来最新”的版本。

## 工具白名单

| 工具 | 数据来源 | 权限 | 写入 |
|---|---|---|---|
| `search_materials` | materials | `material:view` | 否 |
| `get_material_detail` | materials/categories | `material:view` | 否 |
| `get_inventory_availability` | materials | `material:view` | 否 |
| `find_material_locations` | inventory lots + location tree + material ledger | `material:view` | 否 |
| `get_low_stock_materials` | materials | `material:view` | 否 |
| `search_projects` | projects | `project:manage` | 否 |
| `get_project_bom` | project BOM | `project:manage` | 否 |
| `analyze_project_bom_stock` | BOM + current availability + project reservations | `project:manage` | 否 |
| `propose_inventory_reservation` | action proposals | `project:manage` + `inventory:operate` | 仅 Proposal |

工具返回最小必要字段。库位工具把物料账面数量与全部 lot 汇总进行对账，并返回 `complete`、`partial` 或 `inconsistent`。主库位可以作为“已登记路径”展示，但没有 lot 证据时不得把账面数量推断到该库位；出现负数或 lot 总量超过账面量时标为不一致，抑制精确数量断言、自动聚焦与库位高亮。只有一致的实际正库存 lot 才附带可视化容器/格口 ID，前端将其转换为 `/locations?focus=<location_id>`。

## Proposal 状态机与事务

状态为 `pending -> executed | rejected | failed | expired`。创建时保存动作类型、规范化 payload、原因、创建者、request ID、`client_operation_id`、payload hash 和 24 小时失效时间，不改库存。数据库唯一约束 `(created_by_id, client_operation_id, action_type)` 与规范化 hash 一起实现业务幂等：同一操作重试返回原 Proposal，复用同一操作 ID 但 payload 不同则明确冲突。

批准由 API、`ApprovalPolicy` 与 `ActionGuard` 三层复核：调用者需 `inventory:operate`，Proposal 必须 pending 且未过期，目前只接受 `reserve_inventory`，项目与全部物料必须存在，每个数量必须为正且不超过最新可用量。执行时按物料 ID 稳定排序加行锁，先验证整批，再更新预留、写项目预留和库存流水。Proposal 状态、库存结果与审计记录在同一事务提交；任一物料失败则库存零写入，Proposal 记录失败原因。重复批准已离开 pending 的 Proposal 会被拒绝。

## 安全与故障隔离

- API Key 仅从后端环境变量读取，不进入 API 响应或前端 bundle。
- backend 通过独立 `llm_egress` bridge 访问模型；生产 db 与 backup 只连接 `internal`，PostgreSQL 不发布宿主机端口。
- Agent 默认关闭；缺 Key、禁用、provider 错误、限流或超时都返回受控错误，不影响 FastAPI 启动和其他模块。
- Agent API 沿用 HttpOnly session、CSRF、request ID、统一异常格式和后端 RBAC。
- 读工具没有库存写权限；Proposal 工具没有执行权限；审批端点不能接受模型自由文本作为 SQL 或动作类型。
- Prompt injection 不能扩展工具白名单或权限。工具参数永远按 schema 校验，数据库访问使用 ORM 条件而不是拼接 SQL。
- 所有 Proposal 创建、批准、执行、拒绝和失败均写审计日志；库存结果继续由既有库存流水追溯。
- Agent 工作台与全局浮动机器人共用同一个 composable 状态、稳定的 `client_operation_id` 和审批代码路径，避免两个入口产生不同安全语义。

## 配置与降级

`AGENT_ENABLED=false` 是安全默认值。启用需要同时设置有效的 `DASHSCOPE_API_KEY`；Phase 1 只接受 `LLM_PROVIDER=qwen`。单次 Provider 请求支持 1/2/4 秒有限退避，但始终受单次超时和整个 Agent deadline 约束。关闭 Agent 后，工作台显示可恢复的配置提示，物料、库存、库位、项目等原页面照常工作。真实评测使用 Compose `eval` profile 的独立 PostgreSQL 与 allowlist，不自动修改生产模型。
