"""HTTP API service for route optimization."""

from __future__ import annotations

import os

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from route_optimizer import (
    Location,
    compute_vrp_time_limit,
    optimize_balanced_multi_route,
    optimize_route,
)

load_dotenv()

OPENAPI_TAGS = [
    {
        "name": "health",
        "description": "Service health checks.",
    },
    {
        "name": "routing",
        "description": (
            "Optimize and split stops across one or more routes. "
            "Requires start, end, and at least two stops."
        ),
    },
]

EXAMPLE_ADDRESS_START = {
    "address1": "2249 Washington Ave",
    "city": "Bronx",
    "state": "NY",
    "zipcode": "10451",
    "country": "US",
    "location": {"type": "Point", "coordinates": [-73.8955, 40.8515]},
    "verification": {
        "is_verified": True,
        "verified_at": "2026-01-01T00:00:00Z",
    },
}

EXAMPLE_ADDRESS_STOP = {
    "address1": "1 Adrian Ave",
    "city": "Bronx",
    "state": "NY",
    "zipcode": "10463",
    "location": {"type": "Point", "coordinates": [-73.91335, 40.87995]},
}

EXAMPLE_ADDRESS_STOP_B = {
    "address1": "1101 Forest Ave",
    "city": "Bronx",
    "state": "NY",
    "zipcode": "10456",
    "location": {"type": "Point", "coordinates": [-73.90774, 40.88467]},
}

EXAMPLE_ADDRESS_END = {
    "address1": "1101 Forest Ave",
    "city": "Bronx",
    "state": "NY",
    "zipcode": "10456",
    "location": {"type": "Point", "coordinates": [-73.90774, 40.88467]},
}

app = FastAPI(
    title="Route Optimizer API",
    description=(
        "Split stops across routes balanced by road distance, or optimize a "
        "single route between a fixed start and end.\n\n"
        "Uses OpenRouteService road distances and Google OR-Tools.\n\n"
        "## Endpoints\n"
        "- `POST /routes/single` — one optimized route (start → stops → end)\n"
        "- `POST /routes/balance` — split stops across routes balanced by distance\n\n"
        "## Coordinate format\n"
        "Address `location.coordinates` uses GeoJSON order: "
        "`[longitude, latitude]`.\n\n"
        "## Multi-route mode\n"
        "When `numRoutes` > 1, start and end must be the same depot address. "
        "Each route visits a subset of stops and returns to the depot.\n\n"
        "## Interactive docs\n"
        "- Swagger UI: `/docs`\n"
        "- ReDoc: `/redoc`\n"
        "- OpenAPI schema: `/openapi.json`"
    ),
    version="2.0.0",
    openapi_tags=OPENAPI_TAGS,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

DEFAULT_PROFILE = os.environ.get("ROUTE_OPTIMIZER_PROFILE", "driving-car")
DEFAULT_TIME_LIMIT_SECONDS = int(os.environ.get("ROUTE_OPTIMIZER_TIME_LIMIT", "5"))


class GeoPoint(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"type": "Point", "coordinates": [-73.8955, 40.8515]}]
        }
    )

    type: str = Field(
        default="Point",
        description="GeoJSON geometry type. Must be `Point`.",
        examples=["Point"],
    )
    coordinates: list[float] = Field(
        ...,
        min_length=2,
        max_length=2,
        description="GeoJSON coordinates as `[longitude, latitude]`.",
        examples=[[-73.8955, 40.8515]],
    )


class AddressVerification(BaseModel):
    is_verified: bool = Field(
        default=False,
        description="Whether the address has been verified.",
    )
    verified_at: str = Field(
        default="",
        description="ISO-8601 timestamp of the last verification.",
        examples=["2026-01-01T00:00:00Z"],
    )


class Address(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={"examples": [EXAMPLE_ADDRESS_START]}
    )

    address1: str = Field(default="", description="Primary street address.")
    address2: str = Field(default="", description="Secondary street address.")
    apartment: str = Field(default="", description="Apartment or unit number.")
    city: str = Field(default="", description="City name.")
    country: str = Field(default="", description="Country name.")
    location: GeoPoint = Field(
        ...,
        description="GeoJSON point with route coordinates.",
    )
    state: str = Field(default="", description="State or province.")
    verification: AddressVerification | None = Field(
        default=None,
        description="Optional Google address verification metadata.",
    )
    zipcode: str = Field(default="", description="Postal code.")


class AddressWithRouteOrder(Address):
    routeOrder: int = Field(
        ...,
        ge=1,
        description="1-based position of this address in the optimized route.",
        examples=[1],
    )


class SingleRouteRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "apiKey": "your_openrouteservice_api_key",
                    "start": EXAMPLE_ADDRESS_START,
                    "end": EXAMPLE_ADDRESS_END,
                    "stops": [EXAMPLE_ADDRESS_STOP, EXAMPLE_ADDRESS_STOP_B],
                }
            ]
        }
    )

    apiKey: str = Field(
        ...,
        min_length=1,
        description="OpenRouteService API key. Required in every request payload.",
        examples=["your_openrouteservice_api_key"],
    )
    start: Address = Field(..., description="Route start address.")
    end: Address = Field(..., description="Route end address.")
    stops: list[Address] = Field(
        ...,
        min_length=2,
        description="At least two stop addresses to visit between start and end.",
    )


class BalanceRoutesRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "apiKey": "your_openrouteservice_api_key",
                    "start": EXAMPLE_ADDRESS_START,
                    "end": EXAMPLE_ADDRESS_START,
                    "stops": [EXAMPLE_ADDRESS_STOP, EXAMPLE_ADDRESS_STOP_B],
                    "numRoutes": 2,
                }
            ]
        }
    )

    apiKey: str = Field(
        ...,
        min_length=1,
        description="OpenRouteService API key. Required in every request payload.",
        examples=["your_openrouteservice_api_key"],
    )
    start: Address = Field(..., description="Depot address where every route starts.")
    end: Address = Field(
        ...,
        description="Depot address where every route ends (must match `start`).",
    )
    stops: list[Address] = Field(
        ...,
        min_length=2,
        description="At least two stop addresses to split across routes.",
    )
    numRoutes: int = Field(
        ...,
        ge=2,
        description="Number of routes to create (at least 2).",
        examples=[2],
    )


class RouteResult(BaseModel):
    routeNumber: int = Field(
        ...,
        ge=1,
        description="1-based route identifier.",
        examples=[1],
    )
    addresses: list[AddressWithRouteOrder] = Field(
        ...,
        description=(
            "Addresses in visit order for this route (start, stops, end)."
        ),
    )
    distanceMeters: int = Field(
        ...,
        ge=0,
        description="Total road distance for this route in meters.",
        examples=[10500],
    )


class OptimizeResponse(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "routes": [
                        {
                            "routeNumber": 1,
                            "addresses": [
                                {**EXAMPLE_ADDRESS_START, "routeOrder": 1},
                                {**EXAMPLE_ADDRESS_STOP, "routeOrder": 2},
                                {**EXAMPLE_ADDRESS_START, "routeOrder": 3},
                            ],
                            "distanceMeters": 8500,
                        },
                        {
                            "routeNumber": 2,
                            "addresses": [
                                {**EXAMPLE_ADDRESS_START, "routeOrder": 1},
                                {**EXAMPLE_ADDRESS_STOP_B, "routeOrder": 2},
                                {**EXAMPLE_ADDRESS_START, "routeOrder": 3},
                            ],
                            "distanceMeters": 7200,
                        },
                    ],
                    "numRoutes": 2,
                    "totalDistanceMeters": 15700,
                }
            ]
        }
    )

    routes: list[RouteResult] = Field(
        ...,
        description="Optimized routes in order, each with ordered addresses.",
    )
    numRoutes: int = Field(
        ...,
        ge=1,
        description="Number of routes returned.",
        examples=[2],
    )
    totalDistanceMeters: int = Field(
        ...,
        ge=0,
        description="Combined road distance across all routes in meters.",
        examples=[15700],
    )


class HealthResponse(BaseModel):
    status: str = Field(
        ...,
        description="Service health status.",
        examples=["ok"],
    )


class ErrorResponse(BaseModel):
    detail: str = Field(..., description="Error message.")


def _resolve_api_key(explicit_key: str) -> str:
    api_key = explicit_key.strip()
    if not api_key:
        raise HTTPException(
            status_code=422,
            detail="apiKey is required in the request body.",
        )
    return api_key


# Backward-compatible alias for tests
_get_api_key = _resolve_api_key


def _location_from_address(address: Address) -> Location:
    coords = address.location.coordinates
    if len(coords) != 2:
        raise ValueError(
            f"Each address location must have [lon, lat] coordinates, got {coords!r}"
        )
    try:
        lon, lat = float(coords[0]), float(coords[1])
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Address coordinates must be numbers, got {coords!r}"
        ) from exc

    street_parts = [
        part
        for part in (address.address1, address.address2, address.apartment)
        if part
    ]
    return Location(
        lat=lat,
        lng=lon,
        street_address_1=", ".join(street_parts),
        city=address.city,
        state=address.state,
        zip=address.zipcode,
    )


def _addresses_match(a: Address, b: Address) -> bool:
    return (
        a.address1.strip().casefold() == b.address1.strip().casefold()
        and a.city.strip().casefold() == b.city.strip().casefold()
        and a.state.strip().casefold() == b.state.strip().casefold()
        and a.zipcode.strip().casefold() == b.zipcode.strip().casefold()
        and a.location.coordinates == b.location.coordinates
    )


def _address_for_location_index(
    index: int,
    *,
    start: Address,
    end: Address,
    stops: list[Address],
    multi_route: bool,
) -> Address:
    if index == 0:
        return start
    if multi_route:
        return stops[index - 1]
    num_locations = len(stops) + 2
    if index == num_locations - 1:
        return end
    return stops[index - 1]


