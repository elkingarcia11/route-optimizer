from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from main import Address, GeoPoint, _location_from_address, _resolve_api_key, app
from route_optimizer import Location


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _address(
    lon: float,
    lat: float,
    *,
    address1: str = "",
    address2: str = "",
    apartment: str = "",
    city: str = "",
    state: str = "",
    country: str = "",
    zipcode: str = "",
) -> dict:
    return {
        "address1": address1,
        "address2": address2,
        "apartment": apartment,
        "city": city,
        "country": country,
        "state": state,
        "zipcode": zipcode,
        "location": {"type": "Point", "coordinates": [lon, lat]},
    }


def _single_route_payload(
    *,
    start: dict,
    end: dict,
    stops: list[dict],
    api_key: str | None = "test-api-key",
) -> dict:
    payload: dict = {
        "start": start,
        "end": end,
        "stops": stops,
    }
    if api_key is not None:
        payload["apiKey"] = api_key
    return payload


def _balance_routes_payload(
    *,
    start: dict,
    end: dict,
    stops: list[dict],
    num_routes: int = 2,
    api_key: str | None = "test-api-key",
) -> dict:
    payload: dict = {
        "start": start,
        "end": end,
        "stops": stops,
        "numRoutes": num_routes,
    }
    if api_key is not None:
        payload["apiKey"] = api_key
    return payload


class TestResolveApiKey:
    def test_returns_explicit_key(self) -> None:
        assert _resolve_api_key("payload-key") == "payload-key"

    def test_strips_whitespace(self) -> None:
        assert _resolve_api_key("  payload-key  ") == "payload-key"

    def test_raises_when_empty(self) -> None:
        with pytest.raises(HTTPException) as exc_info:
            _resolve_api_key("")
        assert exc_info.value.status_code == 422
        assert exc_info.value.detail == "apiKey is required in the request body."

    def test_raises_when_whitespace_only(self) -> None:
        with pytest.raises(HTTPException) as exc_info:
            _resolve_api_key("  ")
        assert exc_info.value.status_code == 422


class TestLocationFromAddress:
    def test_converts_address_to_location(self) -> None:
        address = Address(
            address1="2249 Washington Ave",
            city="Bronx",
            state="NY",
            location=GeoPoint(coordinates=[-73.8955, 40.8515]),
        )
        location = _location_from_address(address)
        assert location == Location(
            lat=40.8515,
            lng=-73.8955,
            street_address_1="2249 Washington Ave",
            city="Bronx",
            state="NY",
        )

    def test_combines_address_lines(self) -> None:
        address = Address(
            address1="123 Main St",
            address2="Suite 4",
            apartment="Apt 2",
            location=GeoPoint(coordinates=[-73.0, 40.0]),
        )
        location = _location_from_address(address)
        assert location.street_address_1 == "123 Main St, Suite 4, Apt 2"

    def test_rejects_invalid_coordinate_length(self) -> None:
        with pytest.raises(ValidationError):
            Address.model_validate(
                {
                    "location": {"type": "Point", "coordinates": [-73.8955]},
                }
            )

    def test_rejects_non_numeric_coordinates(self) -> None:
        with pytest.raises(ValidationError):
            Address.model_validate(
                {
                    "location": {"type": "Point", "coordinates": ["a", "b"]},
                }
            )


class TestHealthEndpoint:
    def test_returns_ok(self, client: TestClient) -> None:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


