from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import select

from app.agent.tools.common import ToolContext
from app.agent.tools.inventory import get_inventory_availability
from app.agent.tools.locations import find_material_locations
from app.models import Material, User
from app.power_design.architecture import (
    PowerArchitecture,
    PowerArchitectureStage,
    PowerBomCompletenessSummary,
    PowerCandidateReference,
    PowerRail,
    PowerRailBomDraft,
    PowerRailBomRequirement,
)
from app.power_design.official_evidence import POWER_DEVICE_EVIDENCE
from app.power_design.requirements import PowerRequirement, extract_power_requirement
from app.schemas.agent import MaterialIdArgs
from app.services.data_provenance import material_provenance
from app.services.peripheral_bom import (
    build_constraints,
    candidate_sort_key,
    completeness_summary,
    expected_component_classes,
    material_match,
    requirement_spec,
    resolve_component_class,
)


def _decimal_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value.normalize(), "f")


def _in_range(value: Decimal, minimum: Decimal, maximum: Decimal) -> bool:
    return minimum <= value <= maximum


def _candidate_compatibility(
    evidence: dict[str, Any],
    requirement: PowerRequirement,
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    input_voltage = requirement.input_voltage_v
    output_voltage = requirement.output_voltage_v
    current = requirement.effective_load_current_a
    if input_voltage is not None and not _in_range(
        input_voltage,
        evidence["vin_min_v"],
        evidence["vin_max_v"],
    ):
        reasons.append(
            f"输入 {input_voltage}V 不在 {evidence['vin_min_v']}–{evidence['vin_max_v']}V"
        )
    if output_voltage is not None and not _in_range(
        output_voltage,
        evidence["vout_min_v"],
        evidence["vout_max_v"],
    ):
        reasons.append(
            f"输出 {output_voltage}V 不在 {evidence['vout_min_v']}–{evidence['vout_max_v']}V"
        )
    if current is not None and current > evidence["iout_max_a"]:
        reasons.append(f"负载 {current}A 超过证据上限 {evidence['iout_max_a']}A")
    return not reasons, reasons


def _citation(code: str, evidence: dict[str, Any]) -> dict[str, Any]:
    verified_facts = {
        "vin": f"{evidence['vin_min_v']}–{evidence['vin_max_v']} V",
        "vout": f"{evidence['vout_min_v']}–{evidence['vout_max_v']} V",
        "iout": f"up to {evidence['iout_max_a']} A",
        "package": evidence["package"],
    }
    if evidence.get("fixed_output_v") is not None:
        verified_facts["fixed_output"] = f"{evidence['fixed_output_v']} V"
    return {
        "material_code": code,
        "mpn": evidence["mpn"],
        "source_type": "official_vendor",
        "provenance_type": "official_manifest_adapter",
        "managed_evidence_anchor": False,
        "source_title": evidence["source_title"],
        "source_url": evidence["source_url"],
        "datasheet_url": evidence["datasheet_url"],
        "verified_facts": verified_facts,
        "provenance_status": "manifest_verified_not_managed_anchor",
        "migration_debt": (
            "P1：将官方电源器件事实迁移到 EngineeringEvidence Document/Page/Anchor；"
            "当前适配器不等同于受管页级工程证据。"
        ),
    }


def _peripheral_roles(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    roles: list[dict[str, Any]] = []
    for item in evidence.get("peripheral_roles", []):
        exact_value = item.get("exact_value")
        roles.append(
            {
                "role": item["role"],
                "exact_value": exact_value,
                "connection": item.get("connection"),
                "constraint_value": item.get("constraint_value"),
                "source_document_revision": item.get("source_document_revision"),
                "source_page": item.get("source_page"),
                "value_status": (
                    "datasheet_grounded" if exact_value is not None else "select_device_then_verify"
                ),
                "evidence": item.get("evidence")
                or (
                    "未选定器件，不能在此处臆造精确值；请按所选器件数据手册/参考设计定值。"
                    if exact_value is None
                    else "已由随附的一手器件证据锚定。"
                ),
            }
        )
    return roles


def _ldo_calculation(
    requirement: PowerRequirement,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    vin = requirement.input_voltage_v
    vout = requirement.output_voltage_v
    current = requirement.effective_load_current_a
    calculation: dict[str, Any] = {
        "calculation_type": "deterministic_server_calculation",
        "formula": "P_loss = (Vin - Vout) × I_load; η_ideal = Vout / Vin",
        "source": "服务端 Decimal 计算；不是模型估算",
        "vin_v": vin,
        "vout_v": vout,
        "load_current_a": current,
    }
    if vin is None or vout is None or current is None:
        calculation.update(
            {
                "status": "requires_load_current",
                "loss_w": None,
                "ideal_efficiency": None,
            }
        )
        return calculation
    loss = (vin - vout) * current
    calculation.update(
        {
            "status": "calculated",
            "loss_w": loss,
            "ideal_efficiency": vout / vin if vin else None,
            "thermal_note": (
                f"LDO 在 {current}A 下的线性损耗为 {loss}W；需结合封装/PCB 热阻核对温升。"
            ),
        }
    )
    return calculation


class PowerDesignService:
    """Deterministic, topology-first power planning over audited device facts."""

    def __init__(self, db, user: User, request_id: str):
        self.ctx = ToolContext(db=db, user=user, request_id=request_id)

    def plan(self, requirement_text: str) -> dict[str, Any]:
        requirement = extract_power_requirement(requirement_text)
        missing_constraints: list[str] = []
        if requirement.input_voltage_v is None:
            missing_constraints.append("输入电压")
        if requirement.output_voltage_v is None:
            missing_constraints.append("输出电压")
        if not requirement.has_load_current:
            missing_constraints.append("负载电流（或范围）")

        base: dict[str, Any] = {
            "workflow": "power_design",
            "topology_first": True,
            "read_only": True,
            "request": requirement_text,
            "requirements": requirement.model_dump(mode="json"),
            "missing_constraints": missing_constraints,
            "topology_constraints": requirement.topology_constraints,
            "branches": [],
            "evidence_reconciliation": [],
        }
        base["load_case_calculations"] = self._load_case_calculations(requirement)
        if requirement.input_voltage_v is None or requirement.output_voltage_v is None:
            base["status"] = "needs_constraints"
            base["next_question"] = "请补充输入电压和目标输出电压。"
            return base

        for branch_name in ("buck", "ldo"):
            branch = self._branch(branch_name, requirement)
            base["branches"].append(branch)
            base["evidence_reconciliation"].extend(branch.get("evidence_reconciliation", []))

        architectures = self._architectures(requirement, base["branches"])
        base["topologies"] = [item.model_dump(mode="json") for item in architectures]
        base["selected_topology"] = requirement.topology_choice
        base["rail_bom_draft"] = self._rail_bom_draft(requirement, architectures)

        base["status"] = (
            "needs_constraints"
            if missing_constraints
            else (
                "supported"
                if any(branch["compatible_candidate_count"] for branch in base["branches"])
                else "no_grounded_solution"
            )
        )
        if not requirement.has_load_current:
            base["next_question"] = "请补充最大负载电流（或工作电流范围），再确认热设计与器件余量。"
        return base

    @staticmethod
    def _load_case_calculations(requirement: PowerRequirement) -> list[dict[str, Any]]:
        vin = requirement.input_voltage_v
        vout = requirement.output_voltage_v
        intermediate = requirement.intermediate_voltage_v or Decimal("5.0")
        if vin is None or vout is None:
            return []
        current = requirement.effective_load_current_a
        cases = requirement.load_current_cases_a
        if not cases and requirement.direct_ldo_comparison_input_voltage_v is not None:
            cases = [current] if current is not None else []
        if not cases:
            return []
        post_buck_is_valid = vin > intermediate > vout
        direct_ldo_input = requirement.direct_ldo_comparison_input_voltage_v or vin
        return [
            {
                "load_current_a": current,
                "direct_ldo_input_v": direct_ldo_input,
                "output_voltage_v": vout,
                "direct_ldo_loss_w": (direct_ldo_input - vout) * current,
                "post_buck_intermediate_voltage_v": intermediate if post_buck_is_valid else None,
                "post_buck_ldo_loss_w": (
                    (intermediate - vout) * current if post_buck_is_valid else None
                ),
                "calculation_type": "deterministic_server_calculation",
                "formula": "P_loss = (LDO Vin - Vout) × I_load",
            }
            for current in cases
        ]

    @staticmethod
    def _candidate_reference(candidate: dict[str, Any]) -> PowerCandidateReference:
        locations = candidate.get("locations") or {}
        official = POWER_DEVICE_EVIDENCE.get(str(candidate.get("code") or ""), {})
        inventory = dict(candidate.get("inventory") or {})
        available = inventory.get("available_quantity")
        inventory_status = "unknown"
        try:
            if available is not None:
                inventory_status = (
                    "out_of_stock" if Decimal(str(available)) == 0 else "in_stock"
                )
        except Exception:
            inventory_status = "unknown"
        location_facts = [
            dict(item)
            for item in (locations.get("locations") or [])
            if isinstance(item, dict)
        ][:8]
        class_info = resolve_component_class(candidate)
        return PowerCandidateReference(
            material_id=int(candidate["material_id"]),
            code=str(candidate.get("code") or ""),
            mpn=str(candidate.get("mpn") or candidate.get("code") or ""),
            package=candidate.get("package"),
            inventory=inventory,
            locations=[
                str(item.get("full_path") or item.get("code") or "")
                for item in location_facts
                if item.get("full_path") or item.get("code")
            ][:4],
            location_facts=location_facts,
            match_status=candidate.get("match_status"),
            match_reasons=list(candidate.get("match_reasons") or [])[:12],
            match_unknowns=list(candidate.get("match_unknowns") or [])[:12],
            selection_basis=candidate.get("selection_basis"),
            selection_provenance=dict(candidate.get("selection_provenance") or {}),
            inventory_status=inventory_status,
            peripheral_roles=list(candidate.get("peripheral_roles") or [])[:8],
            engineering_parameters=list(official.get("engineering_parameters") or []),
            citations=[candidate["evidence"]] if candidate.get("evidence") else [],
            component_class=candidate.get("component_class") or class_info["component_class"],
            class_source=candidate.get("class_source") or class_info["class_source"],
            class_match=candidate.get("class_match") or (
                "compatible" if class_info["component_class"] != "unknown" else "unknown"
            ),
            rejection_reason=candidate.get("rejection_reason"),
            provenance=dict(candidate.get("provenance") or {}),
        )

    @staticmethod
    def _ldo_operating_metrics(
        input_voltage_v: Decimal,
        output_voltage_v: Decimal,
        load_current_a: Decimal | None,
        candidates: list[PowerCandidateReference],
    ) -> dict[str, Any]:
        metrics: dict[str, Any] = {
            "ideal_efficiency": (
                output_voltage_v / input_voltage_v if input_voltage_v > 0 else None
            ),
            "quiescent_current_a": None,
            "quiescent_input_power_w": None,
            "thermal_screen": {"status": "unknown_load"},
        }
        if not candidates:
            return metrics
        reference = next(
            (
                candidate
                for candidate in candidates
                if candidate.engineering_parameters
            ),
            None,
        )
        if reference is None:
            return metrics
        parameters = reference.engineering_parameters
        iq = next(
            (
                Decimal(str(item["value"])) / Decimal("1000000")
                for item in parameters
                if item.get("key") == "quiescent_current_typical"
            ),
            None,
        )
        theta_key = (
            "theta_ja_to252"
            if "TO-252" in str(reference.package or "").upper()
            else "theta_ja_sot223"
        )
        theta = next(
            (
                Decimal(str(item["value"]))
                for item in parameters
                if item.get("key") == theta_key
            ),
            None,
        )
        metrics["quiescent_current_a"] = iq
        metrics["quiescent_input_power_w"] = input_voltage_v * iq if iq is not None else None
        if load_current_a is not None and theta is not None:
            loss = (input_voltage_v - output_voltage_v) * load_current_a
            metrics["thermal_screen"] = {
                "status": "illustrative_reference_only",
                "method": "P_loss × θJA; first-order temperature-rise screen",
                "reference_device_mpn": reference.mpn,
                "package": reference.package,
                "theta_ja_c_per_w": theta,
                "estimated_delta_t_c": loss * theta,
                "source_page": 5,
                "source_url": "https://www.ti.com/lit/ds/symlink/tlv761.pdf",
                "limitations": (
                    "基于数据手册 θJA 的一阶估算；不是板级结温预测，"
                    "实际结果取决于 PCB 铜面积/层数、"
                    "布局、气流和环境温度，且此估算不含静态电流附加功耗。"
                ),
            }
        else:
            metrics["thermal_screen"] = {
                "status": "unknown_load" if load_current_a is None else "no_package_evidence"
            }
        return metrics

    @staticmethod
    def _rail_currents(requirement: PowerRequirement) -> dict[str, tuple[Decimal | None, str]]:
        total = requirement.effective_load_current_a
        analog = requirement.analog_load_current_a
        digital = requirement.digital_load_current_a
        analog_basis = "user_rail" if analog is not None else "not_allocated"
        digital_basis = "user_rail" if digital is not None else "not_allocated"
        if total is not None and analog is not None and digital is None and analog < total:
            digital = total - analog
            digital_basis = "derived_from_total"
        elif total is not None and digital is not None and analog is None and digital < total:
            analog = total - digital
            analog_basis = "derived_from_total"
        if total is not None and analog is None and digital is None:
            analog_basis = digital_basis = "not_allocated"
        return {
            "analog": (analog, analog_basis),
            "digital": (digital, digital_basis),
        }

    def _compatible_references(
        self,
        topology: str,
        requirement: PowerRequirement,
        branch_candidates: list[dict[str, Any]],
    ) -> list[PowerCandidateReference]:
        references = []
        for candidate in branch_candidates:
            evidence = POWER_DEVICE_EVIDENCE.get(str(candidate.get("code") or ""))
            if evidence is None or evidence.get("topology") != topology:
                continue
            compatible, _ = _candidate_compatibility(evidence, requirement)
            if compatible:
                references.append(self._candidate_reference(candidate))
        return references

    def _architectures(
        self,
        requirement: PowerRequirement,
        branches: list[dict[str, Any]],
    ) -> list[PowerArchitecture]:
        by_topology = {str(item.get("topology")): item for item in branches}
        buck_candidates = list((by_topology.get("buck") or {}).get("candidates") or [])
        ldo_candidates = list((by_topology.get("ldo") or {}).get("candidates") or [])
        vin = requirement.input_voltage_v
        vout = requirement.output_voltage_v
        current = requirement.effective_load_current_a
        intermediate = requirement.intermediate_voltage_v or Decimal("5.0")
        has_intermediate_buck = (
            vin is not None and vout is not None and vin > intermediate > vout
        )
        ldo_input_voltage = intermediate if has_intermediate_buck else vin
        current_basis = "user_total" if current is not None else "not_allocated"
        buck_at_intermediate = requirement.model_copy(update={"output_voltage_v": intermediate})
        ldo_from_intermediate = requirement.model_copy(
            update={
                "input_voltage_v": ldo_input_voltage,
                "output_voltage_v": vout,
            }
        )
        intermediate_bucks = self._compatible_references(
            "buck", buck_at_intermediate, buck_candidates
        )
        intermediate_ldos = self._compatible_references(
            "ldo", ldo_from_intermediate, ldo_candidates
        )
        direct_bucks = self._compatible_references("buck", requirement, buck_candidates)
        architectures: list[PowerArchitecture] = []
        if vin is None or vout is None:
            return architectures

        direct_stage = PowerArchitectureStage(
            stage_id="direct-digital-buck",
            topology="buck",
            input_voltage_v=vin,
            output_voltage_v=vout,
            load_current_a=current,
            current_basis=current_basis,
            loss_w=None,
            loss_status=("requires_efficiency_curve" if current is not None else "unknown_load"),
            dropout_status="not_applicable",
            candidate_devices=direct_bucks,
            notes=[
                "Buck 效率/温升要根据所选器件、开关频率、电感和实际负载曲线核对。",
                "给 MCU 使用不能仅凭拓扑定性；应核对目标负载下的输出纹波、瞬态和 EMI。",
            ],
        )
        architectures.append(
            PowerArchitecture(
                topology="direct_buck",
                label=f"{vin}V → Buck → {vout}V",
                availability="candidate_found" if direct_bucks else "no_grounded_candidate",
                selected_by_user=requirement.topology_choice == "direct_buck",
                total_load_current_a=current,
                rails=[
                    PowerRail(
                        rail_id="main-digital",
                        label="数字 / MCU 电源轨",
                        voltage_v=vout,
                        load_current_a=current,
                        current_basis=current_basis,
                        stage_ids=[direct_stage.stage_id],
                    )
                ],
                stages=[direct_stage],
                summary=(
                    "若纹波、瞬态、EMI 和去耦满足 MCU 约束，直接 Buck 可行且通常避免额外线性损耗。"
                    if direct_bucks
                    else "可作为架构比较项；当前器件证据/库存中没有已核对的兼容 Buck 候选。"
                ),
                constraints=[
                    "不能把 LM5164 反馈注入纹波直接当作 VOUT 纹波；"
                    "VOUT 仍需按所选拓扑、负载和测量条件核对。"
                ],
            )
        )

        ldo_loss = (intermediate - vout) * current if current is not None else None
        buck5_stage = PowerArchitectureStage(
            stage_id="intermediate-buck-5v",
            topology="buck",
            input_voltage_v=vin,
            output_voltage_v=intermediate,
            load_current_a=None,
            current_basis="not_allocated",
            loss_w=None,
            loss_status="requires_efficiency_curve",
            dropout_status="not_applicable",
            candidate_devices=intermediate_bucks,
            notes=[
                "5V 轨的实际输入电流还受 LDO 静态电流和其它 5V 负载影响；"
                "未按 3.3V 输出电流冒充 5V 输入电流。"
            ],
        )
        ldo_stage = PowerArchitectureStage(
            stage_id="post-ldo-3v3",
            topology="ldo",
            input_voltage_v=intermediate,
            output_voltage_v=vout,
            load_current_a=current,
            current_basis=current_basis,
            loss_w=ldo_loss,
            **self._ldo_operating_metrics(intermediate, vout, current, intermediate_ldos),
            loss_status="calculated" if ldo_loss is not None else "unknown_load",
            headroom_v=intermediate - vout,
            dropout_status="verify_at_load",
            candidate_devices=intermediate_ldos,
            notes=[
                "服务端损耗为 (LDO 输入电压 − 输出电压) × 负载电流；"
                "还需按具体负载和压差核对 dropout。",
                "PSRR 随频率、负载和压差变化；单一频点数据不能代表全部开关噪声频段。",
                "理想效率仅按 Vout/Vin；静态电流单独列出，不把典型值伪装成全温保证值。",
            ],
        )
        architectures.append(
            PowerArchitecture(
                topology="buck_ldo",
                label=f"{vin}V → Buck → {intermediate}V → LDO → {vout}V",
                availability=(
                    "candidate_found" if intermediate_bucks and intermediate_ldos else "conceptual"
                ),
                selected_by_user=requirement.topology_choice == "buck_ldo",
                total_load_current_a=current,
                rails=[
                    PowerRail(
                        rail_id="main-low-noise",
                        label=f"{vout}V 后级稳压轨",
                        voltage_v=vout,
                        load_current_a=current,
                        current_basis=current_basis,
                        stage_ids=[buck5_stage.stage_id, ldo_stage.stage_id],
                        notes=["中间轨为 5V 时，后级 LDO 输入按 5V 计算。"],
                    )
                ],
                stages=[buck5_stage, ldo_stage],
                summary=(
                    "后级 LDO 可衰减其有效频段内的一部分开关噪声，但增加静态电流和线性热损耗；"
                    "是否值得取决于实际 PSRR 曲线、负载、压差及是否已有 5V 轨。"
                ),
                constraints=[
                    "LDO 的输入是中间轨，不是原始 12V。",
                    "本方案不假定 Buck+LDO 对所有 MCU 都必需或总是最优。",
                ],
            )
        )
        if not has_intermediate_buck:
            architectures.pop()

        rail_currents = self._rail_currents(requirement)
        analog_current, analog_basis = rail_currents["analog"]
        digital_current, digital_basis = rail_currents["digital"]
        split_digital_stage = PowerArchitectureStage(
            stage_id="split-digital-buck",
            topology="buck",
            input_voltage_v=vin,
            output_voltage_v=vout,
            load_current_a=digital_current,
            current_basis=digital_basis,
            loss_w=None,
            loss_status=(
                "requires_efficiency_curve" if digital_current is not None else "unknown_load"
            ),
            dropout_status="not_applicable",
            candidate_devices=direct_bucks,
            notes=["数字轨电流未单独提供时保持未知，不从总负载复制一份。"],
        )
        split_analog_buck_stage = PowerArchitectureStage(
            stage_id="split-analog-buck-5v",
            topology="buck",
            input_voltage_v=vin,
            output_voltage_v=intermediate,
            load_current_a=None,
            current_basis="not_allocated",
            loss_w=None,
            loss_status="requires_efficiency_curve",
            dropout_status="not_applicable",
            candidate_devices=intermediate_bucks,
            notes=["为敏感模拟支路准备中间轨；若系统已有合规 5V 轨，可重新评估是否复用。"],
        )
        split_analog_ldo_loss = (
            (ldo_input_voltage - vout) * analog_current
            if analog_current is not None and ldo_input_voltage is not None
            else None
        )
        split_analog_ldo_stage = PowerArchitectureStage(
            stage_id="split-analog-ldo",
            topology="ldo",
            input_voltage_v=ldo_input_voltage,
            output_voltage_v=vout,
            load_current_a=analog_current,
            current_basis=analog_basis,
            loss_w=split_analog_ldo_loss,
            **self._ldo_operating_metrics(
                ldo_input_voltage if ldo_input_voltage is not None else intermediate,
                vout,
                analog_current,
                intermediate_ldos,
            ),
            loss_status=("calculated" if split_analog_ldo_loss is not None else "unknown_load"),
            headroom_v=(ldo_input_voltage - vout if ldo_input_voltage is not None else None),
            dropout_status="verify_at_load",
            candidate_devices=intermediate_ldos,
            notes=[
                "只对模拟支路的已知电流计算 LDO 损耗；需按对应频率、负载和压差核对 PSRR。",
                "理想效率仅按 Vout/Vin；静态电流单独列出，不把典型值伪装成全温保证值。",
            ],
        )
        architectures.append(
            PowerArchitecture(
                topology="split_rails",
                label="数字与敏感模拟支路分轨",
                availability=(
                    "candidate_found"
                    if direct_bucks
                    and intermediate_ldos
                    and (not has_intermediate_buck or intermediate_bucks)
                    else "conceptual"
                ),
                selected_by_user=requirement.topology_choice == "split_rails",
                total_load_current_a=current,
                rails=[
                    PowerRail(
                        rail_id="digital-rail",
                        label=f"数字 / MCU {vout}V 轨",
                        voltage_v=vout,
                        load_current_a=digital_current,
                        current_basis=digital_basis,
                        stage_ids=[split_digital_stage.stage_id],
                    ),
                    PowerRail(
                        rail_id="analog-rail",
                        label=f"敏感模拟 {vout}V 轨",
                        voltage_v=vout,
                        load_current_a=analog_current,
                        current_basis=analog_basis,
                        sensitive_analog=True,
                        stage_ids=(
                            [split_analog_buck_stage.stage_id, split_analog_ldo_stage.stage_id]
                            if has_intermediate_buck
                            else [split_analog_ldo_stage.stage_id]
                        ),
                        notes=["未提供模拟/数字分配电流时，两条支路电流都保持未知。"],
                    ),
                ],
                stages=[
                    split_digital_stage,
                    *([split_analog_buck_stage] if has_intermediate_buck else []),
                    split_analog_ldo_stage,
                ],
                summary=(
                    "数字轨可直接 Buck；敏感模拟轨可采用独立低噪声 LDO/滤波支路。"
                    "只有明确模拟电流后才计算该支路的损耗，不能将总电流重复算到两轨。"
                ),
                constraints=[
                    "模拟支路需要独立的负载电流和噪声带宽；"
                    "其 LDO 输入必须高于目标输出并满足 dropout。",
                    (
                        "若 5V 轨不是现有系统电源，模拟 LDO 前级 Buck 的成本/静态电流也需纳入比较。"
                        if has_intermediate_buck
                        else "模拟 LDO 由输入轨直接供电；需核对输入电压和该负载下的 dropout。"
                    ),
                ],
            )
        )
        for architecture in architectures:
            for stage in architecture.stages:
                stage.bom_requirements = self._stage_bom_requirements(stage, requirement)
                stage.engineering_facts = self._stage_engineering_facts(stage)
                stage.selection_status = self._stage_selection_status(stage, requirement)
                stage.completeness = PowerBomCompletenessSummary(
                    **completeness_summary(
                        [item.model_dump(mode="json") for item in stage.bom_requirements]
                    )
                )
            stage_by_id = {stage.stage_id: stage for stage in architecture.stages}
            for rail in architecture.rails:
                rail_stages = [stage_by_id[item] for item in rail.stage_ids if item in stage_by_id]
                rail.selection_status = self._rail_selection_status(rail, rail_stages)
                rail.completeness = PowerBomCompletenessSummary(
                    **completeness_summary(
                        [
                            requirement.model_dump(mode="json")
                            for stage in rail_stages
                            for requirement in stage.bom_requirements
                        ]
                    )
                )
        return architectures

    @staticmethod
    def _role_matches(required_role: str, evidence_role: str) -> bool:
        required = required_role.casefold()
        evidence = evidence_role.casefold()
        aliases = {
            "feedback upper resistor": {"feedback upper resistor", "feedback divider"},
            "feedback lower resistor": {"feedback lower resistor", "feedback divider"},
        }
        return evidence in aliases.get(required, {required})

    @classmethod
    def _role_evidence(
        cls,
        stage: PowerArchitectureStage,
        role: str,
    ) -> list[dict[str, Any]]:
        grounded: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        for candidate in stage.candidate_devices:
            for peripheral in candidate.peripheral_roles:
                evidence_role = str(peripheral.get("role") or "")
                if not cls._role_matches(role, evidence_role):
                    continue
                key = (
                    candidate.mpn,
                    evidence_role,
                    str(peripheral.get("exact_value") or peripheral.get("constraint_value") or ""),
                )
                if key in seen:
                    continue
                seen.add(key)
                grounded.append({"mpn": candidate.mpn, **dict(peripheral)})
        return grounded

    @staticmethod
    def _candidate_inventory_status(candidate: PowerCandidateReference) -> str:
        return str(candidate.inventory_status or "unknown")

    @classmethod
    def _requirement_inventory_status(
        cls,
        candidates: list[PowerCandidateReference],
    ) -> str:
        if not candidates:
            return "not_checked"
        statuses = [cls._candidate_inventory_status(candidate) for candidate in candidates]
        if "in_stock" in statuses:
            return "in_stock"
        if statuses and all(item == "out_of_stock" for item in statuses):
            return "out_of_stock"
        return "unknown"

    @staticmethod
    def _requirement_match_summary(
        candidates: list[PowerCandidateReference],
        selected_material_id: int | None = None,
    ) -> dict[str, Any]:
        valid = [
            item
            for item in candidates
            if item.match_status in {"exact", "compatible"}
            or (
                item.match_status is None
                and item.selection_status in {"candidate_found", "selected"}
            )
        ]
        partial = [item for item in candidates if item.match_status == "partial"]
        mismatch = [item for item in candidates if item.match_status == "mismatch"]
        selected_valid = None
        if selected_material_id is not None:
            selected_valid = any(
                item.material_id == selected_material_id for item in valid
            )
        return {
            "exact_or_compatible_candidates": len(valid),
            "partial_candidates": len(partial),
            "mismatch_candidates": len(mismatch),
            "selected_candidate_valid": selected_valid,
        }

    def _passive_material_candidates(
        self,
        role: str,
        exact_value: str | None,
        grounded_roles: list[dict[str, Any]],
    ) -> list[PowerCandidateReference]:
        if not exact_value:
            return []
        specification = requirement_spec(exact_value)
        specification["constraints"] = build_constraints(role, exact_value, grounded_roles)
        specification["role"] = role
        specification["expected_component_classes"] = sorted(expected_component_classes(role))
        materials = self.ctx.db.scalars(
            select(Material).where(
                Material.is_deleted.is_(False),
                Material.is_active.is_(True),
            )
        ).all()
        candidates: list[PowerCandidateReference] = []
        ranked_rows: list[tuple[tuple[Any, ...], dict[str, Any], dict[str, Any]]] = []
        for material in materials:
            row = {
                "material_id": material.id,
                "code": material.code,
                "name": material.name,
                "mpn": material.mpn,
                "package": material.package,
                "specification": material.specification,
                "attributes": material.attributes or {},
            }
            comparison = material_match(specification, row)
            match_status = comparison.get("match_status") or comparison.get("status")
            candidate_row = {
                **row,
                "match_status": match_status,
                "match_reasons": comparison.get("match_reasons") or [],
                "match_unknowns": comparison.get("unknowns") or [],
                "normalized_spec": comparison.get("normalized_spec") or {},
                "component_class": comparison.get("component_class", "unknown"),
                "class_source": comparison.get("class_source", "unknown"),
                "class_match": comparison.get("class_match", "unknown"),
                "rejection_reason": comparison.get("rejection_reason"),
                "peripheral_roles": grounded_roles,
                "evidence": {
                    "material_code": material.code,
                    "source_type": "engineering_role_match",
                    "provenance_status": "role_evidence_grounded",
                    "verified_facts": {
                        "requirement_role": role,
                        "required_value": exact_value,
                        "constraints": specification.get("constraints") or [],
                    },
                },
            }
            # Mismatches are retained for the UI explanation, but inventory
            # reads are limited to candidates that could still satisfy the
            # grounded requirement.
            if match_status != "mismatch":
                candidate_row["inventory"] = get_inventory_availability(
                    self.ctx,
                    MaterialIdArgs(material_id=material.id),
                )
                candidate_row["locations"] = find_material_locations(
                    self.ctx,
                    MaterialIdArgs(material_id=material.id),
                )
                candidate_row["provenance"] = material_provenance(
                    material,
                    inventory=candidate_row.get("inventory"),
                    locations=candidate_row.get("locations"),
                )
            ranked_rows.append((candidate_sort_key(candidate_row), candidate_row, comparison))
        ranked_rows.sort(key=lambda item: item[0])
        for _, candidate_row, _ in ranked_rows[:12]:
            candidates.append(self._candidate_reference(candidate_row))
        return candidates

    @classmethod
    def _stage_engineering_facts(
        cls,
        stage: PowerArchitectureStage,
    ) -> list[dict[str, Any]]:
        facts: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        for candidate in stage.candidate_devices:
            for peripheral in candidate.peripheral_roles:
                value = peripheral.get("exact_value") or peripheral.get("constraint_value")
                if value is None:
                    continue
                role = str(peripheral.get("role") or "外围工程事实")
                key = (candidate.mpn, role, str(value))
                if key in seen:
                    continue
                seen.add(key)
                facts.append(
                    {
                        "fact_id": f"{stage.stage_id}:{candidate.mpn}:{role}",
                        "mpn": candidate.mpn,
                        "role": role,
                        "value": value,
                        "value_status": peripheral.get("value_status") or "datasheet_grounded",
                        "connection": peripheral.get("connection"),
                        "source_document_revision": peripheral.get("source_document_revision"),
                        "source_page": peripheral.get("source_page"),
                        "related_source_page": peripheral.get("related_source_page"),
                        "evidence": peripheral.get("evidence"),
                    }
                )
        return facts

    def _stage_bom_requirements(
        self,
        stage: PowerArchitectureStage,
        requirement: PowerRequirement,
    ) -> list[PowerRailBomRequirement]:
        if stage.topology == "buck":
            roles = [
                "primary regulator IC",
                "input capacitor",
                "inductor",
                "output capacitor",
                "feedback upper resistor",
                "feedback lower resistor",
                "bootstrap capacitor",
            ]
        elif stage.topology == "ldo":
            roles = ["primary LDO IC", "input capacitor", "output capacitor"]
        else:
            return []

        needs_input = (
            not requirement.has_load_current
            or stage.stage_id.startswith("split-") and stage.load_current_a is None
        )
        requirements: list[PowerRailBomRequirement] = []
        for role in roles:
            requirement_id = f"{stage.stage_id}:{role.casefold().replace(' ', '-')}"
            if role in {"primary regulator IC", "primary LDO IC"}:
                candidates = list(stage.candidate_devices)
                expected_classes = (
                    ["buck converter", "switching regulator", "dc-dc converter"]
                    if stage.topology == "buck"
                    else ["ldo", "linear regulator"]
                )
                status = "needs_input" if needs_input else (
                    "candidate_found" if candidates else "no_grounded_candidate"
                )
                requirements.append(
                    PowerRailBomRequirement(
                        requirement_id=requirement_id,
                        role=role,
                        required_quantity=Decimal("1"),
                        evidence_status="grounded" if candidates else "not_grounded",
                        status=status,
                        selection_status=status,
                        inventory_status=self._requirement_inventory_status(candidates),
                        candidates=candidates,
                        citations=[
                            citation
                            for candidate in candidates
                            for citation in candidate.citations[:4]
                        ][:8],
                        notes=[
                            "库存存在只证明候选事实，不代表系统已选择该器件。",
                        ],
                        component_class=expected_classes[0],
                        expected_component_classes=expected_classes,
                        provenance=(
                            dict(candidates[0].provenance)
                            if candidates and candidates[0].provenance
                            else {}
                        ),
                        completeness_contribution={
                            "defined": True,
                            "grounded": bool(candidates),
                            "candidate_covered": bool(candidates),
                            "explicitly_selected": False,
                            "evidence_gap": not bool(candidates),
                        },
                    )
                )
                continue

            grounded_roles = self._role_evidence(stage, role)
            exact_values = list(
                dict.fromkeys(
                    str(item.get("exact_value"))
                    for item in grounded_roles
                    if item.get("exact_value") is not None
                )
            )
            exact_value = exact_values[0] if len(exact_values) == 1 else None
            constraints = build_constraints(role, exact_value, grounded_roles)
            candidates = self._passive_material_candidates(
                role,
                exact_value,
                grounded_roles,
            )
            role_expected_classes = sorted(expected_component_classes(role))
            match_summary = self._requirement_match_summary(candidates)
            valid_candidates = [
                item
                for item in candidates
                if item.match_status in {"exact", "compatible"}
            ]
            if needs_input:
                status = "needs_input"
            elif not grounded_roles:
                status = "no_grounded_candidate"
            elif valid_candidates:
                status = (
                    "out_of_stock"
                    if self._requirement_inventory_status(valid_candidates) == "out_of_stock"
                    else "candidate_found"
                )
            else:
                status = "needs_selection"
            notes: list[str] = []
            if not candidates:
                if exact_value:
                    notes.append(
                        "没有找到满足已知规格的 grounded material candidate；保持 needs_selection，"
                        "不把它解释为库存为零。"
                    )
                elif grounded_roles:
                    notes.append("精确值需按所选器件数据手册或参考设计确认。")
                else:
                    notes.append("当前器件证据没有覆盖该外围角色，保持 no_grounded_candidate。")
            requirements.append(
                PowerRailBomRequirement(
                    requirement_id=requirement_id,
                    role=role,
                    required_quantity=Decimal("1"),
                    exact_value=exact_value,
                    evidence_status="grounded" if grounded_roles else "not_grounded",
                    status=status,
                    selection_status=status,
                    inventory_status=self._requirement_inventory_status(valid_candidates),
                    constraints=constraints,
                    match_summary=match_summary,
                    candidates=candidates,
                    peripheral_roles=grounded_roles,
                    citations=grounded_roles[:8],
                    notes=notes,
                    component_class=(
                        next(
                            (
                                candidate.component_class
                                for candidate in candidates
                                if candidate.component_class != "unknown"
                            ),
                            role_expected_classes[0] if role_expected_classes else "unknown",
                        )
                    ),
                    expected_component_classes=role_expected_classes,
                    provenance=(
                        dict(candidates[0].provenance)
                        if candidates and candidates[0].provenance
                        else {}
                    ),
                    completeness_contribution={
                        "defined": True,
                        "grounded": bool(grounded_roles),
                        "candidate_covered": bool(valid_candidates),
                        "explicitly_selected": False,
                        "evidence_gap": not bool(grounded_roles),
                    },
                )
            )
        return requirements

    @staticmethod
    def _stage_selection_status(
        stage: PowerArchitectureStage,
        requirement: PowerRequirement,
    ) -> str:
        if (
            not requirement.has_load_current
            or stage.stage_id.startswith("split-") and stage.load_current_a is None
        ):
            return "needs_input"
        statuses = [item.status for item in stage.bom_requirements]
        if "no_grounded_candidate" in statuses:
            return "no_grounded_candidate"
        if any(item in {"needs_selection", "out_of_stock"} for item in statuses):
            return "needs_selection"
        if statuses and all(
            item in {"candidate_found", "selected", "not_applicable"} for item in statuses
        ):
            return "candidate_found"
        return "needs_selection"

    @staticmethod
    def _rail_selection_status(
        rail: PowerRail,
        stages: list[PowerArchitectureStage],
    ) -> str:
        if rail.load_current_a is None:
            return "needs_input"
        statuses = [stage.selection_status for stage in stages]
        if "needs_input" in statuses:
            return "needs_input"
        if "no_grounded_candidate" in statuses:
            return "no_grounded_candidate"
        if any(item in {"needs_selection", "out_of_stock"} for item in statuses):
            return "needs_selection"
        return "candidate_found"

    @staticmethod
    def _rail_bom_draft(
        requirement: PowerRequirement,
        architectures: list[PowerArchitecture],
    ) -> dict[str, Any]:
        selected = requirement.topology_choice
        if selected is None:
            return PowerRailBomDraft(
                status="needs_selection",
                manual_review=[
                    "选择直接 Buck、Buck+LDO 或数字/模拟分轨后，再展开对应多轨工程 BOM 草案。"
                ],
            ).model_dump(mode="json")
        architecture = next((item for item in architectures if item.topology == selected), None)
        if architecture is None:
            return PowerRailBomDraft(
                status="not_supported",
                selected_topology=selected,
                manual_review=["当前输入/输出电压不足以建立该拓扑的分级草案。"],
            ).model_dump(mode="json")
        stages = {item.stage_id: item for item in architecture.stages}
        rails: list[dict[str, Any]] = []
        all_requirements: list[dict[str, Any]] = []
        for rail in architecture.rails:
            rail_stages: list[dict[str, Any]] = []
            rail_requirements: list[dict[str, Any]] = []
            for stage_id in rail.stage_ids:
                stage = stages[stage_id]
                stage_requirements = [
                    requirement.model_dump(mode="json")
                    for requirement in stage.bom_requirements
                ]
                rail_requirements.extend(stage_requirements)
                all_requirements.extend(stage_requirements)
                rail_stages.append(
                    {
                        "stage_id": stage.stage_id,
                        "topology": stage.topology,
                        "input_voltage_v": stage.input_voltage_v,
                        "output_voltage_v": stage.output_voltage_v,
                        "load_current_a": stage.load_current_a,
                        "loss_w": stage.loss_w,
                        "loss_status": stage.loss_status,
                        "headroom_v": stage.headroom_v,
                        "dropout_status": stage.dropout_status,
                        "selection_status": stage.selection_status,
                        "engineering_facts": list(stage.engineering_facts),
                        "bom_requirements": stage_requirements,
                        "candidate_devices": [
                            candidate.model_dump(mode="json")
                            for candidate in stage.candidate_devices
                        ],
                        "selected_material_id": None,
                        "selection_basis": None,
                        "completeness": stage.completeness.model_dump(mode="json"),
                    }
                )
            rails.append(
                {
                    "rail_id": rail.rail_id,
                    "label": rail.label,
                    "voltage_v": rail.voltage_v,
                    "load_current_a": rail.load_current_a,
                    "current_basis": rail.current_basis,
                    "sensitive_analog": rail.sensitive_analog,
                    "selection_status": rail.selection_status,
                    "stages": rail_stages,
                    "completeness": completeness_summary(rail_requirements),
                }
            )
        completeness = completeness_summary(all_requirements)
        if completeness["needs_input_roles"]:
            draft_status = "needs_input"
        elif completeness["complete_for_review"]:
            draft_status = "complete_draft"
        else:
            draft_status = "needs_selection"
        return PowerRailBomDraft(
            status=draft_status,
            selected_topology=selected,
            rails=rails,
            completeness=completeness,
            manual_review=[
                "候选、库存、库位和引用只作为只读草案；显式选择后仍未写入正式 BOM。",
                "未被当前器件证据覆盖的转换效率、输出纹波、瞬态和 dropout 条件保持未知。",
            ],
        ).model_dump(mode="json")

    def _branch(self, topology: str, requirement: PowerRequirement) -> dict[str, Any]:
        branch_candidates: list[dict[str, Any]] = []
        reconciliations: list[dict[str, Any]] = []
        for code, evidence in POWER_DEVICE_EVIDENCE.items():
            if evidence["topology"] != topology:
                continue
            material = self.ctx.db.scalar(
                select(Material).where(
                    Material.code == code,
                    Material.is_deleted.is_(False),
                    Material.is_active.is_(True),
                )
            )
            if material is None:
                continue
            compatible, incompatibilities = _candidate_compatibility(evidence, requirement)
            inventory = get_inventory_availability(
                self.ctx,
                MaterialIdArgs(material_id=material.id),
            )
            locations = find_material_locations(
                self.ctx,
                MaterialIdArgs(material_id=material.id),
            )
            material_payload = {
                "attributes": material.attributes or {},
                "category": (
                    {"name": material.category.name, "code": material.category.code}
                    if material.category is not None
                    else None
                ),
            }
            class_info = resolve_component_class(material_payload)
            expected_class = (
                {"buck converter", "switching regulator", "dc-dc converter"}
                if topology == "buck"
                else {"ldo", "linear regulator"}
            )
            class_match = (
                "unknown"
                if class_info["component_class"] == "unknown"
                else "compatible"
                if class_info["component_class"] in expected_class
                else "mismatch"
            )
            if class_match == "mismatch":
                continue
            citation = _citation(code, evidence)
            candidate = {
                "material_id": material.id,
                "code": material.code,
                "name": material.name,
                "mpn": material.mpn or evidence["mpn"],
                "manufacturer": material.manufacturer or "Texas Instruments",
                "package": evidence["package"],
                "topology": topology,
                "compatible": compatible,
                "provisional": requirement.effective_load_current_a is None,
                "incompatibilities": incompatibilities,
                "inventory": inventory,
                "locations": locations,
                "attributes": dict(material.attributes or {}),
                "component_class": class_info["component_class"],
                "class_source": class_info["class_source"],
                "class_match": class_match,
                "rejection_reason": None,
                "match_status": "compatible" if class_match == "compatible" else "partial",
                "provenance": material_provenance(
                    material,
                    inventory=inventory,
                    locations=locations,
                ),
                "evidence": citation,
                "peripheral_roles": _peripheral_roles(evidence),
            }
            if topology == "ldo":
                candidate["calculations"] = [_ldo_calculation(requirement, evidence)]
            if evidence.get("local_conflict"):
                conflict = {
                    "material_code": code,
                    **evidence["local_conflict"],
                }
                candidate["evidence_reconciliation"] = [conflict]
                reconciliations.append(conflict)
            if compatible:
                # Electrical compatibility and component-class readiness are
                # separate facts.  Keep an electrically relevant untyped
                # candidate visible as a partial candidate so existing power
                # planning remains truthful, while the class gate prevents
                # it from being promoted to exact/compatible.
                branch_candidates.append(candidate)

        return {
            "topology": topology,
            "label": "Buck（开关降压）" if topology == "buck" else "LDO（线性稳压）",
            "supported": bool(branch_candidates),
            "compatible_candidate_count": sum(
                candidate.get("match_status") == "compatible"
                for candidate in branch_candidates
            ),
            "candidates": branch_candidates,
            "tradeoff_summary": (
                "效率通常更高，但需要电感、输入/输出电容和反馈网络；精确值必须由所选器件数据手册/参考设计确定。"
                if topology == "buck"
                else "外围简单、噪声路径可控，但线性损耗由输入输出压差和负载电流决定；需核对温升。"
            ),
            "peripheral_roles": [
                role
                for candidate in branch_candidates[:1]
                for role in candidate.get("peripheral_roles", [])
            ],
            "calculations": (
                [_ldo_calculation(requirement, POWER_DEVICE_EVIDENCE[branch_candidates[0]["code"]])]
                if topology == "ldo" and branch_candidates
                else []
            ),
            "citations": [candidate["evidence"] for candidate in branch_candidates],
            "evidence_reconciliation": reconciliations,
            "missing_constraints": (
                ["负载电流（或范围）"] if not requirement.has_load_current else []
            ),
        }
