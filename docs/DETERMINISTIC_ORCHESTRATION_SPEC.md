# Phase 1.7 Deterministic Orchestration Specification

## Problem shown by Phase 1.6

The real qwen-plus run had 100% tool-argument validity but much lower tool-selection coverage.
The main waste/failure pattern was asking the LLM repeatedly which obvious next fact tool to call.

Example:

```text
User: STM32F405RGT6 还有多少？
LLM -> search_materials
unique material resolved
LLM -> get_inventory_availability
LLM -> final answer
```

The second model decision is unnecessary after the entity and requested fact are deterministic.

## Introduce a Task Contract

Add a small server-side representation, e.g.:

```python
class TaskContract(BaseModel):
    entity_kind: Literal["material", "project", "global", "unknown"]
    requested_facts: set[Literal[
        "material_identity",
        "inventory",
        "location",
        "low_stock",
        "project_bom",
        "bom_stock",
    ]]
    write_intent: Literal["none", "reserve_inventory", "unsupported_write"]
    capability_limitations: set[Literal[
        "build_quantity_not_supported",
        "opened_package_not_supported",
    ]]
```

Do not use this as a hidden reasoning transcript. It is a deterministic execution contract.

## Detection

Use deterministic lexical/rule parsing for high-confidence patterns and the current safety layer.

Examples:

```text
"在哪里/放哪/库位/位置"
→ location

"库存/多少/还有/可用/数量"
→ inventory

"缺料/够不够/库存够吗"
+ project-like context
→ bom_stock

"BOM/物料清单"
+ project-like context
→ project_bom

"生产 N 台/做 N 台/按 N 台"
+ project/product-like context
→ build_quantity_not_supported

"开封/拆封/开封盘"
→ opened_package_not_supported
```

Project-like context must not rely only on the literal word “项目”.
If `search_projects` finds an exact project code/name, treat it as project context.

## Tool-schema pruning

Change ToolRegistry to support:

```python
registry.schemas(allowed_names={...})
```

Do not send all tool schemas on every LLM round.

Examples:

Material inventory query initial schemas:
- `search_materials`

After unique material resolution:
- no LLM tool selection needed; execute inventory deterministically.

Project BOM query initial schemas:
- `search_projects`

After unique project:
- server executes `get_project_bom` or `analyze_project_bom_stock` according to TaskContract.

Reservation intent:
- only expose proposal tool after project/material resolution and permission checks.

## Deterministic follow-up

After a unique entity is resolved, execute required read-only fact tools server-side:

```text
unique material + inventory requested
→ get_inventory_availability(material_id)

unique material + location requested
→ find_material_locations(material_id)

unique project + project_bom requested
→ get_project_bom(project_id, version)

unique project + bom_stock requested
→ analyze_project_bom_stock(project_id, version)
```

Do not ask the model to rediscover obvious IDs.

## Ambiguity

If search returns >1 unresolved candidates:
- stop;
- return candidate UI;
- do not execute entity-specific fact tools.

## Capability limitation

For build-count requests:
- resolve project;
- get current Project BOM if useful;
- explain that current Project BOM is total demand and cannot be multiplied by build count.
- do not call inventory/BOM stock analysis unless the user explicitly asks about stock.

For opened-package requests:
- resolve relevant project/material context as appropriate;
- explain the current InventoryLot data model limitation;
- never invent an opened reel/package.

## Narrative generation

After deterministic facts are assembled, the final LLM call should normally have **no tools**.
Use GroundedResponseComposer for critical facts.

If a deterministic response is sufficient (simple inventory/location/limitation), allow skipping
the final narrative LLM call entirely or use a very short template. Benchmark both variants.

## Targets

After Phase 1.7:
- tool argument validity: 100%
- grounding: 100%
- ambiguity safety: 100%
- P0 hard failure: 0
- average LLM tool-decision rounds materially lower than Phase 1.6
- input tokens/case reduced by at least 40% vs qwen-plus Phase 1.6 P0 baseline
