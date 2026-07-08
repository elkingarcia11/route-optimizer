#!/usr/bin/env python3
"""Compare route order when optimizing for distance vs drive time."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from ortools.constraint_solver import pywrapcp, routing_enums_pb2

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from route_optimizer import (  # noqa: E402
    DEFAULT_ORS_CHUNK_SIZE,
    ORS_MATRIX_PAIR_LIMIT,
    Location,
    _ors_distance_to_int,
    _resolve_api_key,
)

import openrouteservice  # noqa: E402


def load_routes(path: Path) -> tuple[list[Location], str]:
    data = json.loads(path.read_text())
    start = Location.from_value(data["start"])
    end = Location.from_value(data["end"])
    stops = [Location.from_value(stop) for stop in data["stops"]]
    profile = str(data.get("profile", "driving-car"))
    return [start, *stops, end], profile


def build_matrices_ors(
    locations: list[Location],
    *,
    api_key: str,
    profile: str,
    chunk_size: int = DEFAULT_ORS_CHUNK_SIZE,
) -> tuple[list[list[int]], list[list[int]]]:
    n = len(locations)
    distance_matrix = [[0] * n for _ in range(n)]
    duration_matrix = [[0] * n for _ in range(n)]
    ors_locations = [loc.to_ors() for loc in locations]
    client = openrouteservice.Client(key=api_key)

    for src_start in range(0, n, chunk_size):
        sources = list(range(src_start, min(src_start + chunk_size, n)))
        for dst_start in range(0, n, chunk_size):
            destinations = list(range(dst_start, min(dst_start + chunk_size, n)))
            pair_count = len(sources) * len(destinations)
            if pair_count > ORS_MATRIX_PAIR_LIMIT:
                raise RuntimeError(f"Matrix chunk exceeds ORS limit ({pair_count}).")

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


def solve_route(
    cost_matrix: list[list[int]],
    *,
    time_limit_seconds: int = 5,
) -> tuple[list[int], int, float]:
    n = len(cost_matrix)
    manager = pywrapcp.RoutingIndexManager(n, 1, [0], [n - 1])
    routing = pywrapcp.RoutingModel(manager)

    def callback(from_index: int, to_index: int) -> int:
        from_node = manager.IndexToNode(from_index)
        to_node = manager.IndexToNode(to_index)
        return cost_matrix[from_node][to_node]

    transit_index = routing.RegisterTransitCallback(callback)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_index)

    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    )
    params.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    )
    params.time_limit.FromSeconds(time_limit_seconds)

    started = time.perf_counter()
    solution = routing.SolveWithParameters(params)
    runtime = time.perf_counter() - started
    if solution is None:
        raise RuntimeError("No solution found.")

    ordered_indices: list[int] = []
    optimized_cost = 0
    index = routing.Start(0)
    while not routing.IsEnd(index):
        node = manager.IndexToNode(index)
        ordered_indices.append(node)
        previous = index
        index = solution.Value(routing.NextVar(index))
        optimized_cost += routing.GetArcCostForVehicle(previous, index, 0)

    return ordered_indices, optimized_cost, runtime


def route_totals(
    ordered_indices: list[int],
    distance_matrix: list[list[int]],
    duration_matrix: list[list[int]],
) -> tuple[int, int]:
    total_distance = 0
    total_duration = 0
    for left, right in zip(ordered_indices, ordered_indices[1:]):
        total_distance += distance_matrix[left][right]
        total_duration += duration_matrix[left][right]
    return total_distance, total_duration


def format_duration(seconds: int) -> str:
    sign = "-" if seconds < 0 else ""
    seconds = abs(seconds)
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    if hours:
        return f"{sign}{hours}h {minutes}m {secs}s"
    return f"{sign}{minutes}m {secs}s"


def stop_labels(locations: list[Location], ordered_indices: list[int]) -> list[str]:
    labels: list[str] = []
    for index in ordered_indices:
        loc = locations[index]
        if index == 0:
            labels.append(f"START: {loc.street_address_1 or loc.label}")
        elif index == len(locations) - 1:
            labels.append(f"END: {loc.street_address_1 or loc.label}")
        else:
            labels.append(loc.street_address_1 or loc.label)
    return labels


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--routes", type=Path, default=ROOT / "routes.json")
    parser.add_argument(
        "--cache",
        type=Path,
        default=ROOT / ".cache" / "routes_distance_duration_matrices.json",
    )
    parser.add_argument("--time-limit", type=int, default=5)
    parser.add_argument("--refresh-matrix", action="store_true")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    api_key = _resolve_api_key(None)
    locations, profile = load_routes(args.routes)

    print(f"Locations: {len(locations)} | Profile: {profile} | Solver: GLS {args.time_limit}s")

    if args.cache.exists() and not args.refresh_matrix:
        print(f"Loading cached matrices from {args.cache}")
        cached = json.loads(args.cache.read_text())
        distance_matrix = cached["distance"]
        duration_matrix = cached["duration"]
    else:
        print("Fetching distance + duration matrices from OpenRouteService...")
        started = time.perf_counter()
        distance_matrix, duration_matrix = build_matrices_ors(
            locations,
            api_key=api_key,
            profile=profile,
        )
        elapsed = time.perf_counter() - started
        args.cache.parent.mkdir(parents=True, exist_ok=True)
        args.cache.write_text(
            json.dumps(
                {"distance": distance_matrix, "duration": duration_matrix},
                indent=2,
            )
        )
        print(f"Built matrices in {elapsed:.1f}s -> {args.cache}")

    dist_order, dist_opt_cost, dist_runtime = solve_route(
        distance_matrix,
        time_limit_seconds=args.time_limit,
    )
    time_order, time_opt_cost, time_runtime = solve_route(
        duration_matrix,
        time_limit_seconds=args.time_limit,
    )

    dist_route_distance, dist_route_duration = route_totals(
        dist_order, distance_matrix, duration_matrix
    )
    time_route_distance, time_route_duration = route_totals(
        time_order, distance_matrix, duration_matrix
    )

    dist_stops = dist_order[1:-1]
    time_stops = time_order[1:-1]
    same_order = dist_stops == time_stops

    print("\n=== Optimized for DISTANCE ===")
    print(f"Solver runtime: {dist_runtime:.2f}s")
    print(f"Optimized cost: {dist_opt_cost:,} m")
    print(f"Route totals: {dist_route_distance:,} m | {format_duration(dist_route_duration)}")

    print("\n=== Optimized for DRIVE TIME ===")
    print(f"Solver runtime: {time_runtime:.2f}s")
    print(f"Optimized cost: {format_duration(time_opt_cost)}")
    print(f"Route totals: {time_route_distance:,} m | {format_duration(time_route_duration)}")

    print("\n=== Comparison ===")
    print(f"Same stop order: {'YES' if same_order else 'NO'}")
    if not same_order:
        moved = sum(
            1
            for dist_idx, time_idx in zip(dist_stops, time_stops)
            if dist_idx != time_idx
        )
        print(f"Stops in different positions: {moved}/{len(dist_stops)}")

    distance_delta = time_route_distance - dist_route_distance
    duration_delta = time_route_duration - dist_route_duration
    print(
        f"If you use the TIME-optimized route instead of DISTANCE-optimized:\n"
        f"  Distance change: {distance_delta:+,} m\n"
        f"  Drive time change: {format_duration(duration_delta)} "
        f"({'slower' if duration_delta > 0 else 'faster' if duration_delta < 0 else 'same'})"
    )
    print(
        f"If you use the DISTANCE-optimized route instead of TIME-optimized:\n"
        f"  Distance change: {-distance_delta:+,} m\n"
        f"  Drive time change: {format_duration(-duration_delta)} "
        f"({'slower' if duration_delta < 0 else 'faster' if duration_delta > 0 else 'same'})"
    )

    if not same_order:
        print("\n=== Stop order diff (distance vs time) ===")
        dist_labels = stop_labels(locations, dist_order)
        time_labels = stop_labels(locations, time_order)
        max_len = max(len(dist_labels), len(time_labels))
        print(f"{'Distance-optimized':<36} {'Time-optimized':<36}")
        for i in range(max_len):
            left = dist_labels[i] if i < len(dist_labels) else ""
            right = time_labels[i] if i < len(time_labels) else ""
            marker = " " if left == right else "*"
            print(f"{marker} {left:<34} {right:<34}")

    output = {
        "same_stop_order": same_order,
        "distance_optimized": {
            "stop_order_indices": dist_stops,
            "total_distance_meters": dist_route_distance,
            "total_duration_seconds": dist_route_duration,
            "solver_runtime_seconds": round(dist_runtime, 2),
        },
        "time_optimized": {
            "stop_order_indices": time_stops,
            "total_distance_meters": time_route_distance,
            "total_duration_seconds": time_route_duration,
            "solver_runtime_seconds": round(time_runtime, 2),
        },
        "delta_time_route_vs_distance_route": {
            "distance_meters": distance_delta,
            "duration_seconds": duration_delta,
        },
    }
    out_path = ROOT / ".cache" / "distance_vs_duration_comparison.json"
    out_path.write_text(json.dumps(output, indent=2))
    print(f"\nSaved comparison to {out_path}")


if __name__ == "__main__":
    main()
