# Architecture Source Map

This map records the code areas used to derive the public architecture diagrams. It deliberately uses repository-relative paths so the documentation remains portable.

## 01 — Runtime Overview

| Diagram element | Main source paths |
| --- | --- |
| Nginx / single entry | `nginx/`, `docker-compose.yml` |
| Vue frontend | `frontend/src/`, `frontend/src/router/` |
| FastAPI / RBAC | `backend/app/main.py`, `backend/app/api/`, `backend/app/auth.py` (or current auth modules) |
| Agent runtime | `backend/app/agent/service.py`, `backend/app/agent/graph.py` |
| Domain services | `backend/app/services/` |
| Read-only MCP | `backend/app/mcp/` |
| Qwen model pool | `backend/app/llm/qwen.py`, `backend/app/llm/model_pool.py` |
| PostgreSQL domain truth | `backend/app/models/domain.py`, `backend/app/db.py`, `backend/alembic/` |
| Persistent evidence/files | `docker-compose.yml`, `storage/` runtime mounts |
| Backup / restore | `deploy/backup/`, `deploy/restore/` |

## 02 — Backend Subsystems

| Area | Main source paths |
| --- | --- |
| TaskContract / intent scope | `backend/app/agent/task_contract.py`, `backend/app/agent/intent_scope.py` |
| Agent graph | `backend/app/agent/graph.py`, `backend/app/agent/state.py` |
| Tool registry | `backend/app/agent/tools/registry.py`, `backend/app/agent/tools/` |
| Public response boundary | `backend/app/agent/public_result_contract.py`, `backend/app/agent/output_boundary.py`, `backend/app/agent/response_composer.py` |
| Engineering research | `backend/app/services/engineering_research.py` |
| Power + Engineering BOM | `backend/app/services/power_design.py`, `backend/app/services/peripheral_bom.py`, `backend/app/power_design/` |
| Engineering evidence | `backend/app/services/engineering_evidence.py`, `backend/app/component_intelligence/` |
| Provenance | `backend/app/services/data_provenance.py` |
| Product BOM Preview | `backend/app/services/product_bom_preview.py` |
| Warehouse maps | `backend/app/services/warehouse_maps.py`, `backend/app/services/warehouse_routing.py` |
| Picking | `backend/app/services/picking.py`, `backend/app/services/picking_planner.py` |
| Inventory transaction boundary | `backend/app/services/inventory.py` |

## 03 — Agent Runtime Workflow

- `backend/app/agent/service.py`
- `backend/app/agent/task_contract.py`
- `backend/app/agent/graph.py`
- `backend/app/agent/tools/registry.py`
- `backend/app/agent/conversation.py`
- `backend/app/agent/episode.py`
- `backend/app/agent/public_result_contract.py`
- `backend/app/llm/model_pool.py`
- `frontend/src/views/AgentWorkbench.vue`
- floating agent components under `frontend/src/components/agent/`

## 04 — Engineering Intelligence Dataflow

- `backend/app/services/engineering_research.py`
- `backend/app/services/engineering_evidence.py`
- `backend/app/services/data_provenance.py`
- `backend/app/services/power_design.py`
- `backend/app/services/peripheral_bom.py`
- `backend/app/services/product_bom_preview.py`
- `backend/app/component_intelligence/`
- `backend/app/models/domain.py`
- `frontend/src/components/agent/PowerArchitectureOptions.vue`

## 05 — Warehouse Twin and Guided Picking

- `frontend/src/views/WarehouseTwin.vue`
- `frontend/src/views/PickingOperator.vue`
- `backend/app/services/warehouse_maps.py`
- `backend/app/services/warehouse_routing.py`
- `backend/app/services/picking.py`
- `backend/app/services/picking_planner.py`
- `backend/app/services/inventory.py`
- `backend/app/models/domain.py`

## 06 — Release Lifecycle

- `.github/workflows/public-ci.yml`
- private release qualification is documented conceptually; private runner configuration is intentionally not shipped
- `tools/project_context.py`
- `tools/project_qualification.py`
- `deploy/`
- backup/restore tooling and browser-evidence harnesses
