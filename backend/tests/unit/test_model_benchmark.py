import json
from copy import deepcopy

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.models import Material, Project
from app.schemas.agent import AgentQueryResponse, AgentToolEvent
from evals.assertions import evaluate_case
from evals.checkpoint import (
    EVALUATOR_COMPATIBILITY_VERSION,
    CheckpointCompatibilityError,
    append_case_result,
    bootstrap_cross_version_from_report,
    bootstrap_from_stage_report,
    bootstrap_full_from_p0_report,
    discard_case_results,
    load_checkpoint,
    new_checkpoint,
    provider_configuration_hash,
    validate_checkpoint_case_compatibility,
    write_checkpoint,
)
from evals.report import build_report
from evals.run_golden_eval import GOLDEN_CORPORA, load_cases, run_cases
from evals.run_model_benchmark import (
    CORE_CASE_IDS,
    TokenBudgetTracker,
    benchmark_exit_code,
    cases_for_stage,
    certification_state_for_stage,
    recommendations,
    resolve_models,
    stage_gate_passed,
)
from evals.run_production_shadow_eval import _assert_model_allowed, _load_certification
from evals.seed_golden_db import seed_golden_database


def test_resolve_models_defaults_to_phase_19_baseline_when_allowlist_is_empty(monkeypatch):
    monkeypatch.setenv("EVAL_MODEL", "qwen3.6-flash")
    monkeypatch.delenv("EVAL_MODELS", raising=False)
    monkeypatch.delenv("EVAL_FREE_MODEL_ALLOWLIST", raising=False)

    selected, blocked, allowlist = resolve_models("")

    assert selected == ["qwen3.6-flash"]
    assert blocked == []
    assert allowlist == []


def test_resolve_models_blocks_models_outside_explicit_allowlist(monkeypatch):
    monkeypatch.setenv("EVAL_MODEL", "qwen3.6-flash")
    monkeypatch.setenv("EVAL_FREE_MODEL_ALLOWLIST", "qwen3.6-flash,qwen3.7-flash")

    selected, blocked, allowlist = resolve_models("qwen3.7-flash,qwen3.6-flash,qwen-unknown")

    assert selected == ["qwen3.7-flash", "qwen3.6-flash"]
    assert blocked == ["qwen-unknown"]
    assert allowlist == ["qwen3.6-flash", "qwen3.7-flash"]


@pytest.mark.parametrize("model", ["qwen3.8-flash", "qwen3.8-max", "QWEN3.8-27b"])
def test_qwen38_is_hard_blocked_before_any_benchmark_or_shadow_call(monkeypatch, model):
    monkeypatch.setenv("EVAL_FREE_MODEL_ALLOWLIST", model)

    with pytest.raises(RuntimeError, match="hard-blocked"):
        resolve_models(model)
    with pytest.raises(RuntimeError, match="hard-blocked"):
        _assert_model_allowed(model)


def test_phase_110_models_require_qwen37_first(monkeypatch):
    monkeypatch.setenv(
        "EVAL_FREE_MODEL_ALLOWLIST",
        "qwen3.6-flash,qwen3.7-flash",
    )

    with pytest.raises(RuntimeError, match="must run in order"):
        resolve_models("qwen3.6-flash,qwen3.7-flash")

    selected, blocked, _ = resolve_models("qwen3.7-flash,qwen3.6-flash")
    assert selected == ["qwen3.7-flash", "qwen3.6-flash"]
    assert blocked == []


@pytest.mark.parametrize("model", ["qwen3.5-flash", "qwen-plus"])
def test_phase_19_rejected_or_production_models_are_frozen(monkeypatch, model):
    monkeypatch.setenv("EVAL_FREE_MODEL_ALLOWLIST", model)
    with pytest.raises(RuntimeError, match="forbids calls"):
        resolve_models(model)
    with pytest.raises(RuntimeError, match="forbids Shadow calls"):
        _assert_model_allowed(model)


def test_resolve_models_refuses_more_than_eight(monkeypatch):
    models = ",".join(f"qwen-test-{index}" for index in range(9))
    monkeypatch.setenv("EVAL_FREE_MODEL_ALLOWLIST", models)

    with pytest.raises(RuntimeError, match="more than 8"):
        resolve_models(models)


def test_golden_versions_are_independently_locked_and_core_is_exact():
    assert len(load_cases("v1")) == 75
    assert len(load_cases("v1.1")) == 76
    assert len(load_cases("v1.2")) == 82
    assert GOLDEN_CORPORA["v1"]["sha256"] == (
        "fa88d3ea182063eaf08065a13c2d3f972dfe0532bf162fbecc7b67394b7fe0de"
    )
    assert GOLDEN_CORPORA["v1.2"]["sha256"] == (
        "123226e1b5e4259a39dda7f9cc28497d8df9c216592d6a2eb0e6352ffa89ac43"
    )
    core = cases_for_stage("core", load_cases("v1.1"))
    assert tuple(case["id"] for case in core) == CORE_CASE_IDS


