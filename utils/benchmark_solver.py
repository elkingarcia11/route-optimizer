#!/usr/bin/env python3
"""Benchmark OR-Tools time limits and compare local search metaheuristics."""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from ortools.constraint_solver import pywrapcp, routing_enums_pb2

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from route_optimizer import Location, build_distance_matrix_ors  # noqa: E402
from utils.ors_config import load_env, resolve_api_key  # noqa: E402

METAHEURISTICS = {
    "gls": (
        "Guided Local Search",
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH,
    ),
    "tabu": (
        "Tabu Search",
        routing_enums_pb2.LocalSearchMetaheuristic.TABU_SEARCH,
    ),
}


@dataclass(frozen=True)
class RunResult:
    solver_limit_seconds: int
    metaheuristic: str
    metaheuristic_label: str
    distance_meters: int
    runtime_seconds: float


class ProgressTracker:
    """Emit live progress while long solver runs execute."""

    def __init__(self, total_runs: int, *, heartbeat_seconds: float = 2.0) -> None:
        self.total_runs = total_runs
        self.heartbeat_seconds = heartbeat_seconds
        self.completed_runs = 0
        self.benchmark_started = time.perf_counter()
        self.run_durations: list[float] = []

    def log(self, message: str) -> None:
        elapsed = time.perf_counter() - self.benchmark_started
        print(f"[{self._format_duration(elapsed)}] {message}", flush=True)

    def section(self, title: str) -> None:
        self.log("")
        self.log(f"=== {title} ===")

    def step(self, message: str) -> None:
        self.log(message)

    def begin_run(
        self,
        run_index: int,
        *,
        metaheuristic_label: str,
        solver_limit_seconds: int,
    ) -> "_RunProgress":
        avg = (
            sum(self.run_durations) / len(self.run_durations)
            if self.run_durations
            else None
        )
        remaining_runs = self.total_runs - self.completed_runs
        eta = ""
        if avg is not None:
            eta = f", ETA ~{self._format_duration(avg * remaining_runs)} remaining"

        self.log(
            f"Run {run_index}/{self.total_runs}: "
            f"{metaheuristic_label} with {solver_limit_seconds}s solver limit "
            f"(starting{eta})"
        )
        return _RunProgress(self, solver_limit_seconds)

    def finish_run(self, duration_seconds: float) -> None:
        self.completed_runs += 1
        self.run_durations.append(duration_seconds)

    @staticmethod
    def _format_duration(seconds: float) -> str:
        if seconds < 60:
            return f"{seconds:.1f}s"
        minutes = int(seconds // 60)
        remainder = seconds - minutes * 60
        return f"{minutes}m {remainder:.0f}s"


class _RunProgress:
    def __init__(self, tracker: ProgressTracker, solver_limit_seconds: int) -> None:
        self.tracker = tracker
        self.solver_limit_seconds = solver_limit_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._started = time.perf_counter()

    def __enter__(self) -> "_RunProgress":
        self._thread = threading.Thread(target=self._heartbeat, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)

    def _heartbeat(self) -> None:
        while not self._stop.wait(self.tracker.heartbeat_seconds):
            elapsed = time.perf_counter() - self._started
            pct = min(100.0, elapsed / self.solver_limit_seconds * 100)
            self.tracker.log(
                f"  ... still solving ({elapsed:.1f}s elapsed, "
                f"~{pct:.0f}% of {self.solver_limit_seconds}s limit)"
            )


def load_routes(path: Path) -> tuple[Location, list[Location], Location, str]:
    data = json.loads(path.read_text())
    start = Location.from_value(data["start"])
    end = Location.from_value(data["end"])
    stops = [Location.from_value(stop) for stop in data["stops"]]
    profile = str(data.get("profile", "driving-car"))
    return start, stops, end, profile


def solve_with_matrix(
    distance_matrix: list[list[int]],
    *,
    time_limit_seconds: int,
    metaheuristic: str,
    progress: _RunProgress | None = None,
) -> tuple[int, float]:
    label, meta = METAHEURISTICS[metaheuristic]
    del label

    num_locations = len(distance_matrix)
    start_index = 0
    end_index = num_locations - 1

    manager = pywrapcp.RoutingIndexManager(
        num_locations,
        1,
        [start_index],
        [end_index],
    )
    routing = pywrapcp.RoutingModel(manager)

    def distance_callback(from_index: int, to_index: int) -> int:
        from_node = manager.IndexToNode(from_index)
        to_node = manager.IndexToNode(to_index)
        return distance_matrix[from_node][to_node]

    transit_callback_index = routing.RegisterTransitCallback(distance_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_callback_index)

    search_parameters = pywrapcp.DefaultRoutingSearchParameters()
    search_parameters.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    )
    search_parameters.local_search_metaheuristic = meta
    search_parameters.time_limit.FromSeconds(time_limit_seconds)

    started = time.perf_counter()
    if progress is None:
        solution = routing.SolveWithParameters(search_parameters)
    else:
        with progress:
            solution = routing.SolveWithParameters(search_parameters)
    runtime_seconds = time.perf_counter() - started
    if solution is None:
        raise RuntimeError("No solution found.")

    total_distance = 0
    index = routing.Start(0)
    while not routing.IsEnd(index):
        previous = index
        index = solution.Value(routing.NextVar(index))
        total_distance += routing.GetArcCostForVehicle(previous, index, 0)

    return total_distance, runtime_seconds


