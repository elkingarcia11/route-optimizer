from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from main import Address, GeoPoint, _get_api_key, _location_from_address, _resolve_api_key, app
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
    addresses: list[dict],
    *,
    api_key: str | None = "test-api-key",
) -> dict:
    payload: dict = {"addresses": addresses}
    if api_key is not None:
        payload["apiKey"] = api_key
    return payload


class TestResolveApiKey:
    def test_returns_explicit_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ORS_API_KEY", raising=False)
        assert _resolve_api_key("payload-key") == "payload-key"

    def test_returns_env_when_explicit_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ORS_API_KEY", "env-key")
        assert _resolve_api_key(None) == "env-key"
        assert _get_api_key() == "env-key"

    def test_explicit_key_overrides_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ORS_API_KEY", "env-key")
        assert _resolve_api_key("payload-key") == "payload-key"

    def test_raises_when_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ORS_API_KEY", raising=False)
        with pytest.raises(HTTPException) as exc_info:
            _resolve_api_key(None)
        assert exc_info.value.status_code == 422
        assert exc_info.value.detail == (
            "apiKey is required in the request body or via ORS_API_KEY."
        )

    def test_raises_when_empty_strings(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ORS_API_KEY", "")
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
        optimize_post = schema["paths"]["/optimize"]["post"]
        assert optimize_post["summary"] == "Optimize route order"
        assert "422" in optimize_post["responses"]
        assert "apiKey" in schema["components"]["schemas"]["OptimizeRequest"]["properties"]
        assert "OptimizeResponse" in schema["components"]["schemas"]


class TestOptimizeEndpoint:
    ADDRESSES = [
        _address(-73.8955, 40.8515, address1="Start", city="Bronx", state="NY"),
        _address(-73.91335, 40.87995, address1="Stop A", city="Bronx", state="NY"),
        _address(-73.90774, 40.88467, address1="Stop B", city="Bronx", state="NY"),
        _address(-73.8955, 40.8515, address1="End", city="Bronx", state="NY"),
    ]

    def test_returns_422_when_api_key_missing(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ORS_API_KEY", raising=False)
        response = client.post(
            "/optimize", json=_optimize_payload(self.ADDRESSES, api_key=None)
        )
        assert response.status_code == 422
        assert response.json()["detail"] == (
            "apiKey is required in the request body or via ORS_API_KEY."
        )

    def test_returns_422_for_invalid_location(self, client: TestClient) -> None:
        response = client.post(
            "/optimize",
            json=_optimize_payload(
                [
                    self.ADDRESSES[0],
                    {"location": {"type": "Point", "coordinates": [-73.91335]}},
                    self.ADDRESSES[-1],
                ]
            ),
        )
        assert response.status_code == 422

    def test_returns_422_when_too_few_addresses(self, client: TestClient) -> None:
        response = client.post(
            "/optimize",
            json=_optimize_payload([self.ADDRESSES[0]], api_key=None),
        )
        assert response.status_code == 422

    @patch("main.optimize_route")
    def test_returns_optimized_route(
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
            "/optimize",
            json=_optimize_payload(self.ADDRESSES, api_key="test-api-key"),
        )

        assert response.status_code == 200
        body = response.json()
        assert body["totalDistanceMeters"] == 12345
        assert [addr["routeOrder"] for addr in body["addresses"]] == [1, 2, 3, 4]
        assert body["addresses"][0]["address1"] == "Start"
        assert body["addresses"][1]["address1"] == "Stop B"
        assert body["addresses"][2]["address1"] == "Stop A"
        assert body["addresses"][3]["address1"] == "End"
        assert body["addresses"][0]["location"]["coordinates"] == [-73.8955, 40.8515]

        mock_optimize_route.assert_called_once()
        start, stops, end = mock_optimize_route.call_args.args[:3]
        assert mock_optimize_route.call_args.kwargs["api_key"] == "test-api-key"
        assert start == Location(
            lat=40.8515,
            lng=-73.8955,
            street_address_1="Start",
            city="Bronx",
            state="NY",
        )
        assert end == Location(
            lat=40.8515,
            lng=-73.8955,
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
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("ORS_API_KEY", "test-api-key")
        mock_optimize_route.side_effect = RuntimeError("No solution found.")

        response = client.post(
            "/optimize",
            json=_optimize_payload(self.ADDRESSES, api_key="test-api-key"),
        )

        assert response.status_code == 502
        assert response.json()["detail"] == "No solution found."

    def test_returns_422_when_api_key_empty_in_payload_and_env(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ORS_API_KEY", raising=False)
        response = client.post(
            "/optimize",
            json=_optimize_payload(self.ADDRESSES, api_key=None),
        )
        assert response.status_code == 422
        assert response.json()["detail"] == (
            "apiKey is required in the request body or via ORS_API_KEY."
        )

    @patch("main.optimize_route")
    def test_uses_api_key_from_payload_without_env(
        self,
        mock_optimize_route,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("ORS_API_KEY", raising=False)
        mock_optimize_route.return_value = {
            "ordered_indices": [0, 1],
            "total_distance_meters": 1000,
        }
        addresses = [
            _address(-73.8955, 40.8515, address1="Start"),
            _address(-73.90774, 40.88467, address1="End"),
        ]

        response = client.post(
            "/optimize",
            json=_optimize_payload(addresses, api_key="payload-only-key"),
        )

        assert response.status_code == 200
        assert mock_optimize_route.call_args.kwargs["api_key"] == "payload-only-key"

    @patch("main.optimize_route")
    def test_payload_api_key_overrides_env(
        self,
        mock_optimize_route,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("ORS_API_KEY", "env-key")
        mock_optimize_route.return_value = {
            "ordered_indices": [0, 1],
            "total_distance_meters": 1000,
        }
        addresses = [
            _address(-73.8955, 40.8515, address1="Start"),
            _address(-73.90774, 40.88467, address1="End"),
        ]

        response = client.post(
            "/optimize",
            json=_optimize_payload(addresses, api_key="payload-key"),
        )

        assert response.status_code == 200
        assert mock_optimize_route.call_args.kwargs["api_key"] == "payload-key"

    def test_returns_422_when_addresses_key_missing(self, client: TestClient) -> None:
        response = client.post("/optimize", json={})
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert any(err["loc"] == ["body", "addresses"] for err in detail)

    def test_returns_422_when_addresses_is_wrong_type(self, client: TestClient) -> None:
        response = client.post("/optimize", json={"addresses": "not-a-list"})
        assert response.status_code == 422

    def test_returns_422_when_addresses_is_empty(self, client: TestClient) -> None:
        response = client.post("/optimize", json={"addresses": []})
        assert response.status_code == 422

    def test_returns_422_for_non_numeric_coordinates(self, client: TestClient) -> None:
        response = client.post(
            "/optimize",
            json=_optimize_payload(
                [
                    {"location": {"type": "Point", "coordinates": ["a", "b"]}},
                    self.ADDRESSES[-1],
                ]
            ),
        )
        assert response.status_code == 422

    def test_returns_422_for_location_with_three_values(self, client: TestClient) -> None:
        response = client.post(
            "/optimize",
            json=_optimize_payload(
                [
                    self.ADDRESSES[0],
                    {
                        "location": {
                            "type": "Point",
                            "coordinates": [-73.91335, 40.87995, 0.0],
                        }
                    },
                    self.ADDRESSES[-1],
                ]
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
    def test_returns_200_for_start_and_end_only(
        self,
        mock_optimize_route,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("ORS_API_KEY", "test-api-key")
        addresses = [
            _address(-73.8955, 40.8515, address1="Start"),
            _address(-73.90774, 40.88467, address1="End"),
        ]
        mock_optimize_route.return_value = {
            "ordered_indices": [0, 1],
            "total_distance_meters": 5000,
        }

        response = client.post("/optimize", json=_optimize_payload(addresses))

        assert response.status_code == 200
        body = response.json()
        assert body["totalDistanceMeters"] == 5000
        assert [addr["routeOrder"] for addr in body["addresses"]] == [1, 2]
        assert body["addresses"][0]["address1"] == "Start"
        assert body["addresses"][1]["address1"] == "End"
        start, stops, end = mock_optimize_route.call_args.args[:3]
        assert stops == []
        assert start == Location(lat=40.8515, lng=-73.8955, street_address_1="Start")
        assert end == Location(lat=40.88467, lng=-73.90774, street_address_1="End")

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
            "/optimize",
            json=_optimize_payload(self.ADDRESSES, api_key="test-api-key"),
        )

        assert response.status_code == 502
        assert response.json()["detail"] == "No route found between location 0 and 2."

    @patch("main.optimize_route")
    def test_accepts_large_address_list(
        self,
        mock_optimize_route,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("ORS_API_KEY", "test-api-key")
        addresses = [
            _address(-73.89 + i * 0.001, 40.85 + i * 0.001, address1=f"Addr {i}")
            for i in range(57)
        ]
        ordered_indices = list(range(len(addresses)))
        mock_optimize_route.return_value = {
            "ordered_indices": ordered_indices,
            "total_distance_meters": 999999,
        }

        response = client.post("/optimize", json=_optimize_payload(addresses))

        assert response.status_code == 200
        body = response.json()
        assert len(body["addresses"]) == 57
        assert body["addresses"][0]["routeOrder"] == 1
        assert body["addresses"][-1]["routeOrder"] == 57
        start, stops, end = mock_optimize_route.call_args.args[:3]
        assert len(stops) == 55
        assert start.lat == addresses[0]["location"]["coordinates"][1]
        assert end.lat == addresses[-1]["location"]["coordinates"][1]

    @patch("main.optimize_route")
    def test_preserves_verification_metadata(
        self,
        mock_optimize_route,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("ORS_API_KEY", "test-api-key")
        addresses = [
            {
                **_address(-73.8955, 40.8515, address1="Start"),
                "verification": {"is_verified": True, "verified_at": "2026-01-01T00:00:00Z"},
            },
            _address(-73.90774, 40.88467, address1="End"),
        ]
        mock_optimize_route.return_value = {
            "ordered_indices": [0, 1],
            "total_distance_meters": 1000,
        }

        response = client.post("/optimize", json=_optimize_payload(addresses))

        assert response.status_code == 200
        assert response.json()["addresses"][0]["verification"] == {
            "is_verified": True,
            "verified_at": "2026-01-01T00:00:00Z",
        }

    def test_returns_422_when_location_missing(self, client: TestClient) -> None:
        response = client.post(
            "/optimize",
            json=_optimize_payload(
                [
                    {"address1": "Start", "city": "Bronx"},
                    self.ADDRESSES[-1],
                ]
            ),
        )
        assert response.status_code == 422

    def test_returns_422_for_legacy_locations_payload(self, client: TestClient) -> None:
        response = client.post(
            "/optimize",
            json={"locations": [[-73.8955, 40.8515], [-73.90774, 40.88467]]},
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
        addresses = [
            _address(
                -73.8955,
                40.8515,
                address1="123 Main St",
                address2="Floor 2",
                apartment="Unit 5",
                city="Bronx",
                state="NY",
                country="US",
                zipcode="10451",
            ),
            _address(-73.90774, 40.88467, address1="End"),
        ]
        mock_optimize_route.return_value = {
            "ordered_indices": [0, 1],
            "total_distance_meters": 1000,
        }

        response = client.post("/optimize", json=_optimize_payload(addresses))

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