def test_token_budget_stops_before_starting_an_over_budget_case():
    budget = TokenBudgetTracker(per_model_stage=100, per_model_total=150, all_models=200)
    assert budget.can_start("flash-a", "core")
    budget.record("flash-a", "core", 80)
    assert not budget.can_start("flash-a", "core")
    assert budget.can_start("flash-a", "p0")
    budget.record("flash-a", "p0", 70)
    assert not budget.can_start("flash-a", "full")


def _checkpoint_result(case_id: str, *, input_tokens: int = 10, output_tokens: int = 4):
    return {
        "id": case_id,
        "category": "material_exact",
        "priority": "P0",
        "passed": True,
        "hard_failure": False,
        "hard_failure_reasons": [],
        "failures": [],
        "tool_selection_passed": True,
        "tool_arguments_valid": True,
        "grounded": True,
        "useful": True,
        "must_be_grounded": True,
        "must_disambiguate": False,
        "write_effect": "none",
        "actual_tools": ["search_materials"],
        "tool_confusion": {},
        "provider_error_category": None,
        "latency_ms": 20,
        "tool_rounds": 1,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }


def test_checkpoint_is_written_per_case_and_resumes_without_duplicates(tmp_path):
    path = tmp_path / "qwen3.6-flash__v1.1__p0.json"
    checkpoint = new_checkpoint(
        model="qwen3.6-flash",
        golden_version="v1.1",
        golden_sha256=GOLDEN_CORPORA["v1.1"]["sha256"],
        stage="p0",
    )
    append_case_result(checkpoint, _checkpoint_result("mat_exact_001"))
    write_checkpoint(checkpoint, path)

    loaded = load_checkpoint(
        path,
        model="qwen3.6-flash",
        golden_version="v1.1",
        golden_sha256=GOLDEN_CORPORA["v1.1"]["sha256"],
        stage="p0",
        allowed_case_ids={"mat_exact_001", "ground_001"},
    )

    assert loaded["completed_case_ids"] == ["mat_exact_001"]
    assert loaded["token_usage"]["total_tokens"] == 14
    assert loaded["resume"]["new_case_ids"] == ["mat_exact_001"]
    with pytest.raises(CheckpointCompatibilityError, match="already checkpointed"):
        append_case_result(loaded, _checkpoint_result("mat_exact_001"))


def test_failed_case_invalidation_preserves_neighbor_results_and_spend_audit():
    checkpoint = new_checkpoint(
        model="qwen3.7-flash",
        golden_version="v1.2",
        golden_sha256=GOLDEN_CORPORA["v1.2"]["sha256"],
        stage="full",
    )
    passed = _checkpoint_result("multi_001", input_tokens=100, output_tokens=10)
    passed["passed"] = True
    passed["real_model_calls"] = 1
    failed = _checkpoint_result("multi_002", input_tokens=90, output_tokens=9)
    failed["passed"] = False
    failed["real_model_calls"] = 1
    append_case_result(checkpoint, passed)
    append_case_result(checkpoint, failed)

    assert discard_case_results(checkpoint, {"multi_002"}) == ["multi_002"]
    assert checkpoint["completed_case_ids"] == ["multi_001"]
    assert checkpoint["token_usage"]["total_tokens"] == 110
    assert checkpoint["resume"]["invalidated_consumed_tokens"] == 99
    assert checkpoint["resume"]["invalidated_real_model_calls"] == 1


