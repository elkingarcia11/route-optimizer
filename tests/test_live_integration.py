from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from dotenv import load_dotenv
from fastapi.testclient import TestClient

from main import app
from route_optimizer import Location, build_distance_matrix_ors, optimize_route

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ROUTE_JSON = PROJECT_ROOT / "route.json"
EXAMPLE_REQUEST = PROJECT_ROOT / "examples" / "optimize.request.json"

MINIMAL_LOCATIONS = [
    [-73.8955, 40.8515],
    [-73.91335, 40.87995],
    [-73.90774, 40.88467],
    [-73.8955, 40.8515],
]


def _address_from_lonlat(lon: float, lat: float, address1: str = "") -> dict:
    return {
        "address1": address1,
        "location": {"type": "Point", "coordinates": [lon, lat]},
    }


START = _address_from_lonlat(
    MINIMAL_LOCATIONS[0][0], MINIMAL_LOCATIONS[0][1], "Start"
)
END = _address_from_lonlat(
    MINIMAL_LOCATIONS[-1][0], MINIMAL_LOCATIONS[-1][1], "End"
)
STOPS = [
    _address_from_lonlat(lon, lat, label)
    for lon, lat, label in [
        (MINIMAL_LOCATIONS[1][0], MINIMAL_LOCATIONS[1][1], "Stop A"),
        (MINIMAL_LOCATIONS[2][0], MINIMAL_LOCATIONS[2][1], "Stop B"),
    ]
]


@pytest.fixture(scope="module", autouse=True)
def load_dotenv_for_integration() -> None:
    load_dotenv(PROJECT_ROOT / ".env")


@pytest.fixture(scope="module")
def api_key() -> str:
    key = os.environ.get("ORS_API_KEY")
    if not key:
        pytest.skip("ORS_API_KEY is not set in .env")
    return key


def _assert_valid_route_indices(
    location_count: int,
    ordered_indices: list[int],
    total_distance_meters: int,
) -> None:
    assert len(ordered_indices) == location_count
    assert ordered_indices[0] == 0
    assert ordered_indices[-1] == location_count - 1
    assert sorted(ordered_indices) == list(range(location_count))
    assert total_distance_meters > 0


def _assert_valid_address_route(
    address_count: int,
    ordered_addresses: list[dict],
    total_distance_meters: int,
    total_duration_seconds: int,
) -> None:
    assert len(ordered_addresses) == address_count
    assert [addr["routeOrder"] for addr in ordered_addresses] == list(
        range(1, address_count + 1)
    )
    assert total_distance_meters > 0
    assert total_duration_seconds > 0


pytestmark = pytest.mark.integration


class TestLiveOpenRouteService:
    def test_build_distance_matrix(self, api_key: str) -> None:
        locations = [
            Location(lat=40.8515, lng=-73.8955),
            Location(lat=40.87995, lng=-73.91335),
            Location(lat=40.88467, lng=-73.90774),
        ]

        matrix = build_distance_matrix_ors(locations, api_key=api_key)

        assert len(matrix) == 3
        assert all(len(row) == 3 for row in matrix)
        assert matrix[0][0] == 0
        assert matrix[1][1] == 0
        assert matrix[2][2] == 0
        assert matrix[0][1] > 0
        assert matrix[1][0] > 0

    def test_optimize_route(self, api_key: str) -> None:
        start = Location(lat=MINIMAL_LOCATIONS[0][1], lng=MINIMAL_LOCATIONS[0][0])
        end = Location(lat=MINIMAL_LOCATIONS[-1][1], lng=MINIMAL_LOCATIONS[-1][0])
        stops = [
            Location(lat=loc[1], lng=loc[0]) for loc in MINIMAL_LOCATIONS[1:-1]
        ]

        result = optimize_route(
            start,
            stops,
            end,
            api_key=api_key,
            time_limit_seconds=5,
        )

        _assert_valid_route_indices(
            len(MINIMAL_LOCATIONS),
            result["ordered_indices"],
            result["total_distance_meters"],
        )
        assert result["total_distance_meters"] > 0
        assert result["total_duration_seconds"] > 0
        assert result["optimization_metric"] == "duration"
        assert result["distance_source"] == "openrouteservice"
        assert len(result["ordered_locations"]) == len(MINIMAL_LOCATIONS)


class TestLiveHttpApi:
    def test_optimize_endpoint(self, api_key: str) -> None:
        client = TestClient(app)
        response = client.post(
            "/optimize",
            json={
                "start": START,
                "end": END,
                "stops": STOPS,
                "apiKey": api_key,
            },
        )

        assert response.status_code == 200
        body = response.json()
        _assert_valid_address_route(
            len(STOPS) + 2,
            body["addresses"],
            body["totalDistanceMeters"],
            body["totalDurationSeconds"],
        )
        assert body["addresses"][0]["address1"] == "Start"
        assert body["addresses"][-1]["address1"] == "End"

    def test_optimize_example_payload(self, api_key: str) -> None:
        if not EXAMPLE_REQUEST.exists():
            pytest.skip("examples/optimize.request.json not found")

        payload = json.loads(EXAMPLE_REQUEST.read_text(encoding="utf-8"))
        payload["apiKey"] = api_key

        client = TestClient(app)
        response = client.post("/optimize", json=payload)

        assert response.status_code == 200
        body = response.json()
        _assert_valid_address_route(
            len(payload["stops"]) + 2,
            body["addresses"],
            body["totalDistanceMeters"],
            body["totalDurationSeconds"],
        )

    def test_optimize_route_json_payload(self, api_key: str) -> None:
        if not ROUTE_JSON.exists():
            pytest.skip("route.json not found")

        payload = json.loads(ROUTE_JSON.read_text(encoding="utf-8"))
        start = {
            "address1": payload["start"].get("street_address_1", ""),
            "city": payload["start"].get("city", ""),
            "state": payload["start"].get("state", ""),
            "zipcode": payload["start"].get("zip", ""),
            "location": {
                "type": "Point",
                "coordinates": [payload["start"]["lng"], payload["start"]["lat"]],
            },
        }
        end = {
            "address1": payload["end"].get("street_address_1", ""),
            "city": payload["end"].get("city", ""),
            "state": payload["end"].get("state", ""),
            "zipcode": payload["end"].get("zip", ""),
            "location": {
                "type": "Point",
                "coordinates": [payload["end"]["lng"], payload["end"]["lat"]],
            },
        }
        stops = [
            {
                "address1": loc.get("street_address_1", ""),
                "city": loc.get("city", ""),
                "state": loc.get("state", ""),
                "zipcode": loc.get("zip", ""),
                "location": {
                    "type": "Point",
                    "coordinates": [loc["lng"], loc["lat"]],
                },
            }
            for loc in payload["stops"]
        ]

        client = TestClient(app)
        response = client.post(
            "/optimize",
            json={
                "start": start,
                "end": end,
                "stops": stops,
                "apiKey": api_key,
            },
        )

        assert response.status_code == 200
        body = response.json()
        _assert_valid_address_route(
            len(stops) + 2,
            body["addresses"],
            body["totalDistanceMeters"],
            body["totalDurationSeconds"],
        )
        assert body["addresses"][0]["city"] == payload["start"]["city"]
