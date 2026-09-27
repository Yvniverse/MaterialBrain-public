from app.models import PickAllocation, PickTask, PickTaskItem


def constraint_names(model) -> set[str]:
    return {item.name for item in model.__table__.constraints if item.name}


def test_picking_models_expose_required_business_constraints():
    assert PickTask.__tablename__ == "pick_tasks"
    assert PickTaskItem.__tablename__ == "pick_task_items"
    assert PickAllocation.__tablename__ == "pick_allocations"
    assert "uq_pick_task_business_operation" in constraint_names(PickTask)
    assert "uq_pick_task_material" in constraint_names(PickTaskItem)
    assert "uq_pick_item_lot" in constraint_names(PickAllocation)
    assert "ck_pick_allocation_picked_lte_planned" in constraint_names(PickAllocation)
