"""Read-only, versioned semantic mission planning.

The V3 motion planner remains available under its original heading-grid A* name.
This package orders registered docks over the authoritative directed spatial graph.
"""

from .planner import plan_mission

__all__ = ["plan_mission"]