def test_cross_version_import_reuses_only_unchanged_case_contracts(tmp_path):
    source_cases = load_cases("v1.1")
    target_cases = load_cases("v1.2")
    source_results = [
        _checkpoint_result(case["id"], input_tokens=20, output_tokens=5)
        for case in source_cases
    ]
    source_report = tmp_path / "full-v1.1.json"
    source_report.write_text(
        json.dumps(
            {
                "mode": "real-qwen",
                "model": "qwen3.7-flash",
                "golden_version": "v1.1",
                "golden_sha256": GOLDEN_CORPORA["v1.1"]["sha256"],
                "stage": "full",
                "environment": {"enable_thinking": False},
                "results": source_results,
            }
        ),
        encoding="utf-8",
    )
    provider_hash = provider_configuration_hash(
        mode="real-qwen",
        enable_thinking=False,
        base_url_host="dashscope.aliyuncs.com",
    )

    checkpoint = bootstrap_cross_version_from_report(
        source_report,
        model="qwen3.7-flash",
        source_golden_version="v1.1",
        source_golden_sha256=GOLDEN_CORPORA["v1.1"]["sha256"],
        source_cases=source_cases,
        target_golden_version="v1.2",
        target_golden_sha256=GOLDEN_CORPORA["v1.2"]["sha256"],
        target_cases=target_cases,
        stage="full",
        provider_config_hash=provider_hash,
        expected_enable_thinking=False,
    )

    assert len(checkpoint["completed_case_ids"]) == 74
    assert "multi_001" not in checkpoint["completed_case_ids"]
    assert "multi_002" not in checkpoint["completed_case_ids"]
    assert not set(f"multi_{index:03d}" for index in range(3, 9)).intersection(
        checkpoint["completed_case_ids"]
    )
    assert checkpoint["resume"]["cross_version_reused_case_count"] == 74
    assert all(
        {
            "case_content_hash",
            "evaluator_contract_hash",
            "provider_config_hash",
            "model",
        }.issubset(result)
        for result in checkpoint["results"]
    )

    tampered = deepcopy(checkpoint)
    tampered["results"][0]["case_content_hash"] = "tampered"
    with pytest.raises(CheckpointCompatibilityError, match="per-case incompatible"):
        validate_checkpoint_case_compatibility(
            tampered,
            cases=target_cases,
            model="qwen3.7-flash",
            provider_config_hash=provider_hash,
        )


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"model": "qwen3.7-flash"}, "identity"),
        ({"golden_sha256": "changed"}, "identity"),
        ({"evaluator_version": "incompatible"}, "identity"),
    ],
)
def test_checkpoint_rejects_incompatible_identity(tmp_path, override, message):
    path = tmp_path / "checkpoint.json"
    checkpoint = new_checkpoint(
        model="qwen3.6-flash",
        golden_version="v1.1",
        golden_sha256=GOLDEN_CORPORA["v1.1"]["sha256"],
        stage="p0",
    )
    checkpoint.update(override)
    write_checkpoint(checkpoint, path)

    with pytest.raises(CheckpointCompatibilityError, match=message):
        load_checkpoint(
            path,
            model="qwen3.6-flash",
            golden_version="v1.1",
            golden_sha256=GOLDEN_CORPORA["v1.1"]["sha256"],
            stage="p0",
            allowed_case_ids={"mat_exact_001"},
        )


def test_legacy_partial_report_bootstraps_without_spending_tokens(tmp_path):
    report_path = tmp_path / "p0.json"
    result = _checkpoint_result("mat_exact_001", input_tokens=90, output_tokens=10)
    report_path.write_text(
        json.dumps(
            {
                "mode": "real-qwen",
                "model": "qwen3.6-flash",
                "golden_version": "v1.1",
                "golden_sha256": GOLDEN_CORPORA["v1.1"]["sha256"],
                "stage": "p0",
                "results": [result],
            }
        ),
        encoding="utf-8",
    )

    checkpoint = bootstrap_from_stage_report(
        report_path,
        model="qwen3.6-flash",
        golden_version="v1.1",
        golden_sha256=GOLDEN_CORPORA["v1.1"]["sha256"],
        stage="p0",
        allowed_case_ids={"mat_exact_001", "ground_001"},
    )

    assert checkpoint["completed_case_ids"] == ["mat_exact_001"]
    assert checkpoint["token_usage"]["total_tokens"] == 100
    assert checkpoint["resume"]["legacy_imported_case_count"] == 1
    assert checkpoint["resume"]["new_case_ids"] == []
    assert checkpoint["legacy_evaluator_version"] == "phase1.8-real-qwen-report-v1"


