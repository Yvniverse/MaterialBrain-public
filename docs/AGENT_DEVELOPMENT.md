# Agent development

The agent layer translates a user request into a typed contract, permitted tool calls, and structured results. Domain services own persisted facts and state transitions.

## Running locally

Follow [CONTRIBUTING](../CONTRIBUTING.md) for dependencies and an isolated PostGIS test database. Keep model credentials server-side. Unit tests use injected providers; enable live provider calls only in a dedicated runtime with an explicit budget and sample data.

The warehouse API accepts requests at `POST /api/v1/agent/query` when `AGENT_ENABLED=true`. Deterministic spatial requests can run without provider credentials. Spatial mission APIs are documented in [Spatial Agent](SPATIAL_AGENT.md). The baseline navigation lab provides deterministic planning independently of provider calls.

## Adding a tool

1. Define a strict input schema with bounds for text, numbers, and list sizes.
2. Implement the handler in `backend/app/agent/tools/` or the relevant spatial service.
3. Register its name, schema, required permission, and handler in the tool registry.
4. Return only fields needed for answers and UI actions. Do not expose database secrets, sessions, or managed file paths.
5. Add behavior tests for valid input, permission failure, ambiguous identifiers, and unavailable data.

Structured entities and UI actions should carry stable identifiers. The browser should navigate from those identifiers rather than parse links from natural-language answers.

## Planning and execution

Entity resolution must stop on unresolved ambiguity. After a unique material, project, location, or dock is resolved, execute the requested deterministic queries directly. An optional model narrative may explain those facts but cannot replace their values.

For spatial requests, retain the map version, plan constraints, and task graph. Creating a mission stores a plan; execution begins through an explicit start operation. Navigation arrival and material handoff are separate task events.

New write behavior needs a dedicated action schema, authorization path, revalidation, and transactional service. Do not put mutations into a general read tool.

## API contract

`POST /api/v1/agent/query` accepts a message, optional conversation ID, a stable client operation ID, and optional navigation context. Responses include an answer, structured entities, grounded facts, tool events, UI actions, and proposal identifiers.

Proposal endpoints support listing, inspection, approval, and rejection. Browser requests use the application session cookie and `X-CSRF-Token`. Errors follow the common `{code,message,details,request_id}` format.

## Inventory and BOM semantics

`available_quantity = quantity - reserved_quantity`. Project BOM `required_quantity` is the line's total demand; it must not be multiplied by a build count without a matching product/build contract.

Location quantities come from inventory lots. A primary location alone identifies a registered location, not an exact quantity there. Partial and inconsistent lot coverage must remain visible. Sample stock and geometry keep their source metadata.

Engineering draft selection changes draft state. Product BOM preview remains a separate read operation; its diff and readiness result do not themselves apply a formal BOM change.

## Validation

Run backend lint/tests and frontend lint/tests/build for changed contracts. Use the [WarehouseBench](WAREHOUSEBENCH.md) task generator, verifier, and replay interfaces for spatial behavior. Inspect observed navigation state and handoff events when changing robot execution; a valid route alone does not verify mission completion.
