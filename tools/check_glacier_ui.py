#!/usr/bin/env python3
"""Check that the application still integrates the reviewed Glacier interface."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image


def checks(repo: Path) -> dict[str, bool]:
    source = repo / "frontend" / "src"

    def text(path: str) -> str:
        try:
            return (source / path).read_text(encoding="utf-8")
        except OSError:
            return ""

    files = [
        "theme.css", "legacy-theme.css", "domain/demoData.js",
        "components/GIcon.vue", "components/GlacierShell.vue",
        "components/GlacierHome.vue", "components/GlacierCatalog.vue",
        "components/GlacierBrainFrame.vue", "components/GlacierWarehouse.vue",
    ]
    main = text("main.ts")
    theme_index = main.find("'./glacier/theme.css'")
    bridge_index = main.find("'./glacier/legacy-theme.css'")
    legacy_index = main.find("'./assets.css'")
    theme = text("glacier/theme.css").lower()
    layout = text("layouts/AppLayout.vue")
    asset = source / "embodied" / "assets" / "materialbrain-bot-v3.png"
    bot_valid = False
    if asset.is_file():
        try:
            with Image.open(asset) as image:
                bot_valid = (
                    image.format == "PNG" and image.size == (1254, 1254)
                    and image.mode == "RGBA" and image.getextrema()[3][0] == 0
                    and image.getextrema()[3][1] == 255
                )
        except OSError:
            pass
    return {
        "Glacier source set": all((source / "glacier" / p).is_file() for p in files),
        "Glacier theme import order": 0 <= legacy_index < theme_index < bridge_index,
        "Glacier palette": all(
            token in theme for token in ("#f0f4f6", "#c7d9e7", "#d9efed", "#477f99", "#25343e")
        ),
        "GlacierShell integrated": "<GlacierShell" in layout and "<el-aside" not in layout,
        "GlacierHome integrated": "<GlacierHome" in text("views/Dashboard.vue"),
        "GlacierBrainFrame integrated": "<GlacierBrainFrame" in text("views/AgentWorkbench.vue"),
        "GlacierWarehouse integrated": "<GlacierWarehouse" in text("views/WarehouseTwin.vue"),
        "Two warehouse modes": "robot-lab" in text("views/WarehouseTwin.vue"),
        "Compatible laboratory link": "warehouse-lab" in text("router/index.ts"),
        "Transparent original robot": bot_valid,
        "Floating robot integrated": "materialbrain-bot-v3.png" in text("components/agent/FloatingAgentLauncher.vue"),
        "Original storage equipment": all(
            name in text("views/Locations.vue") for name in ("DrawerRack100", "OrganizerBox3D", "ShelfRack6")
        ),
        "Production Three.js laboratory": "mountProductionLabApp" in text("embodied/components/EmbodiedTwin.vue"),
        "Server spatial mission subscription": "subscribeSpatialScene" in text("embodied/components/EmbodiedTwin.vue"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo", type=Path, nargs="?", default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    result = checks(args.repo.resolve())
    for name, passed in result.items():
        print(f"{'PASS' if passed else 'FAIL'} {name}")
    return 0 if all(result.values()) else 2


if __name__ == "__main__":
    sys.exit(main())
