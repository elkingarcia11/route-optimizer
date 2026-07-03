"""HTTP API service for route optimization."""

from __future__ import annotations

import os

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from route_optimizer import Location, optimize_route

load_dotenv()

OPENAPI_TAGS = [
    {
        "name": "health",
        "description": "Service health checks.",
    },
    {
        "name": "routing",
        "description": (
            "Optimize visit order for a list of addresses. "
            "The first address is the route start and the last is the end."
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

EXAMPLE_ADDRESS_END = {
    "address1": "2249 Washington Ave",
    "city": "Bronx",
    "state": "NY",
    "zipcode": "10451",
    "location": {"type": "Point", "coordinates": [-73.8955, 40.8515]},
}

app = FastAPI(
    title="Route Optimizer API",
    description=(
        "Reorder stops between a fixed start and end using OpenRouteService "
        "road distances and Google OR-Tools.\n\n"
        "## Coordinate format\n"
        "Address `location.coordinates` uses GeoJSON order: "
        "`[longitude, latitude]`.\n\n"
        "## Interactive docs\n"
        "- Swagger UI: `/docs`\n"
        "- ReDoc: `/redoc`\n"
        "- OpenAPI schema: `/openapi.json`"
    ),
    version="1.0.0",
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


class OptimizeRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "addresses": [
                        EXAMPLE_ADDRESS_START,
                        EXAMPLE_ADDRESS_STOP,
                        EXAMPLE_ADDRESS_END,
                    ]
                }
            ]
        }
    )

    addresses: list[Address] = Field(
        ...,
        min_length=2,
        description=(
            "Ordered list of addresses. The first entry is the route start, "
            "the last is the route end, and entries in between are stops."
        ),
    )


class OptimizeResponse(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "addresses": [
                        {**EXAMPLE_ADDRESS_START, "routeOrder": 1},
                        {**EXAMPLE_ADDRESS_STOP, "routeOrder": 2},
                        {**EXAMPLE_ADDRESS_END, "routeOrder": 3},
                    ],
                    "totalDistanceMeters": 21220,
                }
            ]
        }
    )

    addresses: list[AddressWithRouteOrder] = Field(
        ...,
        description="Input addresses reordered for the shortest road route.",
    )
    totalDistanceMeters: int = Field(
        ...,
        ge=0,
        description="Total optimized route distance in meters.",
        examples=[21220],
    )


class HealthResponse(BaseModel):
    status: str = Field(
        ...,
        description="Service health status.",
        examples=["ok"],
    )


class ErrorResponse(BaseModel):
    detail: str = Field(..., description="Error message.")


def _get_api_key() -> str:
    api_key = os.environ.get("ORS_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="ORS_API_KEY is not configured.",
        )
    return api_key


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
    "/optimize",
    response_model=OptimizeResponse,
    tags=["routing"],
    summary="Optimize route order",
    description=(
        "Accepts a list of `core.Address`-compatible objects and returns the "
        "same addresses in optimized visit order with `routeOrder` and "
        "`totalDistanceMeters`.\n\n"
        "Routing uses OpenRouteService road distances and OR-Tools."
    ),
    responses={
        422: {
            "model": ErrorResponse,
            "description": "Invalid request body or address coordinates.",
        },
        500: {
            "model": ErrorResponse,
            "description": "OpenRouteService API key is not configured.",
        },
        502: {
            "model": ErrorResponse,
            "description": "Route optimization failed or no route was found.",
        },
    },
)
def optimize(request: OptimizeRequest) -> OptimizeResponse:
    addresses = request.addresses

    try:
        start = _location_from_address(addresses[0])
        end = _location_from_address(addresses[-1])
        stops = [_location_from_address(addr) for addr in addresses[1:-1]]
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
    ordered_addresses = [
        AddressWithRouteOrder(
            routeOrder=position,
            **addresses[idx].model_dump(),
        )
        for position, idx in enumerate(ordered_indices, start=1)
    ]
    return OptimizeResponse(
        addresses=ordered_addresses,
        totalDistanceMeters=result["total_distance_meters"],
    )
