#!/usr/bin/env python3
"""Reorder stops between a fixed start and end using OR-Tools."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import openrouteservice
from dotenv import load_dotenv
from ortools.constraint_solver import pywrapcp, routing_enums_pb2

# ORS matrix limit: sources x destinations <= 3500 per request.
ORS_MATRIX_PAIR_LIMIT = 3500
DEFAULT_ORS_CHUNK_SIZE = 50
DEFAULT_TIME_LIMIT_SECONDS = 5


@dataclass(frozen=True)
class Location:
    lat: float
    lng: float
    street_address_1: str = ""
    city: str = ""
    state: str = ""
    zip: str = ""

    @classmethod
    def from_value(cls, value: Sequence[float] | dict) -> "Location":
        if isinstance(value, dict):
            if "lat" not in value or "lng" not in value:
                raise ValueError(
                    f"Location object must include 'lat' and 'lng', got {value!r}"
                )
            return cls(
                lat=float(value["lat"]),
                lng=float(value["lng"]),
                street_address_1=str(value.get("street_address_1", "")),
                city=str(value.get("city", "")),
                state=str(value.get("state", "")),
                zip=str(value.get("zip", "")),
            )
        if isinstance(value, (list, tuple)) and len(value) == 2:
            return cls(lat=float(value[0]), lng=float(value[1]))
        raise ValueError(
            f"Expected [lat, lng] or location object, got {value!r}"
        )

    def to_ors(self) -> list[float]:
        """OpenRouteService expects [longitude, latitude]."""
        return [self.lng, self.lat]

    @property
    def label(self) -> str:
        line1 = self.street_address_1.strip()
        city_part = self.city.strip()
        state_zip = " ".join(
            part for part in (self.state.strip(), self.zip.strip()) if part
        )
        line2 = ", ".join(part for part in (city_part, state_zip) if part)
        if line1 and line2:
            return f"{line1}, {line2}"
        if line1:
            return line1
        if line2:
            return line2
        return f"{self.lat}, {self.lng}"

    def to_dict(self) -> dict:
        return {
            "lat": self.lat,
            "lng": self.lng,
            "street_address_1": self.street_address_1,
            "city": self.city,
            "state": self.state,
            "zip": self.zip,
            "label": self.label,
        }


# Backward-compatible alias
Coordinate = Location


def _ors_distance_to_int(value: float | None, from_idx: int, to_idx: int) -> int:
    if from_idx == to_idx:
        return 0
    if value is None:
        raise RuntimeError(
            f"No route found between location {from_idx} and {to_idx}."
        )
    return int(round(value))


def build_route_matrices_ors(
    locations: Sequence[Location],
    *,
    api_key: str,
    profile: str = "driving-car",
    chunk_size: int = DEFAULT_ORS_CHUNK_SIZE,
) -> tuple[list[list[int]], list[list[int]]]:
    """Build road distance (meters) and duration (seconds) matrices via ORS."""
    n = len(locations)
    if n == 0:
        return [], []

    if chunk_size * chunk_size > ORS_MATRIX_PAIR_LIMIT:
        raise ValueError(
            f"chunk_size must be <= {int(ORS_MATRIX_PAIR_LIMIT ** 0.5)} "
            f"so each matrix request stays within ORS limits."
        )

    ors_locations = [loc.to_ors() for loc in locations]
    distance_matrix = [[0] * n for _ in range(n)]
    duration_matrix = [[0] * n for _ in range(n)]
    client = openrouteservice.Client(key=api_key)

    for src_start in range(0, n, chunk_size):
        sources = list(range(src_start, min(src_start + chunk_size, n)))
        for dst_start in range(0, n, chunk_size):
            destinations = list(range(dst_start, min(dst_start + chunk_size, n)))
            pair_count = len(sources) * len(destinations)
            if pair_count > ORS_MATRIX_PAIR_LIMIT:
                raise RuntimeError(
                    f"Matrix chunk exceeds ORS limit ({pair_count} > "
                    f"{ORS_MATRIX_PAIR_LIMIT}). Reduce chunk_size."
                )

            response = client.distance_matrix(
                locations=ors_locations,
                profile=profile,
                sources=sources,
                destinations=destinations,
                metrics=["distance", "duration"],
                units="m",
            )
            distances = response["distances"]
            durations = response["durations"]
            for i, src_idx in enumerate(sources):
                for j, dst_idx in enumerate(destinations):
                    distance_matrix[src_idx][dst_idx] = _ors_distance_to_int(
                        distances[i][j], src_idx, dst_idx
                    )
                    duration_matrix[src_idx][dst_idx] = _ors_distance_to_int(
                        durations[i][j], src_idx, dst_idx
                    )

    return distance_matrix, duration_matrix


def build_distance_matrix_ors(
    locations: Sequence[Location],
    *,
    api_key: str,
    profile: str = "driving-car",
    chunk_size: int = DEFAULT_ORS_CHUNK_SIZE,
) -> list[list[int]]:
    """Build a road distance matrix (meters) via OpenRouteService."""
    distance_matrix, _ = build_route_matrices_ors(
        locations,
        api_key=api_key,
        profile=profile,
        chunk_size=chunk_size,
    )
    return distance_matrix


def _route_leg_totals(
    ordered_indices: Sequence[int],
    distance_matrix: list[list[int]],
    duration_matrix: list[list[int]],
) -> tuple[int, int]:
    total_distance = 0
    total_duration = 0
    for left, right in zip(ordered_indices, ordered_indices[1:]):
        total_distance += distance_matrix[left][right]
        total_duration += duration_matrix[left][right]
    return total_distance, total_duration


def optimize_route(
    start: Location,
    stops: Sequence[Location],
    end: Location,
    *,
    api_key: str,
    profile: str = "driving-car",
    time_limit_seconds: int = DEFAULT_TIME_LIMIT_SECONDS,
    ors_chunk_size: int = DEFAULT_ORS_CHUNK_SIZE,
) -> dict:
    """
    Visit every stop exactly once, starting at `start` and finishing at `end`.

    Matrices come from OpenRouteService. OR-Tools minimizes total drive time;
    the response includes both total drive time and total road distance.
    """
    locations = [start, *stops, end]

    if len(locations) == 2:
        distance_matrix, duration_matrix = build_route_matrices_ors(
            locations,
            api_key=api_key,
            profile=profile,
            chunk_size=ors_chunk_size,
        )
        total_distance, total_duration = _route_leg_totals(
            [0, 1], distance_matrix, duration_matrix
        )
        ordered_locations = [start.to_dict(), end.to_dict()]
        return {
            "ordered_locations": ordered_locations,
            "ordered_coordinates": [
                [start.lat, start.lng],
                [end.lat, end.lng],
            ],
            "ordered_indices": [0, 1],
            "stop_order": [],
            "total_distance_meters": total_distance,
            "total_duration_seconds": total_duration,
            "optimization_metric": "duration",
            "distance_source": "openrouteservice",
            "profile": profile,
        }

    distance_matrix, duration_matrix = build_route_matrices_ors(
        locations,
        api_key=api_key,
        profile=profile,
        chunk_size=ors_chunk_size,
    )
    num_locations = len(locations)
    start_index = 0
    end_index = num_locations - 1

    manager = pywrapcp.RoutingIndexManager(
        num_locations,
        1,
        [start_index],
        [end_index],
    )
    routing = pywrapcp.RoutingModel(manager)

    def duration_callback(from_index: int, to_index: int) -> int:
        from_node = manager.IndexToNode(from_index)
        to_node = manager.IndexToNode(to_index)
        return duration_matrix[from_node][to_node]

    transit_callback_index = routing.RegisterTransitCallback(duration_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_callback_index)

    search_parameters = pywrapcp.DefaultRoutingSearchParameters()
    search_parameters.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    )
    search_parameters.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    )
    search_parameters.time_limit.FromSeconds(time_limit_seconds)

    solution = routing.SolveWithParameters(search_parameters)
    if solution is None:
        raise RuntimeError("No solution found. Check coordinates and try again.")

    ordered_indices: list[int] = []
    index = routing.Start(0)

    while not routing.IsEnd(index):
        node = manager.IndexToNode(index)
        ordered_indices.append(node)
        index = solution.Value(routing.NextVar(index))

    ordered_indices.append(manager.IndexToNode(index))
    total_distance, total_duration = _route_leg_totals(
        ordered_indices, distance_matrix, duration_matrix
    )

    ordered_coordinates = [
        [locations[i].lat, locations[i].lng] for i in ordered_indices
    ]
    ordered_locations = [locations[i].to_dict() for i in ordered_indices]
    stop_order = [i - 1 for i in ordered_indices if 0 < i < end_index]

    return {
        "ordered_locations": ordered_locations,
        "ordered_coordinates": ordered_coordinates,
        "ordered_indices": ordered_indices,
        "stop_order": stop_order,
        "total_distance_meters": total_distance,
        "total_duration_seconds": total_duration,
        "optimization_metric": "duration",
        "distance_source": "openrouteservice",
        "profile": profile,
    }


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


def _parse_coordinate(raw: str) -> Location:
    parts = [p.strip() for p in raw.split(",")]
    if len(parts) != 2:
        raise argparse.ArgumentTypeError(
            f"Invalid coordinate {raw!r}; use 'lat,lng'"
        )
    try:
        return Location(lat=float(parts[0]), lng=float(parts[1]))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Invalid coordinate {raw!r}; lat and lng must be numbers"
        ) from exc


def _load_input(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    for key in ("start", "end", "stops"):
        if key not in data:
            raise ValueError(f"Input JSON must include '{key}'")
    return data


def _load_env() -> None:
    load_dotenv(Path(__file__).resolve().parent / ".env")


def _resolve_api_key(explicit_key: str | None) -> str:
    api_key = explicit_key or os.environ.get("ORS_API_KEY")
    if not api_key:
        raise SystemExit(
            "OpenRouteService API key required. Add ORS_API_KEY to .env, "
            "set the env var, or pass --api-key."
        )
    return api_key


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Optimize stop order between a fixed start and end point "
            "using OpenRouteService road distances."
        )
    )
    parser.add_argument(
        "--input",
        "-i",
        help=(
            'JSON file with start/end/stops. Each location may be [lat,lng] '
            'or {"lat", "lng", "street_address_1", "city", "state", "zip"}.'
        ),
    )
    parser.add_argument(
        "--start",
        type=_parse_coordinate,
        help="Start coordinate as 'lat,lng'",
    )
    parser.add_argument(
        "--end",
        type=_parse_coordinate,
        help="End coordinate as 'lat,lng'",
    )
    parser.add_argument(
        "--stop",
        action="append",
        dest="stops",
        type=_parse_coordinate,
        help="Stop coordinate as 'lat,lng' (repeat for each stop)",
    )
    parser.add_argument(
        "--api-key",
        help="OpenRouteService API key (overrides ORS_API_KEY from .env)",
    )
    parser.add_argument(
        "--profile",
        default="driving-car",
        help="ORS travel profile (default: driving-car)",
    )
    parser.add_argument(
        "--time-limit",
        type=int,
        default=DEFAULT_TIME_LIMIT_SECONDS,
        help=f"Solver time limit in seconds (default: {DEFAULT_TIME_LIMIT_SECONDS})",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    _load_env()
    api_key = _resolve_api_key(args.api_key)

    if args.input:
        data = _load_input(args.input)
        start = Location.from_value(data["start"])
        end = Location.from_value(data["end"])
        stops = [Location.from_value(s) for s in data["stops"]]
        time_limit = int(data.get("time_limit_seconds", args.time_limit))
        profile = data.get("profile", args.profile)
    else:
        if args.start is None or args.end is None or not args.stops:
            parser.error(
                "Provide --input OR (--start, --end, and at least one --stop)"
            )
        start = args.start
        end = args.end
        stops = args.stops
        time_limit = args.time_limit
        profile = args.profile

    result = optimize_route(
        start,
        stops,
        end,
        api_key=api_key,
        profile=profile,
        time_limit_seconds=time_limit,
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
