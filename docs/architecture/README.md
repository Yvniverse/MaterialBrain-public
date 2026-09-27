# MaterialBrain Architecture Atlas

This directory contains a six-diagram architecture atlas generated from the MaterialBrain source tree with Archify.

The diagrams are intentionally split by concern instead of forcing every subsystem into one unreadable graph.

| # | Diagram | Purpose | Interactive HTML |
| --- | --- | --- | --- |
| 01 | Runtime Overview | Browser → Nginx → Vue/FastAPI → Agent/Services → durable state and Qwen boundary | [`01-runtime-overview.html`](html/01-runtime-overview.html) |
| 02 | Backend Subsystems | Agent orchestration, engineering intelligence, warehouse execution and transaction truth | [`02-backend-subsystems.html`](html/02-backend-subsystems.html) |
| 03 | Agent Request Workflow | Auth, TaskContract, routing, allowlisted tools, model boundary and trace state | [`03-agent-runtime.html`](html/03-agent-runtime.html) |
| 04 | Engineering Intelligence | Requirement → evidence/material grounding → selection/completeness → BOM preview/dry-run | [`04-engineering-intelligence.html`](html/04-engineering-intelligence.html) |
| 05 | Warehouse + Picking | Warehouse twin, routing, readiness, pick planning and transactional inventory | [`05-warehouse-picking.html`](html/05-warehouse-picking.html) |
| 06 | Release Lifecycle | Local verify → candidate → exact-SHA runtime → CI/evidence → release-ready qualification | [`06-release-lifecycle.html`](html/06-release-lifecycle.html) |

## README preview

![MaterialBrain Runtime Overview](previews/01-runtime-overview.svg)

## Editable sources

Archify source specifications live under [`specs/`](specs/). They are the editable architecture source of truth for these visuals.

The editable specs are validated before the public tag. Final release preparation also performs a visual inspection of every SVG/HTML output. When local `file://` navigation is restricted, the HTML atlas can be served over localhost for visual checking.

## Traceability

[`SOURCE_MAP.md`](SOURCE_MAP.md) maps each diagram area back to the relevant MaterialBrain source paths. The diagrams are explanatory architecture documentation, not a substitute for the code or schemas.
