"""Small-fleet pickup/delivery VRP with hard time windows and capacity.

Travel estimates are explicitly labelled; supply a road matrix for real dispatch.
Unknown/time-limited search outcomes are ERROR, never INFEASIBLE.
"""
import json
import math

from ortools.constraint_solver import pywrapcp, routing_enums_pb2
from app.integrations.agent.contracts import SolverResult, ValidationReport, VerifiedSummary


def estimated_matrix(locations, speed_mps=8.33):
    distances = []
    for a in locations:
        row = []
        for b in locations:
            lat1, lat2 = math.radians(a["latitude"]), math.radians(b["latitude"])
            dlat, dlon = lat2-lat1, math.radians(b["longitude"]-a["longitude"])
            h = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
            row.append(math.ceil(6371000 * 2 * math.asin(min(1, math.sqrt(h)))))
        distances.append(row)
    return distances, [[math.ceil(d/speed_mps) for d in row] for row in distances]


def solve(payload: str) -> SolverResult:
    data = json.loads(payload)
    vehicles, orders = data["vehicles"], data["orders"]
    if not orders:
        return SolverResult(status="FEASIBLE", solution_payload_json=json.dumps({"routes": []}))
    if not vehicles:
        return SolverResult(status="INFEASIBLE", diagnostic_codes=("NO_AVAILABLE_VEHICLE",))
    if len(orders) > 100:
        return SolverResult(status="ERROR", diagnostic_codes=("INPUT_LIMIT_EXCEEDED",))
    distance, duration = data["distance_matrix"], data["duration_matrix"]
    # Nodes are one vehicle start, a virtual open end, then order pickup/delivery.
    nodes = [{"loc": v["location"], "service": 0, "load": 0} for v in vehicles]
    end_node = len(nodes)
    nodes.append({"loc": None, "service": 0, "load": 0})
    pairs = []
    for o in orders:
        pickup = None
        if o.get("pickup") is not None:
            pickup = len(nodes)
            nodes.append({"loc": o["pickup"], "service": o["pickup_service"], "load": o["demand"],
                          "order": o["id"], "kind": o.get("pickup_kind", "PICKUP"),
                          "earliest": o["ready"], "latest": o["deadline"]})
        delivery = len(nodes)
        nodes.append({"loc": o["delivery"], "service": o["delivery_service"], "load": -o["demand"],
                      "order": o["id"], "kind": "DELIVERY", "earliest": o["window_start"], "latest": o["deadline"]})
        pairs.append((o, pickup, delivery))
    manager = pywrapcp.RoutingIndexManager(len(nodes), len(vehicles), list(range(len(vehicles))), [end_node]*len(vehicles))
    routing = pywrapcp.RoutingModel(manager)
    def travel(i, j, matrix, include_service=False):
        a, b = nodes[manager.IndexToNode(i)], nodes[manager.IndexToNode(j)]
        value = 0 if a["loc"] is None or b["loc"] is None else matrix[a["loc"]][b["loc"]]
        return value + (a["service"] if include_service else 0)
    dist_callback = routing.RegisterTransitCallback(lambda i, j: travel(i, j, distance))
    time_callback = routing.RegisterTransitCallback(lambda i, j: travel(i, j, duration, True))
    routing.SetArcCostEvaluatorOfAllVehicles(dist_callback)
    origin = min(v["start"] for v in vehicles)
    horizon = max(max(o["deadline"] + o["delivery_service"] for o in orders), max(v["end"] for v in vehicles)) - origin
    routing.AddDimension(time_callback, horizon, horizon, False, "Time")
    times = routing.GetDimensionOrDie("Time")
    loads = routing.RegisterUnaryTransitCallback(lambda i: nodes[manager.IndexToNode(i)]["load"])
    routing.AddDimensionWithVehicleCapacity(loads, 0, [v["capacity"] for v in vehicles], False, "Load")
    load_dimension = routing.GetDimensionOrDie("Load")
    routing.AddConstantDimension(1, len(nodes)+1, True, "Sequence")
    sequence = routing.GetDimensionOrDie("Sequence")
    for index, v in enumerate(vehicles):
        times.CumulVar(routing.Start(index)).SetRange(v["start"]-origin, v["end"]-origin)
        times.CumulVar(routing.End(index)).SetRange(0, v["end"]-origin)
        load_dimension.CumulVar(routing.Start(index)).SetValue(v.get("initial_load", 0))
        load_dimension.CumulVar(routing.End(index)).SetValue(0)
        routing.AddVariableMinimizedByFinalizer(times.CumulVar(routing.Start(index)))
        routing.AddVariableMinimizedByFinalizer(times.CumulVar(routing.End(index)))
    for o, pickup, delivery in pairs:
        allowed = [i for i, v in enumerate(vehicles) if v["id"] in o["allowed_vehicle_ids"]]
        if not allowed:
            return SolverResult(status="INFEASIBLE", diagnostic_codes=("NO_ALLOWED_VEHICLE",))
        for n in [x for x in (pickup, delivery) if x is not None]:
            low, high = max(0, nodes[n]["earliest"]-origin), nodes[n]["latest"]-origin
            if high < low:
                return SolverResult(status="INFEASIBLE", diagnostic_codes=("EXPIRED_WINDOW",))
            idx = manager.NodeToIndex(n)
            times.CumulVar(idx).SetRange(low, high)
            for vehicle_index in range(len(vehicles)):
                if vehicle_index not in allowed:
                    routing.VehicleVar(idx).RemoveValue(vehicle_index)
        if pickup is not None:
            p, d = manager.NodeToIndex(pickup), manager.NodeToIndex(delivery)
            routing.AddPickupAndDelivery(p, d)
            routing.solver().Add(routing.VehicleVar(p) == routing.VehicleVar(d))
            routing.solver().Add(sequence.CumulVar(p) < sequence.CumulVar(d))
    parameters = pywrapcp.DefaultRoutingSearchParameters()
    parameters.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION
    parameters.time_limit.seconds = 5
    solution = routing.SolveWithParameters(parameters)
    if solution is None:
        status = routing.status()
        # ROUTING_INFEASIBLE=6 is proven infeasibility. FAIL/TIMEOUT remain unknown.
        return SolverResult(status="INFEASIBLE" if status == 6 else "ERROR",
                            diagnostic_codes=(f"ORTOOLS_STATUS_{status}",))
    routes = []
    for i, v in enumerate(vehicles):
        index, stops, meters = routing.Start(i), [], 0
        while not routing.IsEnd(index):
            following = solution.Value(routing.NextVar(index))
            meters += travel(index, following, distance)
            node = nodes[manager.IndexToNode(following)]
            if not routing.IsEnd(following):
                arrival = origin + solution.Value(times.CumulVar(following))
                stops.append({"order_id": node["order"], "kind": node["kind"], "location": node["loc"],
                              "arrival": arrival, "departure": arrival+node["service"]})
            index = following
        if stops:
            start = origin + solution.Value(times.CumulVar(routing.Start(i)))
            end = origin + solution.Value(times.CumulVar(routing.End(i)))
            routes.append({"vehicle_id": v["id"], "start": start, "end": end,
                           "distance_meters": meters, "duration_seconds": end-start, "stops": stops})
    return SolverResult(status="FEASIBLE", solution_payload_json=json.dumps({"routes": routes}))