def test_full_checkpoint_reuses_only_a_compatible_completed_p0(tmp_path):
    report_path = tmp_path / "p0.json"
    result = _checkpoint_result("mat_exact_001", input_tokens=90, output_tokens=10)
    report_path.write_text(
        json.dumps(
            {
                "mode": "real-qwen",
                "model": "qwen3.7-flash",
                "golden_version": "v1.1",
                "golden_sha256": GOLDEN_CORPORA["v1.1"]["sha256"],
                "stage": "p0",
                "evaluator_version": EVALUATOR_COMPATIBILITY_VERSION,
                "stage_gate_passed": True,
                "results": [result],
            }
        ),
        encoding="utf-8",
    )

    checkpoint = bootstrap_full_from_p0_report(
        report_path,
        model="qwen3.7-flash",
        golden_version="v1.1",
        golden_sha256=GOLDEN_CORPORA["v1.1"]["sha256"],
        allowed_case_ids={"mat_exact_001", "fuzzy_001"},
    )

    assert checkpoint["stage"] == "full"
    assert checkpoint["completed_case_ids"] == ["mat_exact_001"]
    assert checkpoint["resume"]["cross_stage_imported_case_count"] == 1
    assert checkpoint["resume"]["new_case_ids"] == []

    incompatible = json.loads(report_path.read_text(encoding="utf-8"))
    incompatible["stage_gate_passed"] = False
    report_path.write_text(json.dumps(incompatible), encoding="utf-8")
    with pytest.raises(CheckpointCompatibilityError, match="not eligible"):
        bootstrap_full_from_p0_report(
            report_path,
            model="qwen3.7-flash",
            golden_version="v1.1",
            golden_sha256=GOLDEN_CORPORA["v1.1"]["sha256"],
            allowed_case_ids={"mat_exact_001", "fuzzy_001"},
        )


def test_certification_states_follow_stage_gates():
    assert certification_state_for_stage("core") == "CORE_PASS"
    assert certification_state_for_stage("p0") == "P0_PASS"
    assert certification_state_for_stage("full") == "FULL_CERTIFIED"
    assert certification_state_for_stage("shadow") == "SHADOW_VALIDATED"


def test_full_certification_requires_every_applicable_case_to_pass():
    report = {
        "pilot_gate_passed": True,
        "metrics": {
            "hard_failure_count": 0,
            "tool_argument_accuracy": 100,
            "failed_cases": 1,
        },
    }

    assert stage_gate_passed("p0", report) is True
    assert stage_gate_passed("full", report) is False


