"""Multi-route VRP helpers — not exposed by the HTTP API."""

from __future__ import annotations

from typing import Sequence

from ortools.constraint_solver import pywrapcp, routing_enums_pb2

from route_optimizer import (
    DEFAULT_ORS_CHUNK_SIZE,
    Location,
    build_distance_matrix_ors,
)


def compute_vrp_time_limit(
    num_stops: int,
    num_routes: int,
    *,
    minimum: int = 30,
    maximum: int = 300,
) -> int:
    """Scale solver time with problem size for multi-stop VRP."""
    return min(maximum, max(minimum, 15 + num_stops + num_routes * 10))


def _max_route_distance_cap(
    distance_matrix: list[list[int]],
    max_stops_on_route: int,
) -> int:
    max_leg = max(
        distance_matrix[i][j]
        for i in range(len(distance_matrix))
        for j in range(len(distance_matrix))
        if i != j
    )
    return max_leg * (max_stops_on_route + 2)


def _solve_routing_model(
    routing: pywrapcp.RoutingModel,
    *,
    time_limit_seconds: int,
):
    strategies = (
        routing_enums_pb2.FirstSolutionStrategy.AUTOMATIC,
        routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC,
        routing_enums_pb2.FirstSolutionStrategy.SAVINGS,
        routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION,
    )
    remaining = max(10, time_limit_seconds)

    for index, strategy in enumerate(strategies):
        attempts_left = len(strategies) - index
        attempt_seconds = max(10, remaining // attempts_left)
        remaining -= attempt_seconds

        search_parameters = pywrapcp.DefaultRoutingSearchParameters()
        search_parameters.first_solution_strategy = strategy
        search_parameters.local_search_metaheuristic = (
            routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
        )
        search_parameters.time_limit.FromSeconds(attempt_seconds)

        solution = routing.SolveWithParameters(search_parameters)
        if solution is not None:
            return solution

    return None


def _extract_vehicle_route(
    routing: pywrapcp.RoutingModel,
    manager: pywrapcp.RoutingIndexManager,
    solution,
    vehicle_id: int,
    locations: Sequence[Location],
    depot_index: int,
) -> dict:
    ordered_indices: list[int] = []
    route_distance = 0
    index = routing.Start(vehicle_id)

    while not routing.IsEnd(index):
        node = manager.IndexToNode(index)
        ordered_indices.append(node)
        previous = index
        index = solution.Value(routing.NextVar(index))
        route_distance += routing.GetArcCostForVehicle(previous, index, vehicle_id)

    ordered_indices.append(manager.IndexToNode(index))

    stop_indices = [i for i in ordered_indices if i != depot_index]
    return {
        "ordered_indices": ordered_indices,
        "stop_indices": stop_indices,
        "ordered_locations": [locations[i].to_dict() for i in ordered_indices],
        "ordered_coordinates": [
            [locations[i].lat, locations[i].lng] for i in ordered_indices
        ],
        "stop_order": [i - 1 for i in stop_indices],
        "ordered_stop_labels": [locations[i].label for i in stop_indices],
        "distance_meters": route_distance,
        "target_stops": len(stop_indices),
    }


def optimize_balanced_multi_route(
    depot: Location,
    stops: Sequence[Location],
    num_routes: int,
    *,
    api_key: str,
    profile: str = "driving-car",
    time_limit_seconds: int = 30,
    ors_chunk_size: int = DEFAULT_ORS_CHUNK_SIZE,
    balance_weight: int = 100,
) -> dict:
    """Split stops across routes from a shared depot, balancing driving distance."""
    if num_routes < 1:
        raise ValueError("At least one route is required.")
    if not stops:
        raise ValueError("At least one stop is required.")
    if num_routes > len(stops):
        raise ValueError(
            f"Cannot create {num_routes} routes with only {len(stops)} stop(s). "
            "Each route needs at least one stop."
        )

    locations = [depot, *stops]
    depot_index = 0

    distance_matrix = build_distance_matrix_ors(
        locations,
        api_key=api_key,
        profile=profile,
        chunk_size=ors_chunk_size,
    )

    manager = pywrapcp.RoutingIndexManager(
        len(locations),
        num_routes,
        depot_index,
    )
    routing = pywrapcp.RoutingModel(manager)

    def distance_callback(from_index: int, to_index: int) -> int:
        from_node = manager.IndexToNode(from_index)
        to_node = manager.IndexToNode(to_index)
        return distance_matrix[from_node][to_node]

    transit_callback_index = routing.RegisterTransitCallback(distance_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_callback_index)

    max_route_distance = _max_route_distance_cap(distance_matrix, len(stops))
    routing.AddDimension(
        transit_callback_index,
        0,
        max_route_distance,
        True,
        "Distance",
    )
    distance_dimension = routing.GetDimensionOrDie("Distance")
    distance_dimension.SetGlobalSpanCostCoefficient(balance_weight)

    def stop_demand_callback(from_index: int) -> int:
        from_node = manager.IndexToNode(from_index)
        return 0 if from_node == depot_index else 1

    stop_callback_index = routing.RegisterUnaryTransitCallback(stop_demand_callback)
    routing.AddDimensionWithVehicleCapacity(
        stop_callback_index,
        0,
        [len(stops)] * num_routes,
        True,
        "StopsDimension",
    )
    stops_dimension = routing.GetDimensionOrDie("StopsDimension")
    for vehicle_id in range(num_routes):
        routing.solver().Add(
            stops_dimension.CumulVar(routing.End(vehicle_id)) >= 1
        )

    solution = _solve_routing_model(
        routing,
        time_limit_seconds=time_limit_seconds,
    )
    if solution is None:
        raise RuntimeError(
            "No solution found for the current stops and route count. "
            "Verify coordinates are reachable."
        )

    routes: list[dict] = []
    total_distance = 0

    for vehicle_id in range(num_routes):
        route = _extract_vehicle_route(
            routing,
            manager,
            solution,
            vehicle_id,
            locations,
            depot_index,
        )
        route["route_number"] = vehicle_id + 1
        total_distance += route["distance_meters"]
        routes.append(route)

    return {
        "depot": depot.to_dict(),
        "routes": routes,
        "route_capacities": [route["target_stops"] for route in routes],
        "split_mode": "balanced_distance",
        "num_routes": num_routes,
        "total_distance_meters": total_distance,
        "distance_source": "openrouteservice",
        "profile": profile,
    }