def require(condition):
    if not condition:
        raise ValueError("Constraint failed")


def validate(payload: str, result: SolverResult) -> ValidationReport:
    """Recalculate constraints/metrics from input; do not trust solver summaries."""
    try:
        data, output = json.loads(payload), json.loads(result.solution_payload_json)
        orders, vehicles = {o["id"]: o for o in data["orders"]}, {v["id"]: v for v in data["vehicles"]}
        seen, used, total_distance, total_duration = {}, set(), 0, 0
        for route in output["routes"]:
            vid = route["vehicle_id"]
            require(vid not in used)
            used.add(vid)
            v = vehicles[vid]
            previous_time, location, load = route["start"], v["location"], v.get("initial_load", 0)
            require(v["start"] <= previous_time <= v["end"] and 0 <= load <= v["capacity"])
            meters = 0
            for stop in route["stops"]:
                o = orders[stop["order_id"]]
                require(vid in o["allowed_vehicle_ids"])
                key = (o["id"], stop["kind"])
                require(key not in seen)
                seen[key] = vid
                pickup_kind = o.get("pickup_kind", "PICKUP")
                if stop["kind"] == pickup_kind:
                    require(o.get("pickup") is not None and stop["location"] == o["pickup"])
                    earliest, service = o["ready"], o["pickup_service"]
                    load += o["demand"]
                else:
                    require(stop["kind"] == "DELIVERY" and stop["location"] == o["delivery"])
                    require(o.get("pickup") is None or seen.get((o["id"], pickup_kind)) == vid)
                    earliest, service = o["window_start"], o["delivery_service"]
                    load -= o["demand"]
                require(0 <= load <= v["capacity"])
                require(max(earliest, previous_time + data["duration_matrix"][location][stop["location"]]) <= stop["arrival"] <= o["deadline"])
                require(stop["departure"] == stop["arrival"] + service)
                meters += data["distance_matrix"][location][stop["location"]]
                previous_time, location = stop["departure"], stop["location"]
            require(load == 0 and previous_time == route["end"] <= v["end"])
            require(route["distance_meters"] == meters and route["duration_seconds"] == route["end"]-route["start"])
            total_distance += meters
            total_duration += route["duration_seconds"]
        for o in orders.values():
            require((o["id"], "DELIVERY") in seen)
            if o.get("pickup") is not None:
                require((o["id"], o.get("pickup_kind", "PICKUP")) in seen)
        return ValidationReport(status="VALID", summary=VerifiedSummary(assigned_order_count=len(orders),
            changed_route_count=len(output["routes"]), total_distance_meters=total_distance,
            total_duration_seconds=total_duration))
    except (AssertionError, ValueError, TypeError, KeyError, IndexError):
        return ValidationReport(status="INVALID", diagnostic_codes=("CONSTRAINT_VALIDATION_FAILED",))
