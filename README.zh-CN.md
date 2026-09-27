<div align="center">

# MaterialBrain

**面向硬件工程的“工程物料智能 + 仓储工作流”平台**

把确定性工程推理、可追溯 AI 助手、Engineering BOM、Product BOM 只读预览、库存/库位真值和数字孪生拣货整合到一套可自托管系统中。

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Public CI](https://github.com/Yvniverse/MaterialBrain-public/actions/workflows/public-ci.yml/badge.svg)](https://github.com/Yvniverse/MaterialBrain-public/actions/workflows/public-ci.yml)
![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![Vue](https://img.shields.io/badge/Vue-3-42b883?logo=vuedotjs&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.1xx-009688?logo=fastapi&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-17-4169E1?logo=postgresql&logoColor=white)

[English](README.md) · [架构图](docs/architecture/README.md) · [安全策略](SECURITY.md) · [贡献指南](CONTRIBUTING.md)

</div>

## 项目定位

MaterialBrain 试图解决硬件研发里经常被拆散的几类信息：**数据手册证据、工程约束、可用物料、实时库存/库位、产品 BOM 与仓储执行**。

项目最核心的原则是：

> **确定性事实与状态转换归服务端所有；大模型可以规划、解释和组织 grounded 结果，但不能成为库存、选型、BOM 或事务状态的真值来源。**

因此系统不是“给库存系统套一个聊天框”，而是把 TaskContract、有限工具集、Engineering Evidence、Material Provenance、显式 Selection、Completeness、只读 Product BOM Preview、事务库存服务与 Exact-SHA 发布证据串成一条工程链。

## 核心能力

| 模块 | 能力 |
| --- | --- |
| **工程 Agent** | 浮动机器人和 `/agent` 两个入口共享同一结构化服务端实体；多轮上下文以 typed state 保留，而不是完全依赖聊天文本。 |
| **工程证据** | 数据手册页级锚点、结构化参数与 provenance；模型回答不能覆盖服务端确定事实。 |
| **Power / Engineering BOM** | 电源 Rail / Stage / Requirement、确定性损耗、外围约束、候选匹配、显式草案选择与 Completeness。 |
| **Product BOM Preview** | 只读计算 `ADD / UPDATE_QUANTITY / NO_CHANGE / UNRESOLVED`，并做 Apply Readiness Dry-Run，不直接修改正式 BOM。 |
| **库存与库位** | 库存、预留、流水、可视化库位、事务锁、幂等与审计。 |
| **数字孪生与拣货** | 仓库空间图、路径规划、PickTask / Allocation 与 Guided Picking。 |
| **发布治理** | RBAC、CSRF/Session、备份清单、Project Memory Guard、Exact-SHA runtime 与 Qualification Gate。 |

## 效果图

<table>
<tr>
<td width="50%"><img src="docs/assets/screenshots/engineering-bom-selection.png" alt="Engineering BOM 选型"></td>
<td width="50%"><img src="docs/assets/screenshots/product-bom-preview.png" alt="Product BOM Preview"></td>
</tr>
<tr>
<td align="center"><b>Grounded Engineering BOM</b><br/>Requirement → Candidate → 显式草案选择 → Completeness</td>
<td align="center"><b>Product BOM Preview</b><br/>只读 Diff、阻塞原因、来源与 Apply Readiness</td>
</tr>
<tr>
<td width="50%"><img src="docs/assets/screenshots/dual-surface-agent.png" alt="双入口工程 Agent"></td>
<td width="50%"><img src="docs/assets/screenshots/warehouse-twin.png" alt="仓库数字孪生"></td>
</tr>
<tr>
<td align="center"><b>双入口，共享服务端真值</b></td>
<td align="center"><b>数字孪生与 Guided Picking</b></td>
</tr>
</table>

## 运行时架构

<p align="center">
  <img src="docs/architecture/previews/01-runtime-overview.svg" alt="MaterialBrain 运行时架构" width="100%">
</p>

MaterialBrain 采用浏览器 + Nginx + Vue 3 + FastAPI + PostgreSQL 的自托管结构。工程 Agent 运行在受约束的服务端边界中，Qwen 只参与有限的规划/解释；库存、选型、损耗、BOM diff 与业务写入仍由确定性服务负责。

[`docs/architecture/`](docs/architecture/README.md) 中还提供 5 张详细图：后端子系统、Agent 工作流、工程智能数据流、仓库孪生/拣货以及 Exact-SHA 发布生命周期。

## 一条典型工程链

```text
真实工程问题
   ↓
工作点 / Typed Requirement
   ↓
Engineering Evidence + Material Attributes / Provenance
   ↓
Component Class Gate + Constraint Matcher
   ↓
Grounded Candidates
   ↓
用户显式 Engineering Draft Selection
   ↓
Completeness / Blocking Reasons
   ↓
只读 Product BOM Preview
   ↓
Apply Readiness Dry-Run（仍然不写正式 BOM）
```

## 真实生产风格问题示例

```text
12V 输入，先 Buck 到 5V，再 LDO 到 3.3V，负载 100mA。
把各级损耗、Engineering BOM 完整度和 LM5164 候选库存/库位一起给我。
```

```text
LM5164 的自举电容需要 2.2nF、耐压至少 50V、X7R。
把满足条件的真实物料按规格、库存、库位和证据列出来，先不要替我选。
```

```text
把当前 Engineering Draft 预览到 PROD-DEXGRIP / EVT-R1，
分别告诉我会新增什么、哪些单台用量会变化、哪些不变、哪些仍未解决；不要实际应用。
```

## 技术栈

- **Backend**：Python 3.12、FastAPI、SQLAlchemy、Alembic、PostgreSQL 17
- **Agent**：确定性 TaskContract / Tool Registry + LangGraph 风格编排 + Qwen Model Pool
- **Frontend**：Vue 3、TypeScript、Pinia、Element Plus、ECharts、Three.js
- **Testing**：pytest、Ruff、Vitest、Playwright、deterministic golden suites
- **Deployment**：Docker Compose、Nginx、Fresh Backup、Exact-SHA runtime metadata

## 快速启动

```bash
git clone https://github.com/Yvniverse/MaterialBrain-public.git
cd MaterialBrain
cp .env.example .env
# 修改数据库强密码；如需 Agent，再配置服务端模型 Key

docker compose up -d --build
docker compose exec backend alembic upgrade head
docker compose exec backend python scripts/create_admin.py
```

浏览器访问 `http://localhost`（或 `WEB_PORT` 指定端口）。核心库存/仓储功能不依赖大模型；没有模型 Key 时可以保持 Agent 关闭。

## 公开里程碑边界

当前公开版本建议坚持以下边界：

- Engineering Draft Selection ≠ 正式 Product BOM 写入；
- Product BOM Preview / Apply Dry-Run 仍为只读；
- 库存写操作必须经过事务服务与权限检查；
- demo/synthetic 的库存/库位必须保持 provenance 可见；
- 任何 UAT fixture 都不得写入默认生产数据库；
- Release Evidence 必须绑定同一 exact SHA。

## Roadmap

- [x] Grounded Engineering Agent
- [x] Engineering Evidence + Material Provenance
- [x] Power Architecture / Multi-rail Engineering BOM
- [x] Explicit Selection Truth + Completeness
- [x] Read-only Product BOM Preview
- [x] Apply Readiness Dry-Run
- [x] Warehouse Twin / Guided Picking 基础链路
- [ ] 完整的电源 BOM readiness 与更多被动器件 derating
- [ ] Preview → Human Confirm → Transactional Product BOM Apply

## License

本项目计划以 **Apache License 2.0** 发布。详见 [`LICENSE`](LICENSE) 与 [`NOTICE`](NOTICE)。

依赖库、厂商数据手册、第三方 Logo/素材及外部证据仍遵循各自许可证或再分发条款，不能因为 MaterialBrain 使用 Apache-2.0 就自动被重新许可。
