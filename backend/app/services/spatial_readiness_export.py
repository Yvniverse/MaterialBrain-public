"""P4 data from observed mission events; absent sensors/velocity stay unknown."""

from app.schemas.spatial_readiness import ObservationFrame, TrajectorySegment


def observed_readiness_bundle(graph: dict) -> dict:
    steps = graph["robot_state"].get("observed_steps", [])
    events = [
        step["outcome"]
        for step in steps
        if step.get("source") == "observed_execution_event" and step.get("outcome", {}).get("pose")
    ]
    if not events:
        events = [event for event in graph["events"] if event.get("pose")]
    if not events:
        return {
            "status": "NO_OBSERVED_POSES",
            "trajectory": None,
            "observations": [],
            "training_performed": False,
            "synthetic_unobserved_frames": 0,
        }
    samples, observations = [], []
    for event in events:
        details = event.get("details", {})
        samples.append(
            {
                "timestamp": event["timestamp"],
                "pose": event["pose"],
                "linear_mps": details.get("linear_mps"),
                "angular_rps": details.get("angular_rps"),
                "velocity_source": "observed"
                if "linear_mps" in details and "angular_rps" in details
                else "unavailable",
                "dynamic_events": [event["type"]]
                if event["type"] in {"obstacle_added", "replanning", "recovery"}
                else [],
            }
        )
        observations.append(
            ObservationFrame(
                observation_id=event["event_id"],
                timestamp=event["timestamp"],
                map_id=graph["map_id"],
                map_revision=graph["map_revision"],
                pose=event["pose"],
            ).model_dump(mode="json")
        )
    trajectory = TrajectorySegment(
        segment_id=graph["mission_id"] + ":observed",
        mission_id=graph["mission_id"],
        skill="navigate_mission",
        map_id=graph["map_id"],
        map_revision=graph["map_revision"],
        samples=samples,
    ).model_dump(mode="json")
    return {
        "status": "OBSERVED",
        "trajectory": trajectory,
        "observations": observations,
        "sensor_refs_available": False,
        "velocity_unavailable": any(
            sample["velocity_source"] == "unavailable" for sample in samples
        ),
        "training_performed": False,
        "synthetic_unobserved_frames": 0,
    }
