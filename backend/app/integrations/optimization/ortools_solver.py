"""Standalone OR-Tools implementation for P0 vehicle routing."""

from dataclasses import dataclass
from uuid import UUID

from ortools.constraint_solver import pywrapcp, routing_enums_pb2

from app.integrations.optimization.contracts import (
    FrozenTask,
    SolverInput,
    SolverOrder,
    SolverResult,
    SolverRoute,
    SolverStatus,
    SolverStop,
    SolverStopType,
    SolverUnassignedOrder,
    SolverVehicle,
)


_UNASSIGNED_PENALTY = 1_000_000_000_000


@dataclass(frozen=True, slots=True)
class _Node:
    location_id: UUID | None
    order_id: UUID | None = None
    stop_type: SolverStopType | None = None
    service_seconds: int = 0
    load_change_load_units: int = 0


@dataclass(frozen=True, slots=True)
class _OrderNodes:
    origin_node: int | None
    delivery_node: int | None
    fixed_vehicle_index: int | None = None


class ORToolsSolver:
    """Solve a materialized SolverInput without accessing application state."""

    def solve(self, solver_input: SolverInput) -> SolverResult:
        try:
            return self._solve(solver_input)
        except Exception as error:  # OR-Tools failures are integration results.
            return SolverResult(
                status=SolverStatus.ERROR,
                routes=(),
                unassigned_orders=(),
                total_distance_meters=0,
                total_duration_seconds=0,
                diagnostic=str(error),
            )

    def _solve(self, solver_input: SolverInput) -> SolverResult:
        vehicles = solver_input.vehicles
        if not vehicles:
            return self._without_vehicles(solver_input)

        location_index = {
            location_id: index
            for index, location_id in enumerate(solver_input.location_ids)
        }
        vehicle_index = {
            vehicle.vehicle_id: index for index, vehicle in enumerate(vehicles)
        }
        frozen_by_vehicle = self._group_frozen_tasks(
            solver_input.frozen_tasks,
            vehicle_index,
        )
        frozen_by_order = {
            (task.stop.order_id, task.stop.stop_type): task
            for task in solver_input.frozen_tasks
        }

        nodes: list[_Node] = []
        starts: list[int] = []
        ends: list[int] = []
        start_times: list[int] = []
        initial_loads: list[int] = []

        for vehicle in vehicles:
            frozen = frozen_by_vehicle.get(vehicle.vehicle_id, ())
            start_location = (
                frozen[-1].stop.location_id
                if frozen
                else vehicle.start_location_id
            )
            start_time = (
                max(vehicle.available_from_seconds, frozen[-1].stop.departure_time_seconds)
                if frozen
                else vehicle.available_from_seconds
            )
            initial_load = vehicle.initial_load_load_units + sum(
                task.stop.load_change_load_units for task in frozen
            )
            if not 0 <= initial_load <= vehicle.capacity_load_units:
                raise ValueError("frozen tasks produce an invalid vehicle load")
            starts.append(len(nodes))
            nodes.append(_Node(location_id=start_location))
            ends.append(len(nodes))
            nodes.append(_Node(location_id=None))
            start_times.append(start_time)
            initial_loads.append(initial_load)

        order_nodes: dict[UUID, _OrderNodes] = {}
        for order in solver_input.orders:
            if (
                order.pickup_location_id is None
                and order.handover_location_id is None
            ):
                required_vehicle_id = order.required_vehicle_id
                if required_vehicle_id not in vehicle_index:
                    raise ValueError(
                        "delivery-only order references an unavailable vehicle"
                    )
                delivery_node = len(nodes)
                nodes.append(
                    _Node(
                        location_id=order.delivery_location_id,
                        order_id=order.order_id,
                        stop_type=SolverStopType.DELIVERY,
                        service_seconds=order.delivery_service_seconds,
                        load_change_load_units=-order.demand_load_units,
                    )
                )
                order_nodes[order.order_id] = _OrderNodes(
                    origin_node=None,
                    delivery_node=delivery_node,
                    fixed_vehicle_index=vehicle_index[required_vehicle_id],
                )
                continue
            origin_type = self._origin_type(order)
            frozen_origin = frozen_by_order.get((order.order_id, origin_type))
            frozen_delivery = frozen_by_order.get(
                (order.order_id, SolverStopType.DELIVERY)
            )
            if frozen_delivery is not None:
                if frozen_origin is None:
                    raise ValueError(
                        "frozen delivery requires its frozen origin task"
                    )
                order_nodes[order.order_id] = _OrderNodes(None, None)
                continue

            origin_node: int | None = None
            fixed_vehicle: int | None = None
            if frozen_origin is None:
                origin_node = len(nodes)
                nodes.append(
                    _Node(
                        location_id=self._origin_location(order),
                        order_id=order.order_id,
                        stop_type=origin_type,
                        service_seconds=self._origin_service_seconds(order),
                        load_change_load_units=order.demand_load_units,
                    )
                )
            else:
                fixed_vehicle = vehicle_index[frozen_origin.vehicle_id]

            delivery_node = len(nodes)
            nodes.append(
                _Node(
                    location_id=order.delivery_location_id,
                    order_id=order.order_id,
                    stop_type=SolverStopType.DELIVERY,
                    service_seconds=order.delivery_service_seconds,
                    load_change_load_units=-order.demand_load_units,
                )
            )
            order_nodes[order.order_id] = _OrderNodes(
                origin_node=origin_node,
                delivery_node=delivery_node,
                fixed_vehicle_index=fixed_vehicle,
            )

        manager = pywrapcp.RoutingIndexManager(
            len(nodes),
            len(vehicles),
            starts,
            ends,
        )
        routing = pywrapcp.RoutingModel(manager)

        def distance_callback(from_index: int, to_index: int) -> int:
            origin = nodes[manager.IndexToNode(from_index)]
            destination = nodes[manager.IndexToNode(to_index)]
            if destination.location_id is None:
                return 0
            if origin.location_id is None:
                return 0
            return solver_input.distance_matrix_meters[
                location_index[origin.location_id]
            ][location_index[destination.location_id]]

        distance_callback_index = routing.RegisterTransitCallback(
            distance_callback
        )
        routing.SetArcCostEvaluatorOfAllVehicles(distance_callback_index)

        def time_callback(from_index: int, to_index: int) -> int:
            origin = nodes[manager.IndexToNode(from_index)]
            destination = nodes[manager.IndexToNode(to_index)]
            if destination.location_id is None:
                return origin.service_seconds
            if origin.location_id is None:
                return 0
            travel_seconds = solver_input.duration_matrix_seconds[
                location_index[origin.location_id]
            ][location_index[destination.location_id]]
            return origin.service_seconds + travel_seconds

        time_callback_index = routing.RegisterTransitCallback(time_callback)
        horizon = max(
            [vehicle.available_until_seconds for vehicle in vehicles]
            + [order.delivery_window_end_seconds for order in solver_input.orders]
            + [order.ready_time_seconds for order in solver_input.orders]
            + [1]
        )
        routing.AddDimension(
            time_callback_index,
            horizon,
            horizon,
            False,
            "Time",
        )
        time_dimension = routing.GetDimensionOrDie("Time")

        def demand_callback(index: int) -> int:
            return nodes[
                manager.IndexToNode(index)
            ].load_change_load_units

        demand_callback_index = routing.RegisterUnaryTransitCallback(
            demand_callback
        )
        routing.AddDimensionWithVehicleCapacity(
            demand_callback_index,
            0,
            [vehicle.capacity_load_units for vehicle in vehicles],
            False,
            "Capacity",
        )
        capacity_dimension = routing.GetDimensionOrDie("Capacity")

        for index, vehicle in enumerate(vehicles):
            time_dimension.CumulVar(routing.Start(index)).SetRange(
                start_times[index],
                start_times[index],
            )
            time_dimension.CumulVar(routing.End(index)).SetRange(
                start_times[index],
                vehicle.available_until_seconds,
            )
            capacity_dimension.CumulVar(routing.Start(index)).SetValue(
                initial_loads[index]
            )

        for order in solver_input.orders:
            order_node = order_nodes[order.order_id]
            if order_node.delivery_node is None:
                continue
            delivery_index = manager.NodeToIndex(order_node.delivery_node)
            time_dimension.CumulVar(delivery_index).SetRange(
                order.delivery_window_start_seconds,
                order.delivery_window_end_seconds,
            )
            if order_node.origin_node is None:
                routing.solver().Add(
                    routing.VehicleVar(delivery_index)
                    == order_node.fixed_vehicle_index
                )
                continue

            origin_index = manager.NodeToIndex(order_node.origin_node)
            time_dimension.CumulVar(origin_index).SetRange(
                order.ready_time_seconds,
                horizon,
            )
            routing.AddPickupAndDelivery(origin_index, delivery_index)
            routing.solver().Add(
                routing.VehicleVar(origin_index)
                == routing.VehicleVar(delivery_index)
            )
            routing.solver().Add(
                time_dimension.CumulVar(origin_index)
                <= time_dimension.CumulVar(delivery_index)
            )
            routing.AddDisjunction(
                [origin_index],
                _UNASSIGNED_PENALTY // 2,
            )
            routing.AddDisjunction(
                [delivery_index],
                _UNASSIGNED_PENALTY // 2,
            )
            routing.solver().Add(
                routing.ActiveVar(origin_index)
                == routing.ActiveVar(delivery_index)
            )

        search_parameters = pywrapcp.DefaultRoutingSearchParameters()
        search_parameters.first_solution_strategy = (
            routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION
        )
        search_parameters.time_limit.seconds = 2
        assignment = routing.SolveWithParameters(search_parameters)
        if assignment is None:
            return SolverResult(
                status=SolverStatus.INFEASIBLE,
                routes=(),
                unassigned_orders=(),
                total_distance_meters=0,
                total_duration_seconds=0,
                diagnostic="No feasible solution for mandatory constraints",
            )

        unassigned = self._unassigned_orders(
            solver_input,
            order_nodes,
            manager,
            routing,
            assignment,
            frozen_by_vehicle,
            location_index,
        )
        routes = self._build_routes(
            solver_input,
            nodes,
            frozen_by_vehicle,
            manager,
            routing,
            assignment,
            time_dimension,
            location_index,
        )
        return SolverResult(
            status=SolverStatus.FEASIBLE,
            routes=routes,
            unassigned_orders=unassigned,
            total_distance_meters=sum(route.distance_meters for route in routes),
            total_duration_seconds=sum(route.duration_seconds for route in routes),
            diagnostic=None,
        )

    @staticmethod
    def _origin_type(order: SolverOrder) -> SolverStopType:
        if order.pickup_location_id is None and order.handover_location_id is None:
            raise ValueError("delivery-only order has no origin stop")
        return (
            SolverStopType.PICKUP
            if order.pickup_location_id is not None
            else SolverStopType.HANDOVER
        )

    @staticmethod
    def _origin_location(order: SolverOrder) -> UUID:
        location_id = order.pickup_location_id or order.handover_location_id
        if location_id is None:
            raise ValueError("delivery-only order has no origin location")
        return location_id

    @staticmethod
    def _origin_service_seconds(order: SolverOrder) -> int:
        return (
            order.pickup_service_seconds
            if order.pickup_location_id is not None
            else order.handover_service_seconds
        )

    @staticmethod
    def _group_frozen_tasks(
        frozen_tasks: tuple[FrozenTask, ...],
        vehicle_index: dict[UUID, int],
    ) -> dict[UUID, tuple[FrozenTask, ...]]:
        grouped: dict[UUID, list[FrozenTask]] = {}
        for task in frozen_tasks:
            if task.vehicle_id not in vehicle_index:
                raise ValueError("frozen task references an unknown vehicle")
            grouped.setdefault(task.vehicle_id, []).append(task)
        result: dict[UUID, tuple[FrozenTask, ...]] = {}
        for vehicle_id, tasks in grouped.items():
            ordered = tuple(sorted(tasks, key=lambda task: task.stop.sequence_no))
            if [task.stop.sequence_no for task in ordered] != list(
                range(1, len(ordered) + 1)
            ):
                raise ValueError("frozen task sequence must be contiguous")
            result[vehicle_id] = ordered
        return result

    @staticmethod
    def _without_vehicles(solver_input: SolverInput) -> SolverResult:
        if solver_input.frozen_tasks:
            return SolverResult(
                status=SolverStatus.ERROR,
                routes=(),
                unassigned_orders=(),
                total_distance_meters=0,
                total_duration_seconds=0,
                diagnostic="Frozen tasks require their assigned vehicles",
            )
        return SolverResult(
            status=SolverStatus.FEASIBLE,
            routes=(),
            unassigned_orders=tuple(
                SolverUnassignedOrder(
                    order_id=order.order_id,
                    reason_code="RESOURCE_UNAVAILABLE",
                    reason_detail="No vehicle is available",
                )
                for order in solver_input.orders
            ),
            total_distance_meters=0,
            total_duration_seconds=0,
            diagnostic=None,
        )

    def _unassigned_orders(
        self,
        solver_input: SolverInput,
        order_nodes: dict[UUID, _OrderNodes],
        manager: pywrapcp.RoutingIndexManager,
        routing: pywrapcp.RoutingModel,
        assignment: pywrapcp.Assignment,
        frozen_by_vehicle: dict[UUID, tuple[FrozenTask, ...]],
        location_index: dict[UUID, int],
    ) -> tuple[SolverUnassignedOrder, ...]:
        unassigned: list[SolverUnassignedOrder] = []
        for order in solver_input.orders:
            node = order_nodes[order.order_id]
            if node.origin_node is None:
                continue
            origin_index = manager.NodeToIndex(node.origin_node)
            if assignment.Value(routing.ActiveVar(origin_index)):
                continue
            reason_code = self._unassigned_reason(
                order,
                solver_input.vehicles,
                frozen_by_vehicle,
                solver_input.duration_matrix_seconds,
                location_index,
            )
            unassigned.append(
                SolverUnassignedOrder(
                    order_id=order.order_id,
                    reason_code=reason_code,
                    reason_detail=None,
                )
            )
        return tuple(unassigned)

    def _unassigned_reason(
        self,
        order: SolverOrder,
        vehicles: tuple[SolverVehicle, ...],
        frozen_by_vehicle: dict[UUID, tuple[FrozenTask, ...]],
        duration_matrix: tuple[tuple[int, ...], ...],
        location_index: dict[UUID, int],
    ) -> str:
        if all(
            order.demand_load_units > vehicle.capacity_load_units
            for vehicle in vehicles
        ):
            return "CAPACITY_INFEASIBLE"
        origin_location = self._origin_location(order)
        origin_service = self._origin_service_seconds(order)
        for vehicle in vehicles:
            frozen = frozen_by_vehicle.get(vehicle.vehicle_id, ())
            start_location = (
                frozen[-1].stop.location_id
                if frozen
                else vehicle.start_location_id
            )
            start_time = (
                max(vehicle.available_from_seconds, frozen[-1].stop.departure_time_seconds)
                if frozen
                else vehicle.available_from_seconds
            )
            origin_arrival = start_time + duration_matrix[
                location_index[start_location]
            ][location_index[origin_location]]
            origin_departure = max(origin_arrival, order.ready_time_seconds) + origin_service
            delivery_arrival = origin_departure + duration_matrix[
                location_index[origin_location]
            ][location_index[order.delivery_location_id]]
            if (
                delivery_arrival <= order.delivery_window_end_seconds
                and delivery_arrival <= vehicle.available_until_seconds
            ):
                return "NO_FEASIBLE_ROUTE"
        return "TIME_WINDOW_INFEASIBLE"

    def _build_routes(
        self,
        solver_input: SolverInput,
        nodes: list[_Node],
        frozen_by_vehicle: dict[UUID, tuple[FrozenTask, ...]],
        manager: pywrapcp.RoutingIndexManager,
        routing: pywrapcp.RoutingModel,
        assignment: pywrapcp.Assignment,
        time_dimension: pywrapcp.RoutingDimension,
        location_index: dict[UUID, int],
    ) -> tuple[SolverRoute, ...]:
        routes: list[SolverRoute] = []
        for vehicle_index, vehicle in enumerate(solver_input.vehicles):
            stops = [
                task.stop
                for task in frozen_by_vehicle.get(vehicle.vehicle_id, ())
            ]
            index = routing.Start(vehicle_index)
            while True:
                next_index = assignment.Value(routing.NextVar(index))
                if routing.IsEnd(next_index):
                    break
                node = nodes[manager.IndexToNode(next_index)]
                if node.order_id is not None and node.stop_type is not None:
                    arrival = assignment.Value(
                        time_dimension.CumulVar(next_index)
                    )
                    stops.append(
                        SolverStop(
                            order_id=node.order_id,
                            location_id=node.location_id,
                            stop_type=node.stop_type,
                            sequence_no=len(stops) + 1,
                            arrival_time_seconds=arrival,
                            departure_time_seconds=arrival + node.service_seconds,
                            service_duration_seconds=node.service_seconds,
                            load_change_load_units=node.load_change_load_units,
                        )
                    )
                index = next_index
            if not stops:
                continue
            distance = self._route_distance(
                vehicle,
                stops,
                solver_input.distance_matrix_meters,
                location_index,
            )
            routes.append(
                SolverRoute(
                    vehicle_id=vehicle.vehicle_id,
                    stops=tuple(stops),
                    distance_meters=distance,
                    duration_seconds=max(
                        0,
                        stops[-1].departure_time_seconds
                        - vehicle.available_from_seconds,
                    ),
                )
            )
        return tuple(routes)

    @staticmethod
    def _route_distance(
        vehicle: SolverVehicle,
        stops: list[SolverStop],
        distance_matrix: tuple[tuple[int, ...], ...],
        location_index: dict[UUID, int],
    ) -> int:
        distance = 0
        previous = vehicle.start_location_id
        for stop in stops:
            distance += distance_matrix[location_index[previous]][
                location_index[stop.location_id]
            ]
            previous = stop.location_id
        return distance
