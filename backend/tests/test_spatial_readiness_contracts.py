import math

from app.schemas.spatial_readiness import MapDeltaProposal
from app.services.spatial_readiness import MapUpdateValidator, bounded_semantic_cost
from app.services.spatial_readiness_export import observed_readiness_bundle


def test_p4_export_uses_observed_poses_and_keeps_missing_velocity_unknown():
    graph = {
        "mission_id": "m",
        "map_id": "lab",
        "map_revision": "v",
        "robot_state": {},
        "events": [
            {
                "event_id": "m:1",
                "timestamp": "2026-10-04T00:00:00Z",
                "type": "feedback",
                "pose": {"x": 1, "y": 2, "yaw": 0},
                "details": {},
            }
        ],
    }
    result = observed_readiness_bundle(graph)
    assert result["trajectory"]["samples"][0]["pose"] == graph["events"][0]["pose"]
    assert result["trajectory"]["samples"][0]["linear_mps"] is None
    assert result["velocity_unavailable"]
    assert result["observations"][0]["rgb_refs"] == []
    assert not result["training_performed"]
    graph["events"] = []
    assert observed_readiness_bundle(graph)["status"] == "NO_OBSERVED_POSES"


def test_learned_cost_cannot_override_legality():
    assert math.isinf(bounded_semantic_cost({}, 0, legal=False))
    assert bounded_semantic_cost({}, 1000, legal=True) == 50


def test_mapping_proposal_requires_current_topology_and_review():
    proposal = MapDeltaProposal(
        proposal_id="p",
        map_id="lab",
        base_revision="old",
        confidence=0.9,
        evidence_refs=["scan:1"],
        topology_changes=[{"from": "unknown", "to": "b"}],
    )
    result = MapUpdateValidator().validate(
        proposal, {"map_id": "lab", "revision": "new", "route_graph": {"nodes": [{"id": "b"}]}}
    )
    assert not result["valid"]
    assert result["automatic_write"] is False
    assert result["reasons"] == ["STALE_MAP_REVISION", "UNREGISTERED_TOPOLOGY_ENDPOINT"]


def test_new_topology_cannot_cross_registered_physical_obstacle():
    proposal = MapDeltaProposal(
        proposal_id="p",
        map_id="lab",
        base_revision="v",
        confidence=1,
        evidence_refs=["scan:1"],
        topology_changes=[{"from": "a", "to": "b"}],
    )
    result = MapUpdateValidator().validate(
        proposal,
        {
            "map_id": "lab",
            "revision": "v",
            "route_graph": {"nodes": [{"id": "a", "x": 0, "y": 1}, {"id": "b", "x": 3, "y": 1}]},
            "geometry": {
                "assets": [
                    {
                        "footprint": {
                            "type": "Polygon",
                            "coordinates": [[[1, 0], [2, 0], [2, 2], [1, 2], [1, 0]]],
                        },
                        "collidable": True,
                    }
                ]
            },
        },
    )
    assert result["reasons"] == ["TOPOLOGY_CROSSES_OBSTACLE"]