class TestOpenApiDocs:
    def test_swagger_ui_available(self, client: TestClient) -> None:
        response = client.get("/docs")
        assert response.status_code == 200
        assert "swagger" in response.text.lower()

    def test_redoc_available(self, client: TestClient) -> None:
        response = client.get("/redoc")
        assert response.status_code == 200

    def test_openapi_schema_available(self, client: TestClient) -> None:
        response = client.get("/openapi.json")
        assert response.status_code == 200
        schema = response.json()
        assert schema["info"]["title"] == "Route Optimizer API"
        assert "/routes/single" in schema["paths"]
        assert "/routes/balance" in schema["paths"]
        assert "/optimize" not in schema["paths"]
        single_post = schema["paths"]["/routes/single"]["post"]
        assert single_post["summary"] == "Optimize a single route"
        assert "422" in single_post["responses"]
        single_props = schema["components"]["schemas"]["SingleRouteRequest"]["properties"]
        single_required = schema["components"]["schemas"]["SingleRouteRequest"]["required"]
        assert "apiKey" in single_props
        assert "start" in single_props
        assert "end" in single_props
        assert "stops" in single_props
        assert set(single_required) >= {"apiKey", "start", "end", "stops"}
        balance_props = schema["components"]["schemas"]["BalanceRoutesRequest"]["properties"]
        assert "numRoutes" in balance_props
        response_props = schema["components"]["schemas"]["OptimizeResponse"]["properties"]
        assert "routes" in response_props
        assert "numRoutes" in response_props
        assert "totalDistanceMeters" in response_props
        route_props = schema["components"]["schemas"]["RouteResult"]["properties"]
        assert "routeNumber" in route_props
        assert "addresses" in route_props
        assert "distanceMeters" in route_props
        assert schema["info"]["version"] == "2.0.0"


