"""Latest approval time that still preserves the generated route schedule."""
from datetime import timedelta

from app.integrations.routing.distance_matrix import build_distance_time_matrix


def review_deadline(context, plan):
    matrix = build_distance_time_matrix(context.locations)
    indices = {location_id: i for i, location_id in enumerate(matrix.location_ids)}
    vehicles = {vehicle.vehicle_id: vehicle for vehicle in context.vehicles}
    deadlines = []
    for route in plan.routes:
        stops = sorted((stop for stop in route.stops
            if stop.status != "COMPLETED" and stop.sequence_no >
            (route.route_metrics or {}).get("preserved_stop_count", 0)),
            key=lambda stop: stop.sequence_no)
        if not stops:
            continue
        vehicle = vehicles[route.vehicle_id]
        first = stops[0]
        travel_seconds = matrix.duration_matrix_seconds[
            indices[vehicle.start_location_id]][indices[first.location_id]]
        deadlines.append(first.planned_arrival_at - timedelta(seconds=travel_seconds))
    return min(deadlines, default=context.current_time)