def _build_route_result(
    route_number: int,
    ordered_indices: list[int],
    *,
    start: Address,
    end: Address,
    stops: list[Address],
    distance_meters: int,
    multi_route: bool,
) -> RouteResult:
    ordered_addresses = [
        AddressWithRouteOrder(
            routeOrder=position,
            **_address_for_location_index(
                idx,
                start=start,
                end=end,
                stops=stops,
                multi_route=multi_route,
            ).model_dump(),
        )
        for position, idx in enumerate(ordered_indices, start=1)
    ]
    return RouteResult(
        routeNumber=route_number,
        addresses=ordered_addresses,
        distanceMeters=distance_meters,
    )


def _parse_route_locations(
    start: Address,
    end: Address,
    stops: list[Address],
) -> tuple[Location, Location, list[Location]]:
    try:
        return (
            _location_from_address(start),
            _location_from_address(end),
            [_location_from_address(addr) for addr in stops],
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _run_single_route(
    *,
    start: Address,
    end: Address,
    stops: list[Address],
    api_key: str,
) -> OptimizeResponse:
    start_loc, end_loc, stop_locs = _parse_route_locations(start, end, stops)
    try:
        result = optimize_route(
            start_loc,
            stop_locs,
            end_loc,
            api_key=api_key,
            profile=DEFAULT_PROFILE,
            time_limit_seconds=DEFAULT_TIME_LIMIT_SECONDS,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    route_result = _build_route_result(
        1,
        result["ordered_indices"],
        start=start,
        end=end,
        stops=stops,
        distance_meters=result["total_distance_meters"],
        multi_route=False,
    )
    return OptimizeResponse(
        routes=[route_result],
        numRoutes=1,
        totalDistanceMeters=result["total_distance_meters"],
    )


def _run_balanced_routes(
    *,
    start: Address,
    end: Address,
    stops: list[Address],
    num_routes: int,
    api_key: str,
) -> OptimizeResponse:
    if not _addresses_match(start, end):
        raise HTTPException(
            status_code=422,
            detail=(
                "Balanced multi-route optimization requires the same start and "
                "end address (depot) for every route."
            ),
        )
    if num_routes > len(stops):
        raise HTTPException(
            status_code=422,
            detail=(
                f"Cannot create {num_routes} routes with only "
                f"{len(stops)} stop(s). Each route needs at least one stop."
            ),
        )

    start_loc, _, stop_locs = _parse_route_locations(start, end, stops)
    time_limit = compute_vrp_time_limit(len(stop_locs), num_routes)
    try:
        result = optimize_balanced_multi_route(
            start_loc,
            stop_locs,
            num_routes,
            api_key=api_key,
            profile=DEFAULT_PROFILE,
            time_limit_seconds=time_limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    route_results = [
        _build_route_result(
            route["route_number"],
            route["ordered_indices"],
            start=start,
            end=end,
            stops=stops,
            distance_meters=route["distance_meters"],
            multi_route=True,
        )
        for route in result["routes"]
    ]
    return OptimizeResponse(
        routes=route_results,
        numRoutes=num_routes,
        totalDistanceMeters=result["total_distance_meters"],
    )


_ROUTE_ERROR_RESPONSES = {
    422: {
        "model": ErrorResponse,
        "description": "Invalid request body or address coordinates.",
    },
    500: {
        "model": ErrorResponse,
        "description": "Unexpected server error.",
    },
    502: {
        "model": ErrorResponse,
        "description": "Route optimization failed or no route was found.",
    },
}


@app.get(
    "/health",
    response_model=HealthResponse,
    tags=["health"],
    summary="Health check",
    description="Returns `ok` when the service is running.",
)
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@app.post(
    "/routes/single",
    response_model=OptimizeResponse,
    tags=["routing"],
    summary="Optimize a single route",
    description=(
        "Optimize visit order for one route from `start` through all `stops` "
        "to `end`. Requires at least two stops and `apiKey` in the payload."
    ),
    responses=_ROUTE_ERROR_RESPONSES,
)
def optimize_single_route(request: SingleRouteRequest) -> OptimizeResponse:
    return _run_single_route(
        start=request.start,
        end=request.end,
        stops=request.stops,
        api_key=_resolve_api_key(request.apiKey),
    )


@app.post(
    "/routes/balance",
    response_model=OptimizeResponse,
    tags=["routing"],
    summary="Split stops across balanced routes",
    description=(
        "Split stops across `numRoutes` routes (minimum 2) from a shared depot. "
        "`start` and `end` must be the same address. Routes are balanced by "
        "driving distance."
    ),
    responses=_ROUTE_ERROR_RESPONSES,
)
def optimize_balanced_routes(request: BalanceRoutesRequest) -> OptimizeResponse:
    return _run_balanced_routes(
        start=request.start,
        end=request.end,
        stops=request.stops,
        num_routes=request.numRoutes,
        api_key=_resolve_api_key(request.apiKey),
    )
