#!/usr/bin/env python3
"""CLI for optimizing a single route from the command line."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterable

from route_optimizer import DEFAULT_TIME_LIMIT_SECONDS, Location, optimize_route

from utils.ors_config import load_env, resolve_api_key


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


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Optimize stop order between a fixed start and end point "
            "using OpenRouteService road networks."
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

    load_env()
    api_key = resolve_api_key(args.api_key)

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
