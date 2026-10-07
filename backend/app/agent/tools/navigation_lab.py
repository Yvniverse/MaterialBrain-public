"""Agent tools expose IDs and structured plans, not invented coordinates."""

from pydantic import BaseModel, ConfigDict

from app.services.embodied_navigation.schemas import PlanRequest
from app.services.embodied_navigation.service import navigation_manifest, plan_navigation


class NavigationManifestArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


def get_navigation_lab(ctx, args):
    return navigation_manifest()


def plan_navigation_lab(ctx, args: PlanRequest):
    result = plan_navigation(args)
    # Raw pose arrays remain available via HTTP/replay. Avoid injecting thousands of
    # coordinates into the language model and hiding important status in token noise.
    return {k: v for k, v in result.items() if k != "segments"} | {
        "segments_count": len(result.get("segments", [])),
        "ui_path": "/warehouse-twin?workspace=robot-lab",
    }
