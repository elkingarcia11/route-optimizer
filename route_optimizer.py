#!/usr/bin/env python3
"""Core route optimization used by the HTTP API."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Sequence

import openrouteservice
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


if __name__ == "__main__":
    from utils.cli import main

    sys.exit(main())