def load_or_build_matrix(
    locations: list[Location],
    *,
    api_key: str,
    profile: str,
    cache_path: Path,
    refresh: bool,
    progress: ProgressTracker,
) -> list[list[int]]:
    if cache_path.exists() and not refresh:
        progress.step(f"Loading cached distance matrix from {cache_path}")
        started = time.perf_counter()
        matrix = json.loads(cache_path.read_text())
        elapsed = time.perf_counter() - started
        progress.step(
            f"Loaded {len(matrix)}x{len(matrix)} matrix in {elapsed:.2f}s"
        )
        return matrix

    progress.step(
        f"Building distance matrix via OpenRouteService "
        f"({len(locations)} locations, profile={profile})"
    )
    progress.step("This may take 30-90 seconds depending on ORS response time.")
    started = time.perf_counter()
    matrix = build_distance_matrix_ors(
        locations,
        api_key=api_key,
        profile=profile,
    )
    elapsed = time.perf_counter() - started
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(matrix))
    progress.step(
        f"Built and cached {len(matrix)}x{len(matrix)} matrix in {elapsed:.1f}s "
        f"-> {cache_path}"
    )
    return matrix


def format_meters(value: int) -> str:
    return f"{value:,} m ({value / 1000:.2f} km)"


def format_runtime(value: float, limit: int) -> str:
    return f"{value:.2f}s ({value / limit * 100:.0f}% of limit)"


def suggest_time_limit(gls_results: list[RunResult]) -> tuple[int, int]:
    """Pick the smallest limit where GLS gains are <= 200 m over the prior step."""
    by_limit: dict[int, RunResult] = {row.solver_limit_seconds: row for row in gls_results}
    limits = sorted(by_limit)
    if len(limits) == 1:
        row = by_limit[limits[0]]
        return row.solver_limit_seconds, row.distance_meters

    for index in range(1, len(limits)):
        prev = by_limit[limits[index - 1]]
        current = by_limit[limits[index]]
        if current.distance_meters < prev.distance_meters:
            improvement = prev.distance_meters - current.distance_meters
            if improvement <= 200:
                return current.solver_limit_seconds, current.distance_meters
        else:
            return prev.solver_limit_seconds, prev.distance_meters

    last = by_limit[limits[-1]]
    return last.solver_limit_seconds, last.distance_meters


def print_results_table(results: list[RunResult]) -> None:
    print(
        f"\n{'Limit (s)':>10}  {'Metaheuristic':>22}  {'Distance':>18}  {'Runtime':>22}",
        flush=True,
    )
    for row in results:
        print(
            f"{row.solver_limit_seconds:>10}  "
            f"{row.metaheuristic_label:>22}  "
            f"{format_meters(row.distance_meters):>18}  "
            f"{format_runtime(row.runtime_seconds, row.solver_limit_seconds):>22}",
            flush=True,
        )


