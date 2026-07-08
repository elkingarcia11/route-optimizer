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


def _optimize_payload(
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
        assert "/optimize" in schema["paths"]
        assert "/routes/single" not in schema["paths"]
        assert "/routes/balance" not in schema["paths"]
        optimize_post = schema["paths"]["/optimize"]["post"]
        assert optimize_post["summary"] == "Optimize route order"
        assert "422" in optimize_post["responses"]
        props = schema["components"]["schemas"]["OptimizeRequest"]["properties"]
        required = schema["components"]["schemas"]["OptimizeRequest"]["required"]
        assert "apiKey" in props
        assert "start" in props
        assert "end" in props
        assert "stops" in props
        assert set(required) >= {"apiKey", "start", "end", "stops"}
        response_props = schema["components"]["schemas"]["OptimizeResponse"]["properties"]
        assert "addresses" in response_props
        assert "totalDistanceMeters" in response_props
        assert "totalDurationSeconds" in response_props
        assert schema["info"]["version"] == "2.2.0"


class TestOptimizeEndpoint:
    START = _address(-73.8955, 40.8515, address1="Depot", city="Bronx", state="NY")
    STOP_A = _address(-73.91335, 40.87995, address1="Stop A", city="Bronx", state="NY")
    STOP_B = _address(-73.90774, 40.88467, address1="Stop B", city="Bronx", state="NY")
    END = _address(-73.90774, 40.88467, address1="End", city="Bronx", state="NY")
    TWO_STOPS = [STOP_A, STOP_B]

    def test_returns_422_when_api_key_missing(self, client: TestClient) -> None:
        response = client.post(
            "/optimize",
            json=_optimize_payload(
                start=self.START,
                end=self.END,
                stops=self.TWO_STOPS,
                api_key=None,
            ),
        )
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert any(err["loc"] == ["body", "apiKey"] for err in detail)

    def test_returns_422_when_api_key_empty(self, client: TestClient) -> None:
        response = client.post(
            "/optimize",
            json={
                **_optimize_payload(
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
            "/optimize",
            json=_optimize_payload(
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
            "/optimize",
            json=_optimize_payload(
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
            "/optimize",
            json=_optimize_payload(
                start=self.START,
                end=self.END,
                stops=[self.STOP_A],
            ),
        )
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert any(err["loc"] == ["body", "stops"] for err in detail)

    @patch("main.optimize_route")
    def test_returns_optimized_route(
        self,
        mock_optimize_route,
        client: TestClient,
    ) -> None:
        mock_optimize_route.return_value = {
            "ordered_indices": [0, 2, 1, 3],
            "total_distance_meters": 12345,
            "total_duration_seconds": 987,
        }

        response = client.post(
            "/optimize",
            json=_optimize_payload(
                start=self.START,
                end=self.END,
                stops=self.TWO_STOPS,
                api_key="test-api-key",
            ),
        )

        assert response.status_code == 200
        body = response.json()
        assert body["totalDistanceMeters"] == 12345
        assert body["totalDurationSeconds"] == 987
        assert [addr["routeOrder"] for addr in body["addresses"]] == [1, 2, 3, 4]
        assert body["addresses"][0]["address1"] == "Depot"
        assert body["addresses"][1]["address1"] == "Stop B"
        assert body["addresses"][2]["address1"] == "Stop A"
        assert body["addresses"][3]["address1"] == "End"

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

    @patch("main.optimize_route")
    def test_returns_502_when_optimizer_fails(
        self,
        mock_optimize_route,
        client: TestClient,
    ) -> None:
        mock_optimize_route.side_effect = RuntimeError("No solution found.")

        response = client.post(
            "/optimize",
            json=_optimize_payload(
                start=self.START,
                end=self.END,
                stops=self.TWO_STOPS,
                api_key="test-api-key",
            ),
        )

        assert response.status_code == 502
        assert response.json()["detail"] == "No solution found."

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
            "total_duration_seconds": 120,
        }

        response = client.post(
            "/optimize",
            json=_optimize_payload(
                start=self.START,
                end=self.END,
                stops=self.TWO_STOPS,
                api_key="payload-only-key",
            ),
        )

        assert response.status_code == 200
        assert mock_optimize_route.call_args.kwargs["api_key"] == "payload-only-key"

    def test_returns_422_when_required_fields_missing(self, client: TestClient) -> None:
        response = client.post("/optimize", json={})
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert any(err["loc"] == ["body", "start"] for err in detail)
        assert any(err["loc"] == ["body", "end"] for err in detail)
        assert any(err["loc"] == ["body", "stops"] for err in detail)
        assert any(err["loc"] == ["body", "apiKey"] for err in detail)

    def test_returns_422_for_non_numeric_coordinates(self, client: TestClient) -> None:
        response = client.post(
            "/optimize",
            json=_optimize_payload(
                start={"location": {"type": "Point", "coordinates": ["a", "b"]}},
                end=self.END,
                stops=self.TWO_STOPS,
            ),
        )
        assert response.status_code == 422

    def test_returns_422_for_invalid_json_body(self, client: TestClient) -> None:
        response = client.post(
            "/optimize",
            content=b"not-json",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 422

    @patch("main.optimize_route")
    def test_returns_502_for_unreachable_route(
        self,
        mock_optimize_route,
        client: TestClient,
    ) -> None:
        mock_optimize_route.side_effect = RuntimeError(
            "No route found between location 0 and 2."
        )

        response = client.post(
            "/optimize",
            json=_optimize_payload(
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
    ) -> None:
        stops = [
            _address(-73.89 + i * 0.001, 40.85 + i * 0.001, address1=f"Stop {i}")
            for i in range(55)
        ]
        ordered_indices = list(range(len(stops) + 2))
        mock_optimize_route.return_value = {
            "ordered_indices": ordered_indices,
            "total_distance_meters": 999999,
            "total_duration_seconds": 99999,
        }

        response = client.post(
            "/optimize",
            json=_optimize_payload(
                start=self.START,
                end=self.END,
                stops=stops,
            ),
        )

        assert response.status_code == 200
        body = response.json()
        assert len(body["addresses"]) == 57
        assert body["addresses"][0]["routeOrder"] == 1
        assert body["addresses"][-1]["routeOrder"] == 57
        start, stops_loc, end = mock_optimize_route.call_args.args[:3]
        assert len(stops_loc) == 55

    @patch("main.optimize_route")
    def test_preserves_verification_metadata(
        self,
        mock_optimize_route,
        client: TestClient,
    ) -> None:
        start = {
            **_address(-73.8955, 40.8515, address1="Start"),
            "verification": {"is_verified": True, "verified_at": "2026-01-01T00:00:00Z"},
        }
        mock_optimize_route.return_value = {
            "ordered_indices": [0, 1, 2, 3],
            "total_distance_meters": 1000,
            "total_duration_seconds": 120,
        }

        response = client.post(
            "/optimize",
            json=_optimize_payload(
                start=start,
                end=self.END,
                stops=self.TWO_STOPS,
            ),
        )

        assert response.status_code == 200
        assert response.json()["addresses"][0]["verification"] == {
            "is_verified": True,
            "verified_at": "2026-01-01T00:00:00Z",
        }

    def test_returns_422_when_location_missing(self, client: TestClient) -> None:
        response = client.post(
            "/optimize",
            json=_optimize_payload(
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
    ) -> None:
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
            "total_duration_seconds": 120,
        }

        response = client.post(
            "/optimize",
            json=_optimize_payload(
                start=start,
                end=self.END,
                stops=self.TWO_STOPS,
            ),
        )

        assert response.status_code == 200
        addr = response.json()["addresses"][0]
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
            "/optimize",
            json={"addresses": [[-73.8955, 40.8515], [-73.90774, 40.88467]]},
        )
        assert response.status_code == 422
