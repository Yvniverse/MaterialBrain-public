"""OR-Tools single-robot routing with capacity, time, priority and recharge state.

Routing costs come exclusively from pairwise SemanticGraph paths. Energy uses a
conservative integer dimension, including service and time-window waiting. At a
registered charging stop its outgoing dimension resets to a full battery after
an explicit, conservatively timed recharge. No provider or inventory write occurs.
"""

from __future__ import annotations

import math

COST_SCALE = 1_000_000
TIME_SCALE = 1000
ENERGY_SCALE = 1_000_000
LOAD_SCALE = 1000
PRIORITY_WEIGHT = 0.1
UNREACHABLE = 10**15


def objective_matrix(stops, paths, robot, weights):
    result = []
    for i, source in enumerate(stops):
        row = []
        service_energy = (
            0 if source["kind"] == "charge" else robot.idle_w * source["service_s"] / 3600
        )
        service_cost = source["service_s"] + weights["energy"] * service_energy
        for j in range(len(stops)):
            path = paths[i][j]
            row.append(path["cost_terms"]["cost_s"] + service_cost if path else math.inf)
        result.append(row)
    return result


def solve_constrained(
    stops, paths, robot, constraints, weights, *, initial_payload_kg=0.0, initial_order=None
):
    try:
        from ortools import __version__
        from ortools.constraint_solver import pywrapcp, routing_enums_pb2
    except ImportError as exc:
        raise RuntimeError("ORTOOLS_REQUIRED_FOR_SPATIAL_MISSION") from exc

    end = len(stops) - 1
    manager = pywrapcp.RoutingIndexManager(len(stops), 1, [0], [end])
    routing = pywrapcp.RoutingModel(manager)
    solver = routing.solver()
    costs = objective_matrix(stops, paths, robot, weights)
    integer_costs = [
        [round(c * COST_SCALE) if math.isfinite(c) else UNREACHABLE for c in row] for row in costs
    ]

    def arc_cost(a, b):
        return integer_costs[manager.IndexToNode(a)][manager.IndexToNode(b)]

    routing.SetArcCostEvaluatorOfAllVehicles(routing.RegisterTransitCallback(arc_cost))
    for i in range(routing.Size()):
        a = manager.IndexToNode(i)
        for b in range(len(stops)):
            if a == b or paths[a][b] is not None:
                continue
            j = routing.End(0) if b == end else manager.NodeToIndex(b)
            routing.NextVar(i).RemoveValue(j)

    finite_times = [p["travel_s"] for row in paths for p in row if p]
    horizon_s = max(
        86400.0,
        (max(finite_times, default=0) + max(s["service_s"] for s in stops) + 1) * len(stops),
        max((s["time_window_s"][1] for s in stops if s.get("time_window_s")), default=0),
    )
    horizon = math.ceil(horizon_s * TIME_SCALE)

    def time_transit(a, b):
        i, j = manager.IndexToNode(a), manager.IndexToNode(b)
        path = paths[i][j]
        return (
            math.ceil((stops[i]["service_s"] + path["travel_s"]) * TIME_SCALE)
            if path
            else horizon + 1
        )

    routing.AddDimension(
        routing.RegisterTransitCallback(time_transit), horizon, horizon, True, "Time"
    )
    times = routing.GetDimensionOrDie("Time")
    for node, stop in enumerate(stops[1:-1], 1):
        index = manager.NodeToIndex(node)
        if stop.get("time_window_s"):
            lo, hi = stop["time_window_s"]
            times.CumulVar(index).SetRange(math.ceil(lo * TIME_SCALE), math.floor(hi * TIME_SCALE))
        if stop["priority"]:
            coefficient = round(stop["priority"] * PRIORITY_WEIGHT * COST_SCALE / TIME_SCALE)
            times.SetCumulVarSoftUpperBound(index, 0, coefficient)
        if not stop["mandatory"]:
            routing.AddDisjunction([index], 0)
        routing.AddVariableMinimizedByFinalizer(times.CumulVar(index))
    routing.AddVariableMinimizedByFinalizer(times.CumulVar(routing.End(0)))

    def demand(index):
        return math.ceil(stops[manager.IndexToNode(index)]["demand_kg"] * LOAD_SCALE)

    remaining_capacity = math.floor(
        (constraints["payload_capacity_kg"] - initial_payload_kg) * LOAD_SCALE
    )
    if remaining_capacity < 0:
        return {"order": None, "status": "INITIAL_PAYLOAD_OVER_CAPACITY", "version": __version__}
    routing.AddDimensionWithVehicleCapacity(
        routing.RegisterUnaryTransitCallback(demand), 0, [remaining_capacity], True, "Payload"
    )

    usable = math.floor(
        robot.battery_wh * (100 - constraints["battery_reserve_pct"]) / 100 * ENERGY_SCALE
    )
    initially_used = math.ceil(
        robot.battery_wh * (100 - constraints["battery_pct"]) / 100 * ENERGY_SCALE
    )
    if initially_used > usable:
        return {"order": None, "status": "INITIAL_BATTERY_BELOW_RESERVE", "version": __version__}

    def energy_transit(a, b):
        i, j = manager.IndexToNode(a), manager.IndexToNode(b)
        path = paths[i][j]
        if path is None:
            return usable + 1
        service = 0 if stops[i]["kind"] == "charge" else robot.idle_w * stops[i]["service_s"] / 3600
        value = math.ceil((path["energy_wh"] + service) * ENERGY_SCALE)
        return value - usable if stops[i]["kind"] == "charge" else value

    routing.AddDimension(
        routing.RegisterTransitCallback(energy_transit), usable, usable, False, "BatteryUsed"
    )
    energy = routing.GetDimensionOrDie("BatteryUsed")
    energy.CumulVar(routing.Start(0)).SetValue(initially_used)
    idle_milliw = math.ceil(robot.idle_w * 1000)
    for index in range(routing.Size()):
        node = manager.IndexToNode(index)
        if stops[node]["kind"] == "charge":
            # Enforced only for an active optional dock occurrence. Energy is a
            # reset dimension, not a negative-energy travel shortcut.
            total = energy.CumulVar(index) + energy.SlackVar(index)
            active = routing.ActiveVar(index)
            solver.Add(total >= usable * active)
            solver.Add(total <= usable + usable * (1 - active))
        else:
            # ceil(idle_W * wait_seconds * 1e6 / 3600), in integer micro-Wh.
            wait = times.SlackVar(index) * idle_milliw
            solver.Add(energy.SlackVar(index) * 3600 >= wait)
            solver.Add(energy.SlackVar(index) * 3600 <= wait + 3599)
        routing.AddVariableMinimizedByFinalizer(energy.CumulVar(index))

    parameters = pywrapcp.DefaultRoutingSearchParameters()
    parameters.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    parameters.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GREEDY_DESCENT
    )
    parameters.time_limit.seconds = 2
    parameters.sat_parameters.num_search_workers = 1
    parameters.sat_parameters.random_seed = 0
    parameters.use_full_propagation = True
    assignment = None
    if initial_order:
        assignment = routing.ReadAssignmentFromRoutes([initial_order], True)
    solution = (
        routing.SolveFromAssignmentWithParameters(assignment, parameters)
        if assignment is not None
        else routing.SolveWithParameters(parameters)
    )
    status = routing.status()
    status_names = {
        0: "ROUTING_NOT_SOLVED",
        1: "ROUTING_SUCCESS",
        2: "ROUTING_PARTIAL_SUCCESS_LOCAL_OPTIMUM_NOT_REACHED",
        3: "ROUTING_FAIL",
        4: "ROUTING_FAIL_TIMEOUT",
        5: "ROUTING_INVALID",
        6: "ROUTING_INFEASIBLE",
        7: "ROUTING_OPTIMAL",
    }
    base = {
        "status": status_names.get(status, str(status)),
        "version": __version__,
        "optimality_proven": status == 7,
        "objective_integer": None,
        "cost_scale": COST_SCALE,
        "time_scale": TIME_SCALE,
        "energy_scale": ENERGY_SCALE,
        "search": "PATH_CHEAPEST_ARC + GREEDY_DESCENT; one worker; seed 0; 2s upper bound",
    }
    if solution is None:
        return {**base, "order": None, "infeasibility_proven": status == 6}
    order, arrivals, consumed = [], {}, {}
    index = routing.Start(0)
    while not routing.IsEnd(index):
        node = manager.IndexToNode(index)
        arrivals[node] = solution.Value(times.CumulVar(index)) / TIME_SCALE
        consumed[node] = solution.Value(energy.CumulVar(index)) / ENERGY_SCALE
        index = solution.Value(routing.NextVar(index))
        if not routing.IsEnd(index):
            order.append(manager.IndexToNode(index))
    arrivals[end] = solution.Value(times.CumulVar(index)) / TIME_SCALE
    consumed[end] = solution.Value(energy.CumulVar(index)) / ENERGY_SCALE
    return {
        **base,
        "order": order,
        "arrivals": arrivals,
        "consumed_wh": consumed,
        "objective_integer": solution.ObjectiveValue(),
    }
