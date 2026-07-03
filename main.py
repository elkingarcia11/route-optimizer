"""HTTP API service for route optimization."""

from __future__ import annotations

import os

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from route_optimizer import Location, optimize_route

load_dotenv()

app = FastAPI(
    title="Route Optimizer",
    description=(
        "Reorder stops between a fixed start and end using OpenRouteService "
        "road distances and OR-Tools."
    ),
    version="1.0.0",
)

DEFAULT_PROFILE = os.environ.get("ROUTE_OPTIMIZER_PROFILE", "driving-car")
DEFAULT_TIME_LIMIT_SECONDS = int(os.environ.get("ROUTE_OPTIMIZER_TIME_LIMIT", "5"))


class OptimizeRequest(BaseModel):
    locations: list[list[float]] = Field(
        ...,
        min_length=2,
        description="[[lon, lat], ...] — first is start, last is end, middle are stops",
    )


class OptimizeResponse(BaseModel):
    routeIndexes: list[int]
    totalDistanceMeters: int
    orderedLocations: list[list[float]]


def _get_api_key() -> str:
    api_key = os.environ.get("ORS_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="ORS_API_KEY is not configured.",
        )
    return api_key


def _location_from_lonlat(lon_lat: list[float]) -> Location:
    if len(lon_lat) != 2:
        raise ValueError(f"Each location must be [lon, lat], got {lon_lat!r}")
    lon, lat = float(lon_lat[0]), float(lon_lat[1])
    return Location(lat=lat, lng=lon)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/optimize", response_model=OptimizeResponse)
def optimize(request: OptimizeRequest) -> OptimizeResponse:
    locations = request.locations

    try:
        start = _location_from_lonlat(locations[0])
        end = _location_from_lonlat(locations[-1])
        stops = [_location_from_lonlat(loc) for loc in locations[1:-1]]
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        result = optimize_route(
            start,
            stops,
            end,
            api_key=_get_api_key(),
            profile=DEFAULT_PROFILE,
            time_limit_seconds=DEFAULT_TIME_LIMIT_SECONDS,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    ordered_indices = result["ordered_indices"]
    return OptimizeResponse(
        routeIndexes=ordered_indices,
        totalDistanceMeters=result["total_distance_meters"],
        orderedLocations=[
            [locations[i][0], locations[i][1]] for i in ordered_indices
        ],
    )
