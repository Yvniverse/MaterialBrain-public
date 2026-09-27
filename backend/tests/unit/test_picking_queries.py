from app.agent.picking_queries import answer_picking_query
from app.agent.task_contract import classify_task_contract
from app.power_design.requirements import extract_power_requirement


def test_negative_picking_mention_stays_on_read_only_engineering_route():
    message = "把这份工程BOM草案整理完整，但先不要写正式产品BOM，也不要创建预留或拣料任务。"

    assert answer_picking_query(None, None, None, message, "request-1") is None


def test_phase333_engineering_bom_draft_routes_to_research_without_model_call():
    message = (
        "12V输入，先Buck到5V再LDO到3.3V，负载100mA。"
        "把这套方案的工程BOM草案展开，并告诉我现在还差哪些器件没选。"
    )

    contract = classify_task_contract(message)
    requirement = extract_power_requirement(message)

    assert contract.entity_kind == "engineering_research"
    assert contract.requested_facts == {"engineering_research"}
    assert str(requirement.input_voltage_v) == "12"
    assert str(requirement.intermediate_voltage_v) == "5"
    assert str(requirement.output_voltage_v) == "3.3"
    assert requirement.topology_choice == "buck_ldo"


def test_phase333_composite_selected_topology_routes_to_engineering_research():
    message = (
        "12V到3.3V、100mA，我准备采用12V→5V Buck→3.3V LDO。"
        "把各级损耗、工程BOM完整度和LM5164候选库存库位一起给我。"
    )

    contract = classify_task_contract(message)
    requirement = extract_power_requirement(message)

    assert contract.entity_kind == "engineering_research"
    assert contract.requested_facts == {"engineering_research"}
    assert requirement.topology_choice == "buck_ldo"
    assert requirement.intermediate_voltage_v == 5