class TestSingleRouteEndpoint:
    START = _address(-73.8955, 40.8515, address1="Depot", city="Bronx", state="NY")
    STOP_A = _address(-73.91335, 40.87995, address1="Stop A", city="Bronx", state="NY")
    STOP_B = _address(-73.90774, 40.88467, address1="Stop B", city="Bronx", state="NY")
    END = _address(-73.90774, 40.88467, address1="End", city="Bronx", state="NY")
    TWO_STOPS = [STOP_A, STOP_B]

    def test_returns_422_when_api_key_missing(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ORS_API_KEY", raising=False)
        response = client.post(
            "/routes/single",
            json=_single_route_payload(
                start=self.START,
                end=self.END,
                stops=self.TWO_STOPS,
                api_key=None,
            ),
        )
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert any(err["loc"] == ["body", "apiKey"] for err in detail)

    def test_returns_422_when_api_key_empty(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ORS_API_KEY", raising=False)
        response = client.post(
            "/routes/single",
            json={
                **_single_route_payload(
                    start=self.START,
                    end=self.END,
                    stops=self.TWO_STOPS,
                    api_key=None,
                ),
                "apiKey": "",
            },
        )
        assert response.status_code == 422

    def test_returns_422_for_invalid_location(self, client: TestClient) -> None:
        response = client.post(
            "/routes/single",
            json=_single_route_payload(
                start=self.START,
                end=self.END,
                stops=[
                    {"location": {"type": "Point", "coordinates": [-73.91335]}},
                    self.STOP_B,
                ],
            ),
        )
        assert response.status_code == 422

    def test_returns_422_when_stops_empty(self, client: TestClient) -> None:
        response = client.post(
            "/routes/single",
            json=_single_route_payload(
                start=self.START,
                end=self.END,
                stops=[],
            ),
        )
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert any(err["loc"] == ["body", "stops"] for err in detail)

    def test_returns_422_when_only_one_stop(self, client: TestClient) -> None:
        response = client.post(
            "/routes/single",
            json=_single_route_payload(
                start=self.START,
                end=self.END,
                stops=[self.STOP_A],
            ),
        )
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert any(err["loc"] == ["body", "stops"] for err in detail)

    @patch("main.optimize_route")
    def test_returns_optimized_single_route(
        self,
        mock_optimize_route,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("ORS_API_KEY", "test-api-key")
        mock_optimize_route.return_value = {
            "ordered_indices": [0, 2, 1, 3],
            "total_distance_meters": 12345,
        }

        response = client.post(
            "/routes/single",
            json=_single_route_payload(
                start=self.START,
                end=self.END,
                stops=[self.STOP_A, self.STOP_B],
                api_key="test-api-key",
            ),
        )

        assert response.status_code == 200
        body = response.json()
        assert body["numRoutes"] == 1
        assert body["totalDistanceMeters"] == 12345
        assert len(body["routes"]) == 1
        route = body["routes"][0]
        assert route["routeNumber"] == 1
        assert route["distanceMeters"] == 12345
        assert [addr["routeOrder"] for addr in route["addresses"]] == [1, 2, 3, 4]
        assert route["addresses"][0]["address1"] == "Depot"
        assert route["addresses"][1]["address1"] == "Stop B"
        assert route["addresses"][2]["address1"] == "Stop A"
        assert route["addresses"][3]["address1"] == "End"

        mock_optimize_route.assert_called_once()
        start, stops, end = mock_optimize_route.call_args.args[:3]
        assert mock_optimize_route.call_args.kwargs["api_key"] == "test-api-key"
        assert start == Location(
            lat=40.8515,
            lng=-73.8955,
            street_address_1="Depot",
            city="Bronx",
            state="NY",
        )
        assert end == Location(
            lat=40.88467,
            lng=-73.90774,
            street_address_1="End",
            city="Bronx",
            state="NY",
        )
        assert stops == [
            Location(
                lat=40.87995,
                lng=-73.91335,
                street_address_1="Stop A",
                city="Bronx",
                state="NY",
            ),
            Location(
                lat=40.88467,
                lng=-73.90774,
                street_address_1="Stop B",
                city="Bronx",
                state="NY",
            ),
        ]

    @patch("main.optimize_balanced_multi_route")
    def test_returns_balanced_multi_route(
        self,
        mock_optimize_balanced,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("ORS_API_KEY", "test-api-key")
        mock_optimize_balanced.return_value = {
            "routes": [
                {
                    "route_number": 1,
                    "ordered_indices": [0, 1, 0],
                    "distance_meters": 8000,
                },
                {
                    "route_number": 2,
                    "ordered_indices": [0, 2, 0],
                    "distance_meters": 7500,
                },
            ],
            "total_distance_meters": 15500,
        }

        response = client.post(
            "/routes/balance",
            json=_balance_routes_payload(
                start=self.START,
                end=self.START,
                stops=[self.STOP_A, self.STOP_B],
                api_key="test-api-key",
            ),
        )

        assert response.status_code == 200
        body = response.json()
        assert body["numRoutes"] == 2
        assert body["totalDistanceMeters"] == 15500
        assert len(body["routes"]) == 2

        route1 = body["routes"][0]
        assert route1["routeNumber"] == 1
        assert route1["distanceMeters"] == 8000
        assert route1["addresses"][0]["address1"] == "Depot"
        assert route1["addresses"][1]["address1"] == "Stop A"
        assert route1["addresses"][2]["address1"] == "Depot"

        route2 = body["routes"][1]
        assert route2["routeNumber"] == 2
        assert route2["addresses"][1]["address1"] == "Stop B"

        mock_optimize_balanced.assert_called_once()
        depot, stops = mock_optimize_balanced.call_args.args[:2]
        assert mock_optimize_balanced.call_args.args[2] == 2
        assert depot.lat == 40.8515
        assert len(stops) == 2

    def test_returns_422_when_multi_route_start_end_differ(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ORS_API_KEY", "test-api-key")
        response = client.post(
            "/routes/balance",
            json=_balance_routes_payload(
                start=self.START,
                end=self.END,
                stops=[self.STOP_A, self.STOP_B],
            ),
        )
        assert response.status_code == 422
        assert "same start and end" in response.json()["detail"]

    def test_returns_422_when_too_many_routes_for_stops(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ORS_API_KEY", "test-api-key")
        response = client.post(
            "/routes/balance",
            json=_balance_routes_payload(
                start=self.START,
                end=self.START,
                stops=self.TWO_STOPS,
                num_routes=3,
            ),
        )
        assert response.status_code == 422
        assert "Cannot create 3 routes" in response.json()["detail"]

    @patch("main.optimize_route")
    def test_returns_502_when_optimizer_fails(
        self,
        mock_optimize_route,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("ORS_API_KEY", "test-api-key")
        mock_optimize_route.side_effect = RuntimeError("No solution found.")

        response = client.post(
            "/routes/single",
            json=_single_route_payload(
                start=self.START,
                end=self.END,
                stops=self.TWO_STOPS,
                api_key="test-api-key",
            ),
        )

        assert response.status_code == 502
        assert response.json()["detail"] == "No solution found."

    def test_returns_422_when_api_key_empty_in_payload(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ORS_API_KEY", raising=False)
        response = client.post(
            "/routes/single",
            json={
                **_single_route_payload(
                    start=self.START,
                    end=self.END,
                    stops=self.TWO_STOPS,
                    api_key=None,
                ),
                "apiKey": "  ",
            },
        )
        assert response.status_code == 422

    @patch("main.optimize_route")
    def test_uses_api_key_from_payload(
        self,
        mock_optimize_route,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("ORS_API_KEY", raising=False)
        mock_optimize_route.return_value = {
            "ordered_indices": [0, 1, 2, 3],
            "total_distance_meters": 1000,
        }

        response = client.post(
            "/routes/single",
            json=_single_route_payload(
                start=self.START,
                end=self.END,
                stops=self.TWO_STOPS,
                api_key="payload-only-key",
            ),
        )

        assert response.status_code == 200
        assert mock_optimize_route.call_args.kwargs["api_key"] == "payload-only-key"

    @patch("main.optimize_route")
    def test_passes_payload_api_key_to_optimizer(
        self,
        mock_optimize_route,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("ORS_API_KEY", "env-key")
        mock_optimize_route.return_value = {
            "ordered_indices": [0, 1, 2, 3],
            "total_distance_meters": 1000,
        }

        response = client.post(
            "/routes/single",
            json=_single_route_payload(
                start=self.START,
                end=self.END,
                stops=self.TWO_STOPS,
                api_key="payload-key",
            ),
        )

        assert response.status_code == 200
        assert mock_optimize_route.call_args.kwargs["api_key"] == "payload-key"

    def test_returns_422_when_required_fields_missing(self, client: TestClient) -> None:
        response = client.post("/routes/single", json={})
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert any(err["loc"] == ["body", "start"] for err in detail)
        assert any(err["loc"] == ["body", "end"] for err in detail)
        assert any(err["loc"] == ["body", "stops"] for err in detail)
        assert any(err["loc"] == ["body", "apiKey"] for err in detail)

    def test_returns_422_for_non_numeric_coordinates(self, client: TestClient) -> None:
        response = client.post(
            "/routes/single",
            json=_single_route_payload(
                start={"location": {"type": "Point", "coordinates": ["a", "b"]}},
                end=self.END,
                stops=self.TWO_STOPS,
            ),
        )
        assert response.status_code == 422

    def test_returns_422_for_invalid_json_body(self, client: TestClient) -> None:
        response = client.post(
            "/routes/single",
            content=b"not-json",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 422

    @patch("main.optimize_route")
    def test_returns_502_for_unreachable_route(
        self,
        mock_optimize_route,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("ORS_API_KEY", "test-api-key")
        mock_optimize_route.side_effect = RuntimeError(
            "No route found between location 0 and 2."
        )

        response = client.post(
            "/routes/single",
            json=_single_route_payload(
                start=self.START,
                end=self.END,
                stops=self.TWO_STOPS,
                api_key="test-api-key",
            ),
        )

        assert response.status_code == 502
        assert response.json()["detail"] == "No route found between location 0 and 2."

    @patch("main.optimize_route")
    def test_accepts_large_stop_list(
        self,
        mock_optimize_route,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("ORS_API_KEY", "test-api-key")
        stops = [
            _address(-73.89 + i * 0.001, 40.85 + i * 0.001, address1=f"Stop {i}")
            for i in range(55)
        ]
        ordered_indices = list(range(len(stops) + 2))
        mock_optimize_route.return_value = {
            "ordered_indices": ordered_indices,
            "total_distance_meters": 999999,
        }

        response = client.post(
            "/routes/single",
            json=_single_route_payload(
                start=self.START,
                end=self.END,
                stops=stops,
            ),
        )

        assert response.status_code == 200
        body = response.json()
        assert len(body["routes"][0]["addresses"]) == 57
        assert body["routes"][0]["addresses"][0]["routeOrder"] == 1
        assert body["routes"][0]["addresses"][-1]["routeOrder"] == 57
        start, stops_loc, end = mock_optimize_route.call_args.args[:3]
        assert len(stops_loc) == 55

    @patch("main.optimize_route")
    def test_preserves_verification_metadata(
        self,
        mock_optimize_route,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("ORS_API_KEY", "test-api-key")
        start = {
            **_address(-73.8955, 40.8515, address1="Start"),
            "verification": {"is_verified": True, "verified_at": "2026-01-01T00:00:00Z"},
        }
        mock_optimize_route.return_value = {
            "ordered_indices": [0, 1, 2, 3],
            "total_distance_meters": 1000,
        }

        response = client.post(
            "/routes/single",
            json=_single_route_payload(
                start=start,
                end=self.END,
                stops=self.TWO_STOPS,
            ),
        )

        assert response.status_code == 200
        assert response.json()["routes"][0]["addresses"][0]["verification"] == {
            "is_verified": True,
            "verified_at": "2026-01-01T00:00:00Z",
        }

    def test_returns_422_when_location_missing(self, client: TestClient) -> None:
        response = client.post(
            "/routes/single",
            json=_single_route_payload(
                start={"address1": "Start", "city": "Bronx"},
                end=self.END,
                stops=self.TWO_STOPS,
            ),
        )
        assert response.status_code == 422

    @patch("main.optimize_route")
    def test_preserves_all_address_fields_in_response(
        self,
        mock_optimize_route,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("ORS_API_KEY", "test-api-key")
        start = _address(
            -73.8955,
            40.8515,
            address1="123 Main St",
            address2="Floor 2",
            apartment="Unit 5",
            city="Bronx",
            state="NY",
            country="US",
            zipcode="10451",
        )
        mock_optimize_route.return_value = {
            "ordered_indices": [0, 1, 2, 3],
            "total_distance_meters": 1000,
        }

        response = client.post(
            "/routes/single",
            json=_single_route_payload(
                start=start,
                end=self.END,
                stops=self.TWO_STOPS,
            ),
        )

        assert response.status_code == 200
        addr = response.json()["routes"][0]["addresses"][0]
        assert addr["address1"] == "123 Main St"
        assert addr["address2"] == "Floor 2"
        assert addr["apartment"] == "Unit 5"
        assert addr["city"] == "Bronx"
        assert addr["state"] == "NY"
        assert addr["country"] == "US"
        assert addr["zipcode"] == "10451"
        assert addr["routeOrder"] == 1

    def test_returns_422_for_invalid_payload_shape(self, client: TestClient) -> None:
        response = client.post(
            "/routes/single",
            json={"addresses": [[-73.8955, 40.8515], [-73.90774, 40.88467]]},
        )
        assert response.status_code == 422


class TestBalanceRoutesEndpoint:
    DEPOT = TestSingleRouteEndpoint.START
    TWO_STOPS = TestSingleRouteEndpoint.TWO_STOPS

    @patch("main.optimize_balanced_multi_route")
    def test_routes_balance_splits_stops(
        self,
        mock_optimize_balanced,
        client: TestClient,
    ) -> None:
        mock_optimize_balanced.return_value = {
            "routes": [
                {
                    "route_number": 1,
                    "ordered_indices": [0, 1, 0],
                    "distance_meters": 8000,
                },
                {
                    "route_number": 2,
                    "ordered_indices": [0, 2, 0],
                    "distance_meters": 7500,
                },
            ],
            "total_distance_meters": 15500,
        }

        response = client.post(
            "/routes/balance",
            json=_balance_routes_payload(
                start=self.DEPOT,
                end=self.DEPOT,
                stops=self.TWO_STOPS,
            ),
        )

        assert response.status_code == 200
        body = response.json()
        assert body["numRoutes"] == 2
        assert body["totalDistanceMeters"] == 15500
        mock_optimize_balanced.assert_called_once()

    def test_routes_balance_requires_at_least_two_routes(
        self, client: TestClient
    ) -> None:
        response = client.post(
            "/routes/balance",
            json={
                "apiKey": "test-api-key",
                "start": self.DEPOT,
                "end": self.DEPOT,
                "stops": self.TWO_STOPS,
                "numRoutes": 1,
            },
        )
        assert response.status_code == 422

    def test_routes_balance_requires_matching_depot(
        self, client: TestClient
    ) -> None:
        response = client.post(
            "/routes/balance",
            json=_balance_routes_payload(
                start=self.DEPOT,
                end=TestSingleRouteEndpoint.END,
                stops=self.TWO_STOPS,
            ),
        )
        assert response.status_code == 422
        assert "same start and end" in response.json()["detail"]