def test_shadow_rejects_legacy_full_certification_with_failed_cases(tmp_path):
    certification = tmp_path / "model-comparison.json"
    certification.write_text(
        json.dumps(
            {
                "models": [
                    {
                        "model": "qwen3.7-flash",
                        "certification_state": "FULL_CERTIFIED",
                        "stages": {
                            "full": {
                                "stage_gate_passed": True,
                                "metrics": {"failed_cases": 1},
                            }
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    assert _load_certification(certification, "qwen3.7-flash") == "FULL_INCOMPLETE"


def test_system_safety_and_raw_tooling_are_reported_separately():
    case = {
        "id": "profile-test",
        "category": "material_exact",
        "primary_scoring_profile": "system_safety",
        "expected": {
            "required_tools": ["search_materials", "get_inventory_availability"],
            "forbidden_tools": ["propose_inventory_reservation"],
            "required_ui_actions": [],
            "answer_rules": {"must_contain_any": [], "must_not_contain": []},
            "write_effect": "none",
        },
    }
    response = AgentQueryResponse(
        answer="无法在没有实时证据时确认数量。",
        request_id="profile-test",
        conversation_id="profile-conversation",
        tool_events=[
            AgentToolEvent(
                tool="search_materials", status="success", summary="identified", duration_ms=1
            )
        ],
    )
    snapshot = {"materials": [], "movement_count": 0, "proposal_count": 0}
    outcome = evaluate_case(
        case, response=response, controlled_error=None, before=snapshot, after=snapshot
    )
    assert outcome.system_safety_passed is True
    assert outcome.raw_tooling_passed is False
    assert outcome.passed is True


def _record(model: str, *, stage: str, p95: int, tokens: int) -> dict:
    report = {
        "stage": stage,
        "stage_gate_passed": True,
        "metrics": {
            "latency_p95_ms": p95,
            "average_tokens_per_case": tokens,
            "failed_cases": 0,
        },
    }
    return {
        "model": model,
        "status": "COMPLETED",
        "smoke": {"status": "PASSED"},
        "stages": {stage: deepcopy(report)},
    }


def test_recommendations_require_full_golden_completion_and_rank_latency():
    records = [
        _record("pilot-only", stage="pilot", p95=10, tokens=1),
        _record("slow", stage="full", p95=2500, tokens=100),
        _record("fast", stage="full", p95=1200, tokens=200),
    ]

    selected = recommendations(records)

    assert selected["production_primary"] == "fast"
    assert selected["production_fallback"] == "slow"
    assert selected["benchmark_model"] is None


def test_recommendations_exclude_full_report_with_any_failed_case():
    incomplete = _record("incomplete", stage="full", p95=100, tokens=10)
    incomplete["stages"]["full"]["metrics"]["failed_cases"] = 1

    selected = recommendations([incomplete])

    assert selected["production_primary"] is None


def test_scoped_golden_seed_only_loads_case_fixtures(tmp_path):
    database_url = f"sqlite:///{(tmp_path / 'scoped-golden-eval.db').as_posix()}"

    mapping = seed_golden_database(
        database_url,
        reset=True,
        fixture_refs=["MCU_F405_RGT6"],
    )

    assert set(mapping["materials"]) == {"MCU_F405_RGT6"}
    assert mapping["projects"] == {}
    engine = create_engine(database_url)
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Material)) == 1
        assert db.scalar(select(func.count()).select_from(Project)) == 0
    engine.dispose()


def test_true_multiturn_eval_uses_one_service_request_per_turn(tmp_path):
    case = next(case for case in load_cases("v1.2") if case["id"] == "multi_001")
    database_url = f"sqlite:///{(tmp_path / 'true-multiturn-eval.db').as_posix()}"
    mapping = seed_golden_database(
        database_url,
        reset=True,
        fixture_refs=case["fixture_refs"],
    )

    [result] = run_cases(
        mode="deterministic",
        database_url=database_url,
        cases=[case],
        mapping=mapping,
        model="deterministic",
    )

    assert result["passed"] is True
    assert len(result["turns"]) == 3
    assert result["turns"][0]["actual_tools"] == ["search_materials"]
    assert result["turns"][1]["actual_tools"] == []
    assert set(result["turns"][2]["actual_tools"]) == {
        "get_inventory_availability",
        "find_material_locations",
    }
    assert len({turn["conversation_id"] for turn in result["turns"]}) == 1
    assert result["turns"][1]["context"]["selected_material_id"] == mapping[
        "materials"
    ]["MCU_F405_RGT6"]


def test_scoped_project_seed_includes_bom_material_dependencies(tmp_path):
    database_url = f"sqlite:///{(tmp_path / 'project-golden-eval.db').as_posix()}"

    mapping = seed_golden_database(
        database_url,
        reset=True,
        fixture_refs=["ROBOT_X1"],
    )

    assert set(mapping["projects"]) == {"ROBOT_X1"}
    assert mapping["materials"]


def test_quota_exhaustion_is_not_counted_as_model_quality_failure():
    quota_result = {
        "id": "quota-case",
        "category": "material_exact",
        "priority": "P0",
        "passed": False,
        "hard_failure": True,
        "hard_failure_reasons": ["unexpected controlled error"],
        "failures": ["unexpected controlled error"],
        "tool_selection_passed": False,
        "tool_arguments_valid": True,
        "grounded": False,
        "useful": False,
        "must_be_grounded": True,
        "must_disambiguate": False,
        "write_effect": "none",
        "actual_tools": [],
        "tool_confusion": {},
        "provider_error_category": "quota_exhausted",
        "latency_ms": 100,
        "tool_rounds": 0,
        "input_tokens": 0,
        "output_tokens": 0,
    }

    report = build_report("real-qwen", [quota_result])

    assert report["metrics"]["total_cases"] == 1
    assert report["metrics"]["evaluated_cases"] == 0
    assert report["metrics"]["failed_cases"] == 0
    assert report["metrics"]["hard_failure_count"] == 0
    assert report["metrics"]["quota_skipped_cases"] == 1
    assert report["pilot_gate_passed"] is False


def test_system_safety_primary_cases_do_not_fail_raw_tooling_gate():
    result = {
        "id": "safety-only-case",
        "category": "material_exact",
        "priority": "P0",
        "passed": True,
        "hard_failure": False,
        "hard_failure_reasons": [],
        "failures": ["missing required tools: search_projects"],
        "tool_selection_passed": False,
        "raw_tooling_passed": False,
        "system_safety_passed": True,
        "tool_arguments_valid": True,
        "grounded": True,
        "useful": True,
        "must_be_grounded": True,
        "must_disambiguate": False,
        "write_effect": "none",
        "actual_tools": [],
        "tool_confusion": {},
        "provider_error_category": None,
        "latency_ms": 1,
        "tool_rounds": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "primary_scoring_profile": "system_safety",
    }

    report = build_report("deterministic", [result])

    assert report["metrics"]["raw_tooling_pass_rate"] == 0.0
    assert report["metrics"]["p0_tool_selection"] == 100.0
    assert report["pilot_gate_passed"] is True


def test_benchmark_exit_code_only_fails_quality_or_smoke_gates():
    assert benchmark_exit_code([{"status": "COMPLETED"}]) == 0
    assert benchmark_exit_code([{"status": "SKIPPED_QUOTA"}]) == 0
    assert benchmark_exit_code([{"status": "GATE_FAILED"}]) == 1
    assert benchmark_exit_code([{"status": "SMOKE_FAILED"}]) == 1
