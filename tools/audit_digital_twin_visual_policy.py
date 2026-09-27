#!/usr/bin/env python3
from __future__ import annotations
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS = ROOT / 'frontend/src/components/locations/digitalTwin/twin.css'
SCENE = ROOT / 'frontend/src/components/locations/digitalTwin/scene.js'
LABELS = ROOT / 'frontend/src/components/locations/digitalTwin/labelVisibility.js'


def main() -> int:
    css = CSS.read_text(encoding='utf-8')
    scene = SCENE.read_text(encoding='utf-8')
    labels = LABELS.read_text(encoding='utf-8')
    failures: list[str] = []
    sizes = [float(value) for value in re.findall(r'font-size:\s*([0-9.]+)px', css)]
    if not sizes or min(sizes) < 11:
        failures.append(f'visible font-size floor violated: {min(sizes) if sizes else "none"}')
    required_scene = [
        '700 40px', 'updateLabelVisibility', 'routeArrowGeometry',
        'selectedFramed', 'minimumOrbitDistance', 'PCFSoftShadowMap',
    ]
    for token in required_scene:
        if token not in scene:
            failures.append(f'missing scene visual policy token: {token}')
    if 'ConeGeometry(' in scene:
        failures.append('legacy oversized cone route arrows still present')
    for token in ['labelOccluded', 'maxVisible', 'labelInFrustum']:
        if token not in labels:
            failures.append(f'missing label visibility policy: {token}')
    report = {
        'status': 'PASS' if not failures else 'FAIL',
        'font_size_floor_px': min(sizes) if sizes else None,
        'checks': {
            'occlusion_aware_labels': 'labelOccluded' in labels,
            'frustum_aware_labels': 'labelInFrustum' in labels,
            'route_ribbon': 'routeArrowGeometry' in scene and 'ConeGeometry(' not in scene,
            'camera_visual_framing': 'selectedFramed' in scene,
            'soft_shadows': 'PCFSoftShadowMap' in scene,
        },
        'failures': failures,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
