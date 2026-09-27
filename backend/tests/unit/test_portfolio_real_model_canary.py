import hashlib
import json
from pathlib import Path

from evals.run_portfolio_real_model_canary import CANARY_SHA256


def test_portfolio_canary_identity_and_scope():
    path = (
        Path(__file__).resolve().parents[2]
        / "evals"
        / "production"
        / "portfolio_post_rebuild_real_model_canary_v1.jsonl"
    )
    raw = path.read_bytes()
    cases = [json.loads(line) for line in raw.decode().splitlines() if line.strip()]
    assert hashlib.sha256(raw).hexdigest() == CANARY_SHA256
    assert len(cases) == 12
    assert {case["expect"] for case in cases}.issuperset(
        {
            "product_build",
            "project_aware_build",
            "project_bom",
            "inventory_location",
            "component_search",
            "product_multiturn",
            "component_multiturn",
            "linked_project_build",
            "reset_safety",
            "replacement_candidate_only",
            "product_bom_grounded",
        }
    )
