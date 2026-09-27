# Warehouse Agent 开发指南

## 本地启动

复制 `.env.example` 为 `.env`。不需要测试 Agent 时保持 `AGENT_ENABLED=false`；需要联调时在仅供本机使用的 `.env` 中设置 `AGENT_ENABLED=true` 和 `DASHSCOPE_API_KEY`。密钥不得放进源码、前端环境变量、测试快照或提交记录。

安装并验证：

```powershell
cd backend
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\alembic upgrade head
.venv\Scripts\python -m ruff check .
.venv\Scripts\python -m pytest

cd ..\frontend
pnpm install --frozen-lockfile
pnpm run lint
pnpm test
pnpm run build
pnpm run e2e
```

单元测试使用注入的 fake LLM provider，不需要联网和真实 Key。应至少覆盖 tool call 回路、消歧、库存/BOM 语义、Proposal 未审批零写入、整批预留失败回滚、重复审批和 provider 超时。

Golden Set v1 是不可变评测资产；runner 会在每次运行前校验固定 SHA-256 和 75 个唯一 case。确定性全量回归命令为：

```powershell
cd backend
.venv\Scripts\python -m evals.run_golden_eval --mode deterministic --output-dir eval-results
```

真实 Qwen 只允许在显式设置 `EVAL_RUN_REAL_QWEN=true`、提供 Key，并把 `EVAL_DATABASE_URL` 指向名称含 `eval`、`test` 或 `golden` 的隔离 PostgreSQL 数据库时运行。不得把正式库用于评测 seed/reset。

Phase 1.6 推荐使用 Compose 隔离 profile；不要为了评测修改生产 `DASHSCOPE_MODEL`：

```powershell
docker compose --profile eval up -d db_eval
docker compose --profile eval run --rm -e EVAL_RUN_REAL_QWEN=true eval_runner `
  python -m evals.run_model_benchmark --through-stage full
```

`EVAL_FREE_MODEL_ALLOWLIST` 为空时只能调用 `EVAL_MODEL`；显式 allowlist 仍限制为最多 8 个模型并顺序执行。每个 Real-Qwen case 会重置隔离数据库并只装载该 case 声明的 fixture，项目 fixture 自动装载 BOM 依赖。额度耗尽标记为 `SKIPPED_QUOTA`，不能算作模型质量失败。真实模型 E2E 默认跳过，只有设置 `RUN_REAL_QWEN_E2E=true` 并提供独立登录测试凭据时才执行。

## 添加一个只读工具

1. 在 `backend/app/schemas/agent.py` 定义严格的参数模型，并设置长度、范围和列表数量上限。
2. 在 `backend/app/agent/tools/` 实现查询；只返回回答和 UI 所需的最小字段，不返回密码、会话、附件路径等内部数据。
3. 在 `ToolRegistry` 显式注册名称、说明、参数 schema、所需权限和 handler。未注册的模型调用必须保持拒绝。
4. 若返回可导航实体，增加结构化 `entity_key` / `AgentUIAction`，不要让前端解析自然语言链接。
5. 更新系统 prompt 的工具选择规则和事实守卫，并加入 fake-provider 测试。

添加写能力时不要直接扩展普通工具。先定义受支持的 Proposal action schema，再在 policy、guard 和专用业务服务中实现服务端重验；最终状态和业务写入必须处于同一事务，并继续调用既有领域服务。

## API 快速参考

- `POST /api/v1/agent/query`：提交 `{ "message": "...", "client_operation_id": "稳定的客户端操作 ID" }`，返回答案、确定性 facts、有限遥测、实体、工具事件、UI actions、Proposal IDs 与 request ID。
- `GET /api/v1/agent/proposals?status=pending`：有库存操作权限者可看全部；其他用户只看自己创建的记录。
- `GET /api/v1/agent/proposals/{id}`：查看单项。
- `POST /api/v1/agent/proposals/{id}/approve`：需 `inventory:operate`，批准并原子执行。
- `POST /api/v1/agent/proposals/{id}/reject`：创建者或库存操作员可拒绝，body 可含 `reason`。

浏览器调用沿用系统的 Cookie 会话与 `X-CSRF-Token`，不要另建 bearer token。统一错误响应含业务错误码和 request ID；前端应根据 Agent 禁用、缺 Key、超时和上游错误显示友好提示，不展示异常堆栈。

## BOM 与库存语义

项目 BOM 的 `required_quantity` 已是该 BOM 行的总需求，Agent 不得擅自再乘项目数量。项目缺料按以下口径计算：

```text
缺料 = max(总需求 - 当前可用库存 - 当前项目已预留数量, 0)
```

`quantity` 为账面库存，`reserved_quantity` 为总预留，`available_quantity = quantity - reserved_quantity`。库位数量只来自 lot 证据；主库位字段仅证明登记路径，不证明该路径中的数量。物料账面量与 lot 汇总相等为 `complete`，lot 仅覆盖部分账面量为 `partial`，负数或 lot 汇总超过账面量为 `inconsistent`。不一致结果不得生成精确库位数量叙述或可视化聚焦。

## 人工验收建议

使用至少两个相似 MPN、一个多库位物料、一个包含足量与缺料行的项目 BOM 验证：模糊查询先消歧；库存与库位来自实时工具；点击“在库位中查看”能打开对应盒/货架并高亮；Proposal 创建后库存不变；无 `inventory:operate` 不显示可用批准按钮且 API 返回 403；批准后每个物料恰好产生一次预留流水；任一物料不足时整批不变；关闭 Agent 或移除 Key 后其他页面仍正常。
