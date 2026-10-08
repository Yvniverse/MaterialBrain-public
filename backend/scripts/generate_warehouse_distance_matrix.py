from __future__ import annotations

import json
from pathlib import Path

from app.services.warehouse_routing import distance_matrix, load_warehouse_map

ROOT = Path(__file__).resolve().parents[1]
MAP_PATH = ROOT / "sample_data" / "v2" / "warehouse_map_v1.json"
OUTPUT_PATH = ROOT / "sample_data" / "v2" / "warehouse_distance_matrix_v1.json"


def main() -> None:
    definition = load_warehouse_map(MAP_PATH)
    terminal_nodes = [definition.default_start_node]
    terminal_nodes.extend(binding.pick_node_code for binding in definition.bindings)
    if definition.default_end_node and definition.default_end_node not in terminal_nodes:
        terminal_nodes.append(definition.default_end_node)
    matrix = distance_matrix(definition, terminal_nodes)
    payload = {
        "map_code": definition.map_code,
        "map_version": definition.version,
        "graph_hash": definition.graph_hash,
        "calibration_status": definition.calibration_status,
        "coordinate_unit": definition.coordinate_unit,
        "node_codes": terminal_nodes,
        "matrix_m": matrix,
        "derived": True,
        "source_of_truth": "warehouse_map_v1.json nodes + edges",
    }
    OUTPUT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(OUTPUT_PATH)
    print(definition.graph_hash)


if __name__ == "__main__":
    main()