def print_head_to_head(results: list[RunResult], limit: int) -> None:
    rows = {
        row.metaheuristic: row
        for row in results
        if row.solver_limit_seconds == limit
    }
    if len(rows) != 2:
        return

    gls = rows["gls"]
    tabu = rows["tabu"]
    print(f"\n--- Head-to-head at {limit}s solver limit ---", flush=True)
    print(f"{'Metric':>18}  {'Guided Local Search':>22}  {'Tabu Search':>22}", flush=True)
    print(
        f"{'Distance':>18}  {format_meters(gls.distance_meters):>22}  "
        f"{format_meters(tabu.distance_meters):>22}",
        flush=True,
    )
    print(
        f"{'Runtime':>18}  {gls.runtime_seconds:>21.2f}s  "
        f"{tabu.runtime_seconds:>21.2f}s",
        flush=True,
    )

    if tabu.distance_meters < gls.distance_meters:
        route_winner = "Tabu Search"
        route_delta = gls.distance_meters - tabu.distance_meters
    elif gls.distance_meters < tabu.distance_meters:
        route_winner = "Guided Local Search"
        route_delta = tabu.distance_meters - gls.distance_meters
    else:
        route_winner = "Tie"
        route_delta = 0

    if tabu.runtime_seconds < gls.runtime_seconds:
        speed_winner = "Tabu Search"
        speed_delta = gls.runtime_seconds - tabu.runtime_seconds
    elif gls.runtime_seconds < tabu.runtime_seconds:
        speed_winner = "Guided Local Search"
        speed_delta = tabu.runtime_seconds - gls.runtime_seconds
    else:
        speed_winner = "Tie"
        speed_delta = 0.0

    print(
        f"Better route: {route_winner}"
        + (f" by {route_delta:,} m" if route_delta else ""),
        flush=True,
    )
    print(
        f"Faster runtime: {speed_winner}"
        + (f" by {speed_delta:.2f}s" if speed_delta else ""),
        flush=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--routes",
        type=Path,
        default=ROOT / "utils" / "samples" / "routes.json",
        help="Route payload with start, stops, and end.",
    )
    parser.add_argument(
        "--time-limits",
        default="5,10,15,20,30,45,60",
        help="Comma-separated solver time limits in seconds.",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=ROOT / ".cache" / "routes_distance_matrix.json",
        help="Cached ORS distance matrix.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / ".cache" / "benchmark_results.json",
        help="Where to write JSON benchmark results.",
    )
    parser.add_argument(
        "--refresh-matrix",
        action="store_true",
        help="Rebuild the ORS distance matrix even if cache exists.",
    )
    parser.add_argument(
        "--heartbeat-seconds",
        type=float,
        default=2.0,
        help="How often to print in-run solver progress updates.",
    )
    args = parser.parse_args()

    load_env()
    api_key = resolve_api_key(None)

    start, stops, end, profile = load_routes(args.routes)
    locations = [start, *stops, end]
    time_limits = [
        int(value.strip()) for value in args.time_limits.split(",") if value.strip()
    ]
    total_runs = len(time_limits) * len(METAHEURISTICS)
    progress = ProgressTracker(total_runs, heartbeat_seconds=args.heartbeat_seconds)

    progress.section("Benchmark setup")
    progress.step(
        f"Loaded {args.routes.name}: {len(locations)} locations "
        f"(1 start + {len(stops)} stops + 1 end)"
    )
    progress.step(f"Profile: {profile}")
    progress.step(
        f"Plan: {total_runs} solver runs "
        f"({len(time_limits)} time limits x {len(METAHEURISTICS)} metaheuristics)"
    )
    progress.step(f"Time limits: {', '.join(str(value) for value in time_limits)}s")

    matrix = load_or_build_matrix(
        locations,
        api_key=api_key,
        profile=profile,
        cache_path=args.cache,
        refresh=args.refresh_matrix,
        progress=progress,
    )

    progress.section("Solver runs (GLS vs Tabu)")
    results: list[RunResult] = []
    best_by_meta: dict[str, tuple[int, int]] = {}
    run_index = 0

    for limit in time_limits:
        progress.step(f"--- Time limit bucket: {limit}s ---")
        for key in ("gls", "tabu"):
            run_index += 1
            label, _ = METAHEURISTICS[key]
            run_progress = progress.begin_run(
                run_index,
                metaheuristic_label=label,
                solver_limit_seconds=limit,
            )
            distance, runtime_seconds = solve_with_matrix(
                matrix,
                time_limit_seconds=limit,
                metaheuristic=key,
                progress=run_progress,
            )
            progress.finish_run(runtime_seconds)

            result = RunResult(
                solver_limit_seconds=limit,
                metaheuristic=key,
                metaheuristic_label=label,
                distance_meters=distance,
                runtime_seconds=runtime_seconds,
            )
            results.append(result)

            previous_best = best_by_meta.get(key)
            if previous_best is None or distance < previous_best[0]:
                best_by_meta[key] = (distance, limit)
                best_note = "new best for this metaheuristic"
            else:
                best_distance, best_limit = previous_best
                best_note = f"best so far: {best_distance:,} m @ {best_limit}s"

            progress.step(
                f"Run {run_index}/{total_runs} complete: {label} @ {limit}s -> "
                f"{format_meters(distance)}, runtime {runtime_seconds:.2f}s ({best_note})"
            )

    progress.section("Final results")
    print_results_table(results)

    gls_results = [row for row in results if row.metaheuristic == "gls"]
    optimal_limit, optimal_distance = suggest_time_limit(gls_results)
    progress.step(
        f"Suggested solver time limit: {optimal_limit}s "
        f"(GLS distance {format_meters(optimal_distance)}; "
        f"<=200 m gain vs previous step)"
    )
    print_head_to_head(results, optimal_limit)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "locations": len(locations),
        "stops": len(stops),
        "profile": profile,
        "suggested_time_limit_seconds": optimal_limit,
        "total_benchmark_runtime_seconds": round(
            time.perf_counter() - progress.benchmark_started, 2
        ),
        "runs": [asdict(row) for row in results],
    }
    args.output.write_text(json.dumps(payload, indent=2))
    progress.step(f"Results saved to {args.output}")
    progress.step(
        f"Benchmark finished in "
        f"{ProgressTracker._format_duration(time.perf_counter() - progress.benchmark_started)}"
    )


if __name__ == "__main__":
    main()
