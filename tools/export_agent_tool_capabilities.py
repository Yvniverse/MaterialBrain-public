import argparse
import json
from pathlib import Path

from app.agent.tools.registry import ToolRegistry


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--component-intelligence-enabled", action="store_true")
    args = parser.parse_args()

    registry = ToolRegistry(component_intelligence_enabled=args.component_intelligence_enabled)
    payload = {
        "schema_version": 1,
        "tool_count": len(registry.names),
        "mcp_exposed_count": len(registry.mcp_exposed_names),
        "tools": registry.capability_matrix(),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
