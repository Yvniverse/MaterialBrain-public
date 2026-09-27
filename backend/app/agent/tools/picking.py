from pydantic import BaseModel, Field

from app.services.picking import PickingService


class PickingReadinessArgs(BaseModel):
    build_plan_id: int = Field(gt=0)


class PickTaskArgs(BaseModel):
    pick_task_id: int = Field(gt=0)


def get_build_picking_readiness(ctx, args):
    return PickingService(ctx.db).readiness(args.build_plan_id)


def get_pick_task(ctx, args):
    return PickingService(ctx.db).detail(args.pick_task_id)


def get_next_pick_stop(ctx, args):
    task = PickingService(ctx.db).detail(args.pick_task_id)
    return {"pick_task_id": task["id"], "status": task["status"], "next_stop": task["next_stop"]}
