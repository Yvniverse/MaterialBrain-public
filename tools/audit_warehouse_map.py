#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo-root', type=Path, default=Path.cwd())
    args = parser.parse_args()
    repo = args.repo_root.resolve()
    backend = repo / 'backend'
    sys.path.insert(0, str(backend))

    from app.services.warehouse_routing import (  # noqa: PLC0415
        distance_matrix,
        load_warehouse_map,
        optimize_route,
    )

    data = backend / 'portfolio_demo_data' / 'v2'
    definition = load_warehouse_map(data / 'warehouse_map_v1.json')
    failures: list[str] = []

    expected_organizers: set[str] = set()
    for name in ('v1_demo_locations.json', 'demo_locations_extension.json'):
        payload = json.loads((data / name).read_text(encoding='utf-8'))
        expected_organizers.update(item['code'] for item in payload['organizers'])
    actual_organizers = {item.location_code for item in definition.bindings}
    if actual_organizers != expected_organizers:
        failures.append(
            f'organizer bindings mismatch: missing={sorted(expected_organizers-actual_organizers)} '
            f'extra={sorted(actual_organizers-expected_organizers)}'
        )

    matrix_payload = json.loads((data / 'warehouse_distance_matrix_v1.json').read_text(encoding='utf-8'))
    if matrix_payload.get('graph_hash') != definition.graph_hash:
        failures.append('distance matrix graph_hash is stale')
    recomputed = distance_matrix(definition, matrix_payload['node_codes'])
    if matrix_payload.get('matrix_m') != recomputed:
        failures.append('distance matrix values differ from graph-derived Dijkstra values')

    examples_payload = json.loads((data / 'warehouse_route_examples_v1.json').read_text(encoding='utf-8'))
    if examples_payload.get('graph_hash') != definition.graph_hash:
        failures.append('route examples graph_hash is stale')
    for example in examples_payload.get('examples', []):
        route = optimize_route(
            definition,
            example['stop_nodes'],
            closed_edge_codes=set(example.get('closed_edge_codes') or []),
        )
        if list(route.ordered_stop_nodes) != example['ordered_stop_nodes']:
            failures.append(f"{example['code']}: ordered stops changed")
        if route.total_distance_m != example['total_distance_m']:
            failures.append(f"{example['code']}: route distance changed")

    if definition.calibration_status != 'demo_synthetic':
        failures.append('checked-in generated map must remain demo_synthetic until replaced by measured data')

    report = {
        'map_code': definition.map_code,
        'graph_hash': definition.graph_hash,
        'calibration_status': definition.calibration_status,
        'node_count': len(definition.nodes),
        'edge_count': len(definition.edges),
        'organizer_binding_count': len(definition.bindings),
        'expected_organizer_count': len(expected_organizers),
        'distance_matrix_nodes': len(matrix_payload['node_codes']),
        'route_example_count': len(examples_payload.get('examples', [])),
        'failures': failures,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
