"""Resettable, offline, verified interaction over the accepted V4 graph/planner.

This environment models mission-level observations and simulated human actions.
It does not emulate low-level Nav2 physics, settle inventory, or claim hardware
results. Every move still uses a legal directed path from the exported graph.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .vendor.planner import plan_mission
from .vendor.schema import SpatialMapSnapshot
from .vendor.semantic import RobotProfile, SemanticGraph
from .vendor.skills import skill_manifest, validate_skill_selection


def _canonical(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


class _Reject(ValueError):
    def __init__(self, code: str, category: str = "invalid"):
        super().__init__(code)
        self.code, self.category = code, category


class WarehouseTrainingEnv:
    """Local policy → tool → observation environment, with no network dependencies.

    ``expert_action`` is deliberately separate from ``observe``. Environment
    rewards are computed from this state machine, never from a model-provided
    reward, completion claim, or copied training label.
    """

    verifier_version = "warehouse-fast-symbolic-v1"
    _plan_cache: OrderedDict[str, dict] = OrderedDict()
    _plan_cache_limit = 4096
    _event_types = {"blocked_path", "reopen", "wrong_scan", "low_battery", "handoff_delay"}

    def __init__(self, snapshot_path: str | Path, mode: str = "fast_symbolic"):
        if mode != "fast_symbolic":
            raise ValueError("Only the offline fast_symbolic training mode is supported")
        path = Path(snapshot_path)
        self.snapshot_path = path
        self.snapshot = json.loads(path.read_text(encoding="utf-8"))
        if self.snapshot.get("contract_version") != "materialbrain-warehousebench-v1":
            raise ValueError("Unsupported WarehouseBench snapshot contract")
        self.map = SpatialMapSnapshot.model_validate(self.snapshot["map"]).model_dump(mode="json")
        self.skills = skill_manifest()
        if _canonical(self.snapshot["skills"]) != _canonical(self.skills):
            raise ValueError("Snapshot skill schemas differ from the accepted thirteen skills")
        for item in self.snapshot.get("planner_identity", {}).get("files", []):
            bundled = Path(__file__).parent / "vendor" / item["bundled"]
            if hashlib.sha256(bundled.read_bytes()).hexdigest() != item["bundled_sha256"]:
                raise ValueError("Accepted pure planner source hash mismatch")
        manifest_path = path.with_name(path.stem + ".manifest.json")
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest["files"].get(path.name) != hashlib.sha256(path.read_bytes()).hexdigest():
                raise ValueError("Immutable snapshot hash mismatch")
        self.docks = {dock["id"]: dock for dock in self.map["docks"]}
        self.robot_profile = RobotProfile.from_snapshot(self.map, "MB-R01")
        self.mode = mode
        self._reset = False

    @staticmethod
    def _batches(goals: list[str]) -> list[list[str]]:
        """Do not manufacture new docks or revisit a completed dock in one mission."""
        batches, current = [], []
        for goal in goals:
            if goal in current or len(current) == 9:
                batches.append(current)
                current = []
            current.append(goal)
        if current or not batches:
            batches.append(current)
        return batches

    def reset(self, scenario: dict) -> dict:
        if not isinstance(scenario, dict):
            raise ValueError("Scenario must be a JSON object")
        _canonical(scenario)
        required = {
            "scenario_template",
            "scenario_seed",
            "inventory_snapshot_id",
            "instruction",
            "goal_ids",
            "profile",
            "battery_pct",
            "payload_kg",
            "service_s",
            "priority",
            "time_window_s",
            "stock_status",
            "ambiguity",
            "events",
            "max_steps",
        }
        if required - scenario.keys():
            raise ValueError(
                "Scenario fields missing: " + ",".join(sorted(required - scenario.keys()))
            )
        if scenario["scenario_template"] not in {f"T{i}" for i in range(6)}:
            raise ValueError("Scenario template must be T0–T5")
        goals = scenario["goal_ids"]
        if (
            not isinstance(goals, list)
            or not all(isinstance(g, str) for g in goals)
            or len(goals) > 20
        ):
            raise ValueError("Scenario needs zero to twenty registered goal occurrences")
        if not set(goals).issubset(self.docks):
            raise ValueError("Scenario contains an unregistered dock")
        if any(
            self.docks[g].get("kind") == "home" or "charge" in self.docks[g].get("capabilities", [])
            for g in goals
        ):
            raise ValueError("Requested handoffs cannot be HOME or CHARGER")
        if scenario["profile"] not in {"fastest", "safest", "esd_safe"}:
            raise ValueError("Unknown semantic profile")
        for field, low, high in (
            ("battery_pct", 0, 100),
            ("payload_kg", 0, 1000),
            ("service_s", 0, 86400),
            ("priority", 0, 100),
        ):
            value = scenario[field]
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or not low <= value <= high
            ):
                raise ValueError("Invalid scenario field: " + field)
        if (
            not isinstance(scenario["max_steps"], int)
            or isinstance(scenario["max_steps"], bool)
            or not 1 <= scenario["max_steps"] <= 512
        ):
            raise ValueError("Invalid episode step budget")
        window = scenario["time_window_s"]
        if window is not None and (
            not isinstance(window, (list, tuple))
            or len(window) != 2
            or not 0 <= window[0] <= window[1]
        ):
            raise ValueError("Invalid scenario time window")
        if scenario["stock_status"] not in {"full", "partial", "missing"}:
            raise ValueError("Unknown synthetic stock status")
        events = scenario["events"]
        if not isinstance(events, list):
            raise ValueError("Scenario events must be a list")
        edge_ids = {edge["id"] for edge in self.map["route_graph"]["edges"]}
        for event in events:
            if not isinstance(event, dict) or event.get("type") not in self._event_types:
                raise ValueError("Unknown exogenous event type")
            if not isinstance(event.get("at_step", 0), int) or event.get("at_step", 0) < 0:
                raise ValueError("Invalid event step")
            if event.get("goal_id") and event["goal_id"] not in self.docks:
                raise ValueError("Event goal is not registered")
            if not set(event.get("edge_ids", [])).issubset(edge_ids):
                raise ValueError("Event edge is not registered")
        self.scenario = copy.deepcopy(scenario)
        self.group_id = _digest(
            [
                self.map["revision"],
                scenario["scenario_template"],
                scenario["scenario_seed"],
                scenario["inventory_snapshot_id"],
            ]
        )
        self.mission_batches = self._batches(goals)
        self.batch_index = 0
        self.step_count = 0
        self.virtual_time_s = 0.0
        self.max_steps = scenario["max_steps"]
        self.total_reward = 0.0
        self.reward_totals = {
            name: 0.0
            for name in (
                "terminal",
                "progress",
                "safety",
                "unauthorized",
                "invalid",
                "no_progress",
                "tool_cost",
            )
        }
        self.trace: list[dict] = []
        self.exogenous_trace: list[dict] = []
        self.metrics = {
            "task_success": False,
            "clarification_success": False,
            "denial_success": False,
            "invalid_actions": 0,
            "unauthorized_actions": 0,
            "safety_violations": 0,
            "tool_calls": 0,
            "recovery_events": 0,
            "recovery_successes": 0,
            "wrong_scans": 0,
            "route_feasible": None,
            "route_cost_s": 0.0,
            "reference_cost_s": 0.0,
            "completed_handoffs": 0,
            "inventory_writes": 0,
        }
        self._event_done: set[int] = set()
        self._overlays: list[dict] = []
        self._wrong_readings: dict[str, float] = {}
        self._handoff_until: dict[str, float] = {}
        self._terminated = self._truncated = False
        self.completed_occurrences: list[dict] = []
        self.bom_resolved = self.inventory_checked = self.locations_resolved = (
            self.context_queried
        ) = False
        self._plan: dict | None = None
        self._mission_started_s: float | None = None
        self._plan_pointer = 0
        self._completed: list[str] = []
        self._arrival_goal: str | None = None
        self._scan_verified = False
        self._returned_home = False
        self._recovery: dict | None = None
        self._last_verifier = "RESET"
        self._successful_charges: set[str] = set()
        self.battery_pct = float(scenario["battery_pct"])
        self.payload_kg = 0.0
        start_id = scenario.get("start_goal_id", "HOME")
        if start_id not in self.docks:
            raise ValueError("Initial pose must come from a registered dock")
        self.pose = copy.deepcopy(self.docks[start_id]["pose"])
        if "charge" in self.docks[start_id].get("capabilities", []):
            self._arrival_goal = start_id
        self._mission_id = self._owned_mission_id()
        self._epoch = datetime.fromisoformat(
            self.snapshot.get("exported_at", "2026-10-05T00:00:00+00:00").replace("Z", "+00:00")
        )
        if self._epoch.tzinfo is None:
            self._epoch = self._epoch.replace(tzinfo=timezone.utc)
        self._reset = True
        # Publish events due at the next decision boundary before the model is
        # asked to choose. Hidden same-step event injection would create an
        # impossible oracle label and is not interactive feedback.
        self._inject_events(1)
        return self.observe()

    def _owned_mission_id(self) -> str:
        return "P3-" + self.group_id[:20] + "-" + str(self.batch_index)

    @property
    def current_goals(self) -> list[str]:
        return self.mission_batches[self.batch_index][:]

    @property
    def plan(self) -> dict | None:
        return copy.deepcopy(self._plan)

    @property
    def terminated(self) -> bool:
        return self._terminated

    @property
    def truncated(self) -> bool:
        return self._truncated

    def _active_stop(self) -> dict | None:
        if (
            self._plan
            and self._plan["status"] == "READY"
            and self._plan_pointer < len(self._plan["stops"])
        ):
            return self._plan["stops"][self._plan_pointer]
        return None

    def _state(self) -> dict:
        return {
            "mission": self._mission_id,
            "batch_index": self.batch_index,
            "step_count": self.step_count,
            "virtual_time_s": round(self.virtual_time_s, 6),
            "battery_pct": round(self.battery_pct, 9),
            "payload_kg": round(self.payload_kg, 9),
            "pose": self.pose,
            "completed": self._completed,
            "completed_occurrences": self.completed_occurrences,
            "arrival": self._arrival_goal,
            "scan_verified": self._scan_verified,
            "returned_home": self._returned_home,
            "grounding": [
                self.bom_resolved,
                self.inventory_checked,
                self.locations_resolved,
                self.context_queried,
            ],
            "plan": self._plan,
            "plan_pointer": self._plan_pointer,
            "recovery": self._recovery,
            "mission_started_s": self._mission_started_s,
            "overlays": self._overlays,
            "wrong_readings": self._wrong_readings,
            "handoff_until": self._handoff_until,
            "event_done": sorted(self._event_done),
            "terminated": self._terminated,
            "truncated": self._truncated,
        }

    def state_hash(self) -> str:
        return _digest(self._state())

    def observe(self) -> dict:
        if not self._reset:
            raise RuntimeError("reset(scenario) is required before observing")
        stop = self._active_stop()
        goal = self._arrival_goal
        wrong = goal in self._wrong_readings
        current_plan = None
        if self._plan:
            current_plan = {
                "status": self._plan["status"],
                "ordered_goal_ids": self._plan["ordered_goal_ids"],
                "stops": [
                    {
                        k: s.get(k)
                        for k in (
                            "goal_id",
                            "kind",
                            "service_s",
                            "demand_kg",
                            "arrival_s",
                            "time_window_s",
                        )
                    }
                    for s in self._plan["stops"]
                ],
                "violations": self._plan["violations"],
            }
        observation = {
            "schema_version": 1,
            "mode": self.mode,
            "map_id": self.map["map_id"],
            "map_revision": self.map["revision"],
            "mission_id": self._mission_id,
            "instruction": self.scenario["instruction"],
            "step": self.step_count,
            "virtual_time_s": round(self.virtual_time_s, 3),
            "mission_batch_index": self.batch_index,
            "mission_batch_count": len(self.mission_batches),
            "request": {
                "goal_ids": self.current_goals,
                "profile": self.scenario["profile"],
                "requested_occurrences": len(self.scenario["goal_ids"]),
                "ambiguity": self.scenario["ambiguity"],
                "demand_kg": self.scenario["payload_kg"],
                "payload_capacity_kg": self.scenario.get("payload_capacity_kg", 18.0),
                "service_s": self.scenario["service_s"],
                "priority": self.scenario["priority"],
                "time_window_s": self.scenario["time_window_s"],
            },
            "business": {
                "bom_resolved": self.bom_resolved,
                "inventory_checked": self.inventory_checked,
                "locations_resolved": self.locations_resolved,
                "stock_status": self.scenario["stock_status"]
                if self.inventory_checked
                else "unknown",
            },
            "context": {"queried": self.context_queried, "registered_goal_ids": sorted(self.docks)},
            "robot": {
                "pose": self.pose,
                "battery_pct": round(self.battery_pct, 6),
                "payload_kg": round(self.payload_kg, 6),
                "battery_reserve_pct": 15.0,
            },
            "execution": {
                "plan": current_plan,
                "completed_goal_ids": self._completed,
                "remaining_goal_ids": [g for g in self.current_goals if g not in self._completed],
                "active_stop": {"goal_id": stop["goal_id"], "kind": stop["kind"]} if stop else None,
                "arrived_goal_id": self._arrival_goal,
                "scan_verified": self._scan_verified,
                "returned_home": self._returned_home,
                "completed_occurrences": len(self.completed_occurrences),
            },
            "scanner": {
                "observed_code": "SYN-WRONG-READING" if wrong else goal,
                "expected_code": goal,
                "status": "wrong_reading" if wrong else "ready" if goal else "not_at_dock",
            },
            "handoff": {
                "available": not goal or goal not in self._handoff_until,
                "wait_until_s": self._handoff_until.get(goal),
            },
            "recovery": self._recovery,
            "active_closures": [
                {
                    "id": o["id"],
                    "edge_ids": o.get("edge_ids", []),
                    "reopen_at_s": o.get("reopen_at_s"),
                }
                for o in self._overlays
            ],
            "last_verifier_code": self._last_verifier,
            "available_skill_names": [skill["name"] for skill in self.skills],
            "terminated": self._terminated,
            "truncated": self._truncated,
        }
        return copy.deepcopy(observation)

    def _inject_events(self, at_step: int) -> None:
        for index, event in enumerate(self.scenario["events"]):
            if index in self._event_done or event.get("at_step", 0) > at_step:
                continue
            self._event_done.add(index)
            kind = event["type"]
            if kind == "blocked_path":
                goal = (
                    event.get("goal_id")
                    or (self._active_stop() or {}).get("goal_id")
                    or (self.current_goals[0] if self.current_goals else None)
                )
                edges = event.get("edge_ids")
                if edges is None:
                    node = self.docks[goal]["node_id"] if goal else None
                    edges = [
                        edge["id"]
                        for edge in self.map["route_graph"]["edges"]
                        if edge["to"] == node
                    ]
                overlay = {"id": f"P3-CLOSURE-{index}", "kind": "closure", "edge_ids": edges[:]}
                if event.get("reopen_after_s") is not None:
                    overlay["reopen_at_s"] = self.virtual_time_s + float(event["reopen_after_s"])
                self._overlays.append(overlay)
                self._recovery = {"code": "BLOCKED_PATH", "requires_replan": True}
                self.metrics["recovery_events"] += 1
            elif kind == "reopen":
                self._overlays = (
                    [o for o in self._overlays if o["id"] != event.get("closure_id")]
                    if event.get("closure_id")
                    else []
                )
                self._recovery = {"code": "PATH_REOPENED", "requires_replan": True}
            elif kind == "low_battery":
                self.battery_pct = float(event.get("battery_pct", 15.2))
                self._recovery = {"code": "LOW_BATTERY", "requires_replan": True}
                self.metrics["recovery_events"] += 1
            elif kind == "wrong_scan":
                goal = (
                    event.get("goal_id")
                    or (self._active_stop() or {}).get("goal_id")
                    or (self.current_goals[0] if self.current_goals else None)
                )
                if goal:
                    self._wrong_readings[goal] = self.virtual_time_s + float(
                        event.get("duration_s", 5)
                    )
                    self.metrics["recovery_events"] += 1
            elif kind == "handoff_delay":
                goal = (
                    event.get("goal_id")
                    or (self._active_stop() or {}).get("goal_id")
                    or (self.current_goals[0] if self.current_goals else None)
                )
                if goal:
                    self._handoff_until[goal] = self.virtual_time_s + float(
                        event.get("duration_s", 5)
                    )
                    self.metrics["recovery_events"] += 1
            self.exogenous_trace.append(
                {
                    "event_index": index,
                    "event": copy.deepcopy(event),
                    "step": at_step,
                    "virtual_time_s": self.virtual_time_s,
                }
            )

    def _recover_due(self) -> bool:
        changed = False
        for mapping in (self._wrong_readings, self._handoff_until):
            for goal, deadline in list(mapping.items()):
                if deadline <= self.virtual_time_s + 1e-9:
                    del mapping[goal]
                    changed = True
                    self.metrics["recovery_successes"] += 1
        remaining = []
        for overlay in self._overlays:
            if overlay.get("reopen_at_s", math.inf) <= self.virtual_time_s + 1e-9:
                changed = True
                self.metrics["recovery_successes"] += 1
                self._recovery = {"code": "PATH_REOPENED", "requires_replan": True}
            else:
                remaining.append(overlay)
        self._overlays = remaining
        return changed

    def _planning_snapshot(self) -> dict:
        snapshot = copy.deepcopy(self.map)
        snapshot["provenance"] = {
            **snapshot.get("provenance", {}),
            "evaluation_time": (self._epoch + timedelta(seconds=self.virtual_time_s)).isoformat(),
        }
        return snapshot

    def _request(self, *, completed: bool = False, profile: str | None = None) -> dict:
        goals = self.current_goals
        demand = float(self.scenario["payload_kg"]) / max(1, len(goals))
        window = self.scenario["time_window_s"]
        if completed and window is not None and self._mission_started_s is not None:
            elapsed = self.virtual_time_s - self._mission_started_s
            window = [max(0.0, window[0] - elapsed), max(0.0, window[1] - elapsed)]
        return {
            "map_id": self.map["map_id"],
            "map_revision": self.map["revision"],
            "profile": profile or self.scenario["profile"],
            "start_pose": self.pose,
            "goal_ids": goals,
            "completed_goal_ids": self._completed[:] if completed else [],
            "stops": [
                {
                    "goal_id": goal,
                    "demand_kg": demand,
                    "service_s": self.scenario["service_s"],
                    "priority": int(self.scenario["priority"]),
                    "time_window_s": window,
                }
                for goal in goals
            ],
            "constraints": {
                "payload_capacity_kg": self.scenario.get("payload_capacity_kg", 18.0),
                "battery_pct": self.battery_pct,
                "battery_reserve_pct": 15.0,
                "robot_class": "MB-R01",
            },
            "return_home": True,
            "dynamic_overlays": copy.deepcopy(self._overlays),
        }

    def _make_plan(self, *, completed: bool = False, profile: str | None = None) -> dict:
        snapshot = self._planning_snapshot()
        request = self._request(completed=completed, profile=profile)
        # Wall time is not a simulation fact. Absolute reference time matters
        # only when the frozen map has a persisted time-limited overlay.
        key = _digest(
            {
                "map": self.map["revision"],
                "persisted_overlays": snapshot["dynamic_overlays"],
                "request": request,
                "evaluation_time": snapshot["provenance"]["evaluation_time"]
                if snapshot["dynamic_overlays"]
                else None,
                "planner": self.snapshot.get("planner_identity", {}),
            }
        )
        if key in self._plan_cache:
            self._plan_cache.move_to_end(key)
            return copy.deepcopy(self._plan_cache[key])
        result = plan_mission(snapshot, request)
        result["metrics"].pop("planning_ms", None)
        self._plan_cache[key] = copy.deepcopy(result)
        if len(self._plan_cache) > self._plan_cache_limit:
            self._plan_cache.popitem(last=False)
        return result

    def _non_tool(self, decision: dict) -> tuple[str, float]:
        if (
            set(decision) != {"decision", "reason"}
            or decision.get("decision") not in {"clarify", "deny"}
            or not isinstance(decision.get("reason"), str)
            or not decision["reason"].strip()
        ):
            raise _Reject("NON_TOOL_SCHEMA")
        ambiguity = bool(self.scenario["ambiguity"]) or not self.current_goals
        partial = self.inventory_checked and self.scenario["stock_status"] == "partial"
        stock_missing = self.inventory_checked and self.scenario["stock_status"] == "missing"
        pending_reopen = any(
            event["type"] == "reopen" and index not in self._event_done
            for index, event in enumerate(self.scenario["events"])
        )
        temporary = any("reopen_at_s" in overlay for overlay in self._overlays) or pending_reopen
        infeasible = bool(self._plan and self._plan["status"] == "INFEASIBLE" and not temporary)
        if decision["decision"] == "clarify" and (ambiguity or partial):
            self.metrics["clarification_success"] = self.metrics["task_success"] = True
            self._terminated = True
            return "VERIFIED_CLARIFICATION", 10.0
        if decision["decision"] == "deny" and (stock_missing or infeasible):
            self.metrics["denial_success"] = self.metrics["task_success"] = True
            self._terminated = True
            return "VERIFIED_INFEASIBILITY", 10.0
        self._terminated = True
        return "PREMATURE_NON_TOOL_RESPONSE", -10.0

    def _require(self, condition: bool, code: str, category: str = "invalid") -> None:
        if not condition:
            raise _Reject(code, category)

    def _run_skill(self, action: dict) -> tuple[str, float]:
        name, args = action["name"], action["args"]
        if "mission_id" in args:
            self._require(
                args["mission_id"] == self._mission_id, "MISSION_OWNER_MISMATCH", "unauthorized"
            )
        if name in {"resolve_engineering_bom", "check_inventory", "resolve_pick_locations"}:
            self._require(
                args["instruction"] == self.scenario["instruction"],
                "BUSINESS_SCOPE_MISMATCH",
                "unauthorized",
            )
        if name == "resolve_engineering_bom":
            self._require(not self.bom_resolved, "BOM_ALREADY_RESOLVED")
            self._require(not bool(self.scenario["ambiguity"]), "BOM_SCOPE_REQUIRED")
            self.bom_resolved = True
            return "BOM_GROUNDED", 0.2
        if name == "check_inventory":
            self._require(self.bom_resolved, "BOM_DEPENDENCY_REQUIRED")
            self._require(not self.inventory_checked, "INVENTORY_ALREADY_CHECKED")
            self.inventory_checked = True
            return {
                "full": "INVENTORY_AVAILABLE",
                "partial": "INVENTORY_PARTIAL_UNKNOWN",
                "missing": "INSUFFICIENT_STOCK",
            }[self.scenario["stock_status"]], 0.2
        if name == "resolve_pick_locations":
            self._require(self.inventory_checked, "INVENTORY_DEPENDENCY_REQUIRED")
            self._require(self.scenario["stock_status"] == "full", "INVENTORY_NOT_READY")
            self._require(not self.locations_resolved, "LOCATIONS_ALREADY_RESOLVED")
            self.locations_resolved = True
            return "REGISTERED_LOCATIONS_GROUNDED", 0.2
        if name == "query_spatial_context":
            self._require(not self.context_queried, "CONTEXT_ALREADY_QUERIED")
            self.context_queried = True
            if self.scenario.get("terminal_skill") == name:
                self._terminated = True
                self.metrics["task_success"] = True
            return "SPATIAL_CONTEXT_READ", 0.2
        if name == "plan_mission":
            self._require(
                self.locations_resolved and self.context_queried, "GROUNDING_DEPENDENCIES_REQUIRED"
            )
            self._require(self._plan is None, "PLAN_ALREADY_EXISTS_USE_REPLAN")
            self._require(
                set(args["goal_ids"]) == set(self.current_goals),
                "REQUIRED_GOALS_CHANGED",
                "unauthorized",
            )
            self._require(
                args["profile"] == self.scenario["profile"],
                "PROFILE_SCOPE_MISMATCH",
                "unauthorized",
            )
            self._mission_started_s = self.virtual_time_s
            self._plan = self._make_plan()
            self._plan_pointer = 0
            self.metrics["route_feasible"] = self._plan["status"] == "READY"
            self.metrics["reference_cost_s"] += float(self._plan["metrics"].get("cost_s", 0))
            if self._plan["status"] == "READY":
                self._recovery = None
            return "PLAN_" + self._plan["status"], 0.5 if self._plan["status"] == "READY" else 0.0
        if name == "wait_or_yield":
            self._require(
                self.context_queried
                and (
                    self._recovery or self._overlays or self._wrong_readings or self._handoff_until
                ),
                "WAIT_HAS_NO_VERIFIED_CAUSE",
            )
            self.virtual_time_s += args["duration_s"]
            changed = self._recover_due()
            return "WAIT_EVENT_READY" if changed else "WAITING", 0.1 if changed else 0.0
        if name == "replan_remaining":
            self._require(
                self._plan is not None and self.locations_resolved, "PLAN_DEPENDENCY_REQUIRED"
            )
            self._require(
                self._recovery is not None or self._overlays, "REPLAN_HAS_NO_VERIFIED_CAUSE"
            )
            profile = args.get("profile") or self.scenario["profile"]
            self._require(
                profile == self.scenario["profile"], "PROFILE_SCOPE_MISMATCH", "unauthorized"
            )
            self._recover_due()
            previous_completed = self._completed[:]
            self._plan = self._make_plan(completed=True, profile=profile)
            self._require(
                self._plan["completed_goal_ids"] == previous_completed,
                "COMPLETED_STOPS_MUST_BE_PRESERVED",
                "safety",
            )
            self._plan_pointer = 0
            self.metrics["route_feasible"] = self._plan["status"] == "READY"
            if self._plan["status"] == "READY":
                self._recovery = None
                self.metrics["recovery_successes"] += 1
            else:
                self._recovery = {
                    "code": "BLOCKED_PATH" if self._overlays else "INFEASIBLE",
                    "requires_replan": bool(self._overlays),
                }
            return "REPLAN_" + self._plan["status"], 0.3 if self._plan["status"] == "READY" else 0.0
        if name == "charge_robot" and self._plan is None:
            self._require(
                self.context_queried and self._arrival_goal == args["goal_id"],
                "OBSERVED_CHARGER_ARRIVAL_REQUIRED",
            )
            self._require(
                self.battery_pct < 25
                and "charge" in self.docks[args["goal_id"]].get("capabilities", []),
                "CHARGE_NOT_REQUIRED",
            )
            self.virtual_time_s += (
                self.robot_profile.battery_wh
                * (100 - self.battery_pct)
                / 100
                / self.robot_profile.charge_w
                * 3600
            )
            self.battery_pct = 100.0
            return "OBSERVED_SIMULATION_CHARGE", 0.5
        self._require(self._plan is not None, "PLAN_DEPENDENCY_REQUIRED")
        if name == "summarize_mission":
            if set(self._completed) != set(self.current_goals) or not self._returned_home:
                self._terminated = True
                return "PREMATURE_SUCCESS_CLAIM", -10.0
            if self.batch_index + 1 < len(self.mission_batches):
                self.batch_index += 1
                self._mission_id = self._owned_mission_id()
                self._plan = None
                self._mission_started_s = None
                self._plan_pointer = 0
                self._completed = []
                self._arrival_goal = None
                self._scan_verified = self._returned_home = False
                self.payload_kg = 0.0
                return "MISSION_BATCH_COMPLETED", 0.5
            self._terminated = True
            self.metrics["task_success"] = (
                not self.metrics["safety_violations"] and not self.metrics["unauthorized_actions"]
            )
            return "VERIFIED_MISSION_COMPLETED", 10.0 if self.metrics["task_success"] else -10.0
        self._require(self._plan["status"] == "READY", "READY_PLAN_REQUIRED")
        if name == "return_home":
            self._require(
                set(self._completed) == set(self.current_goals), "ALL_HANDOFFS_REQUIRED", "safety"
            )
            self._require(not self._returned_home, "HOME_ALREADY_REACHED")
            self._move_to("HOME")
            self._returned_home = True
            self._arrival_goal = "HOME"
            self._scan_verified = False
            return "OBSERVED_HOME_ARRIVAL", 0.5
        stop = self._active_stop()
        self._require(stop is not None, "NO_PENDING_STOP")
        self._require(args.get("goal_id") == stop["goal_id"], "PLAN_STOP_DEPENDENCY_REQUIRED")
        if name == "navigate_mission":
            self._require(
                not self._recovery or not self._recovery.get("requires_replan"), "RECOVERY_REQUIRED"
            )
            self._require(self._arrival_goal != stop["goal_id"], "ALREADY_AT_STOP")
            self._move_to(stop["goal_id"])
            self._arrival_goal = stop["goal_id"]
            self._scan_verified = False
            return "OBSERVED_DOCK_ARRIVAL", 0.3
        self._require(self._arrival_goal == stop["goal_id"], "OBSERVED_ARRIVAL_REQUIRED")
        if name == "charge_robot":
            self._require(stop["kind"] == "charge", "PLANNED_CHARGE_REQUIRED")
            charge_key = (
                str(self.batch_index) + ":" + stop.get("occurrence_id", str(self._plan_pointer))
            )
            self._require(charge_key not in self._successful_charges, "CHARGE_ALREADY_COMPLETED")
            self._successful_charges.add(charge_key)
            self.virtual_time_s += float(stop["service_s"])
            self.battery_pct = 100.0
            self._plan_pointer += 1
            self._arrival_goal = None
            self._scan_verified = False
            return "OBSERVED_SIMULATION_CHARGE", 0.5
        self._require(stop["kind"] == "task", "HANDOFF_TASK_REQUIRED")
        if name == "confirm_scan":
            self._require(not self._scan_verified, "SCAN_ALREADY_VERIFIED")
            if stop["goal_id"] in self._wrong_readings or args["scan_code"] != stop["goal_id"]:
                self.metrics["wrong_scans"] += 1
                raise _Reject("WRONG_SCAN")
            self._scan_verified = True
            return "SCAN_VERIFIED_READ_ONLY", 0.3
        if name == "verify_handoff":
            self._require(self._scan_verified, "VERIFIED_SCAN_REQUIRED", "safety")
            self._require(stop["goal_id"] not in self._handoff_until, "HUMAN_HANDOFF_NOT_READY")
            self._require(stop["goal_id"] not in self._completed, "HANDOFF_ALREADY_COMPLETED")
            self._service(stop)
            self._completed.append(stop["goal_id"])
            self.completed_occurrences.append(
                {
                    "mission_id": self._mission_id,
                    "goal_id": stop["goal_id"],
                    "batch_index": self.batch_index,
                }
            )
            self.metrics["completed_handoffs"] += 1
            self._plan_pointer += 1
            self._arrival_goal = None
            self._scan_verified = False
            return "HUMAN_HANDOFF_VERIFIED", 1.0
        raise _Reject("SKILL_NOT_READY")

    def _graph(self) -> SemanticGraph:
        return SemanticGraph(
            self._planning_snapshot(),
            profile=self.scenario["profile"],
            robot=self.robot_profile,
            overlays=self._overlays,
        )

    def _move_to(self, goal: str) -> None:
        dock = self.docks[goal]
        path = None
        # The accepted planner has already computed the exact directed path for
        # each stop occurrence. Reuse that immutable segment while its start
        # pose and active closures still match; never rebuild a 1000-edge graph
        # for every policy token/tool step.
        if self._plan and self._plan["status"] == "READY" and not self._recovery:
            index = len(self._plan["segments"]) - 1 if goal == "HOME" else self._plan_pointer
            if 0 <= index < len(self._plan["segments"]):
                segment = self._plan["segments"][index]
                start = segment["poses"][0]
                if (
                    segment["to_goal_id"] == goal
                    and math.dist([start["x"], start["y"]], [self.pose["x"], self.pose["y"]]) < 1e-7
                ):
                    path = segment
        if path is None:
            graph = self._graph()
            try:
                node = graph.attach_start(self.pose)
            except ValueError as exc:
                raise _Reject(str(exc), "safety") from exc
            path = graph.path(node, dock["node_id"], start_pose=self.pose, end_pose=dock["pose"])
        self._require(path is not None, "BLOCKED_PATH", "safety")
        battery_after = self.battery_pct - 100 * path["energy_wh"] / self.robot_profile.battery_wh
        self._require(battery_after >= 15 - 1e-7, "BATTERY_RESERVE", "safety")
        self.pose = copy.deepcopy(dock["pose"])
        self.battery_pct = battery_after
        self.virtual_time_s += path["travel_s"]
        self.metrics["route_cost_s"] += path["cost_terms"]["cost_s"]

    def _service(self, stop: dict) -> None:
        service_s = float(stop["service_s"])
        window = self.scenario["time_window_s"]
        elapsed = self.virtual_time_s - (self._mission_started_s or 0.0)
        wait = max(0.0, float(window[0]) - elapsed) if window else 0.0
        self._require(
            not window or elapsed + wait <= window[1] + 1e-7, "TIME_WINDOW_MISSED", "safety"
        )
        energy = self.robot_profile.idle_w * (wait + service_s) / 3600
        battery_after = self.battery_pct - 100 * energy / self.robot_profile.battery_wh
        payload_after = self.payload_kg + float(stop["demand_kg"])
        self._require(battery_after >= 15 - 1e-7, "BATTERY_RESERVE", "safety")
        self._require(
            payload_after <= self.scenario.get("payload_capacity_kg", 18.0) + 1e-9,
            "PAYLOAD_LIMIT",
            "safety",
        )
        self.virtual_time_s += wait + service_s
        self.battery_pct = battery_after
        self.payload_kg = payload_after
        weights = self._plan["objective_terms"].get("weights", {})
        # Match the accepted graph/routing objective: service energy and
        # priority-weighted arrival matter. External recovery waits are an
        # additional observed delay, never a shorter route/oracle improvement.
        self.metrics["route_cost_s"] += (
            service_s
            + weights.get("energy", 0.0) * self.robot_profile.idle_w * service_s / 3600
            + 0.1 * int(stop["priority"]) * (elapsed + wait)
        )

    def step(self, decision: dict) -> tuple[dict, float, bool, bool, dict]:
        if not self._reset:
            raise RuntimeError("reset(scenario) is required before step")
        if self._terminated or self._truncated:
            return (
                self.observe(),
                0.0,
                self._terminated,
                self._truncated,
                {"verifier_code": "EPISODE_CLOSED", "state_hash": self.state_hash()},
            )
        before_hash = self.state_hash()
        self.step_count += 1
        self._inject_events(self.step_count)
        components = {name: 0.0 for name in self.reward_totals}
        components["tool_cost"] = -0.02
        progress = 0.0
        category = None
        try:
            _canonical(decision)
            if not isinstance(decision, dict):
                raise _Reject("DECISION_OBJECT_REQUIRED")
            if "decision" in decision:
                code, terminal = self._non_tool(decision)
                components["terminal"] = terminal
            else:
                name = decision.get("name")
                if name not in {s["name"] for s in self.skills}:
                    raise _Reject(
                        "UNREGISTERED_SKILL",
                        "invalid" if name == "__invalid_json__" else "unauthorized",
                    )
                try:
                    action = validate_skill_selection(decision, snapshot=self.map)
                except ValueError as exc:
                    category = (
                        "unauthorized"
                        if str(exc)
                        in {
                            "SPATIAL_MAP_NOT_REGISTERED",
                            "SPATIAL_STALE_MAP",
                            "SPATIAL_GOAL_NOT_REGISTERED",
                        }
                        else "invalid"
                    )
                    if (
                        isinstance(decision.get("args"), dict)
                        and {"x", "y", "pose", "quantity", "stock_delta"} & decision["args"].keys()
                    ):
                        category = "unauthorized"
                    raise _Reject(str(exc), category) from exc
                self.metrics["tool_calls"] += 1
                code, progress = self._run_skill(action)
                if self._terminated:
                    components["terminal"] = progress if progress <= -10 or progress >= 10 else 10.0
                    progress = 0.0
                else:
                    components["progress"] = progress
        except (TypeError, ValueError) as exc:
            code = exc.code if isinstance(exc, _Reject) else "DECISION_JSON_INVALID"
            category = exc.category if isinstance(exc, _Reject) else "invalid"
            self.metrics["invalid_actions"] += 1
            components["invalid"] = -3.0
            if category in {"unauthorized", "safety"}:
                self.metrics[
                    "unauthorized_actions" if category == "unauthorized" else "safety_violations"
                ] += 1
                components[category] = -20.0
                components["terminal"] = -10.0
                self._terminated = True
        if not self._terminated and progress <= 0:
            components["no_progress"] = -0.3
        if not self._terminated and self.step_count >= self.max_steps:
            self._truncated = True
            components["terminal"] = -10.0
            code = "STEP_BUDGET_EXHAUSTED"
        if not self._terminated and not self._truncated:
            self._inject_events(self.step_count + 1)
        self._last_verifier = code
        reward = sum(components.values())
        self.total_reward += reward
        for key, value in components.items():
            self.reward_totals[key] += value
        after_hash = self.state_hash()
        entry = {
            "step": self.step_count,
            "action": copy.deepcopy(decision),
            "before_state_hash": before_hash,
            "after_state_hash": after_hash,
            "verifier_code": code,
            "category": category,
            "progress": progress,
            "reward_components": components,
            "reward": reward,
            "terminated": self._terminated,
            "truncated": self._truncated,
            "completed_goal_ids": self._completed[:],
            "mission_id": self._mission_id,
        }
        self.trace.append(entry)
        info = {
            **entry,
            "metrics": copy.deepcopy(self.metrics),
            "total_reward": self.total_reward,
            "reward_totals": self.reward_totals.copy(),
            "success": self.metrics["task_success"],
            "invalid_skill": bool(category),
            "unauthorized_action": category == "unauthorized",
            "safety_violation": category == "safety",
            "route_feasible": self.metrics["route_feasible"],
            "route_cost_s": self.metrics["route_cost_s"],
            "optimal_route_cost_s": self.metrics["reference_cost_s"],
        }
        return self.observe(), reward, self._terminated, self._truncated, info

    def get_reward(self) -> float:
        return self.total_reward

    def summary(self) -> dict:
        """Counter numerators and route costs, with unavailable ratios left null.

        ``optimal_route_cost_s`` is a legacy harness field name. Its value is
        the accepted constrained planner's feasible reference cost; this is
        not a claim of a proven resource-constrained global optimum.
        """
        reference = self.metrics["reference_cost_s"]
        return {
            **copy.deepcopy(self.metrics),
            "success": self.metrics["task_success"],
            "group_id": self.group_id,
            "steps": self.step_count,
            "terminated": self._terminated,
            "truncated": self._truncated,
            "total_reward": self.total_reward,
            "reward_components": self.reward_totals.copy(),
            "requested_stop_occurrences": len(self.scenario["goal_ids"]),
            "mission_batches": len(self.mission_batches),
            "invalid_skill": self.metrics["invalid_actions"],
            "unauthorized_action": self.metrics["unauthorized_actions"],
            "safety_violation": self.metrics["safety_violations"],
            "optimal_route_cost_s": reference if reference else None,
            "route_cost_ratio": self.metrics["route_cost_s"] / reference if reference else None,
            "reference_optimality_proven": False,
            "final_state_hash": self.state_hash(),
        }

    def expert_action(self) -> dict:
        from .expert import expert_action

        return expert_action(self)
