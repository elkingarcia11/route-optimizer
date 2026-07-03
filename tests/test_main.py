from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from main import _get_api_key, _location_from_lonlat, app
from route_optimizer import Location


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


class TestGetApiKey:
    def test_returns_key_when_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ORS_API_KEY", "test-api-key")
        assert _get_api_key() == "test-api-key"

    def test_raises_when_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ORS_API_KEY", raising=False)
        with pytest.raises(HTTPException) as exc_info:
            _get_api_key()
        assert exc_info.value.status_code == 500
        assert exc_info.value.detail == "ORS_API_KEY is not configured."

    def test_raises_when_empty_string(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ORS_API_KEY", "")
        with pytest.raises(HTTPException) as exc_info:
            _get_api_key()
        assert exc_info.value.status_code == 500
        assert exc_info.value.detail == "ORS_API_KEY is not configured."


class TestLocationFromLonLat:
    def test_converts_lon_lat_to_location(self) -> None:
        location = _location_from_lonlat([-73.8955, 40.8515])
        assert location == Location(lat=40.8515, lng=-73.8955)

    def test_rejects_invalid_length(self) -> None:
        with pytest.raises(ValueError, match="Each location must be \\[lon, lat\\]"):
            _location_from_lonlat([-73.8955])

    def test_rejects_empty_list(self) -> None:
        with pytest.raises(ValueError, match="Each location must be \\[lon, lat\\]"):
            _location_from_lonlat([])

    def test_rejects_three_coordinate_values(self) -> None:
        with pytest.raises(ValueError, match="Each location must be \\[lon, lat\\]"):
            _location_from_lonlat([-73.8955, 40.8515, 0.0])


class TestHealthEndpoint:
    def test_returns_ok(self, client: TestClient) -> None:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


class TestOptimizeEndpoint:
    LOCATIONS = [
        [-73.8955, 40.8515],
        [-73.91335, 40.87995],
        [-73.90774, 40.88467],
        [-73.8955, 40.8515],
    ]

    def test_returns_500_when_api_key_missing(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ORS_API_KEY", raising=False)
        response = client.post("/optimize", json={"locations": self.LOCATIONS})
        assert response.status_code == 500
        assert response.json()["detail"] == "ORS_API_KEY is not configured."

    def test_returns_422_for_invalid_location(self, client: TestClient) -> None:
        response = client.post(
            "/optimize",
            json={"locations": [[-73.8955, 40.8515], [-73.91335]]},
        )
        assert response.status_code == 422
        assert "Each location must be [lon, lat]" in response.json()["detail"]

    def test_returns_422_when_too_few_locations(self, client: TestClient) -> None:
        response = client.post(
            "/optimize",
            json={"locations": [[-73.8955, 40.8515]]},
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

        response = client.post("/optimize", json={"locations": self.LOCATIONS})

        assert response.status_code == 200
        assert response.json() == {
            "routeIndexes": [0, 2, 1, 3],
            "totalDistanceMeters": 12345,
            "orderedLocations": [
                self.LOCATIONS[0],
                self.LOCATIONS[2],
                self.LOCATIONS[1],
                self.LOCATIONS[3],
            ],
        }
        mock_optimize_route.assert_called_once()
        start, stops, end = mock_optimize_route.call_args.args[:3]
        assert mock_optimize_route.call_args.kwargs["api_key"] == "test-api-key"
        assert start == Location(lat=40.8515, lng=-73.8955)
        assert end == Location(lat=40.8515, lng=-73.8955)
        assert stops == [
            Location(lat=40.87995, lng=-73.91335),
            Location(lat=40.88467, lng=-73.90774),
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

        response = client.post("/optimize", json={"locations": self.LOCATIONS})

        assert response.status_code == 502
        assert response.json()["detail"] == "No solution found."

    def test_returns_500_when_api_key_empty_string(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ORS_API_KEY", "")
        response = client.post("/optimize", json={"locations": self.LOCATIONS})
        assert response.status_code == 500
        assert response.json()["detail"] == "ORS_API_KEY is not configured."

    def test_returns_422_when_locations_key_missing(self, client: TestClient) -> None:
        response = client.post("/optimize", json={})
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert any(err["loc"] == ["body", "locations"] for err in detail)

    def test_returns_422_when_locations_is_wrong_type(self, client: TestClient) -> None:
        response = client.post("/optimize", json={"locations": "not-a-list"})
        assert response.status_code == 422

    def test_returns_422_when_locations_is_empty(self, client: TestClient) -> None:
        response = client.post("/optimize", json={"locations": []})
        assert response.status_code == 422

    def test_returns_422_for_non_numeric_coordinates(self, client: TestClient) -> None:
        response = client.post(
            "/optimize",
            json={"locations": [["a", "b"], [-73.8955, 40.8515]]},
        )
        assert response.status_code == 422

    def test_returns_422_for_location_with_three_values(self, client: TestClient) -> None:
        response = client.post(
            "/optimize",
            json={
                "locations": [
                    [-73.8955, 40.8515],
                    [-73.91335, 40.87995, 0.0],
                    [-73.8955, 40.8515],
                ]
            },
        )
        assert response.status_code == 422
        assert "Each location must be [lon, lat]" in response.json()["detail"]

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
        locations = [[-73.8955, 40.8515], [-73.90774, 40.88467]]
        mock_optimize_route.return_value = {
            "ordered_indices": [0, 1],
            "total_distance_meters": 5000,
        }

        response = client.post("/optimize", json={"locations": locations})

        assert response.status_code == 200
        assert response.json() == {
            "routeIndexes": [0, 1],
            "totalDistanceMeters": 5000,
            "orderedLocations": locations,
        }
        start, stops, end = mock_optimize_route.call_args.args[:3]
        assert stops == []
        assert start == Location(lat=40.8515, lng=-73.8955)
        assert end == Location(lat=40.88467, lng=-73.90774)

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

        response = client.post("/optimize", json={"locations": self.LOCATIONS})

        assert response.status_code == 502
        assert response.json()["detail"] == "No route found between location 0 and 2."

    @patch("main.optimize_route")
    def test_accepts_large_location_list(
        self,
        mock_optimize_route,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("ORS_API_KEY", "test-api-key")
        # 1 start + 55 stops + 1 end = 57 locations (within ORS all-to-all limits)
        locations = [[-73.89 + i * 0.001, 40.85 + i * 0.001] for i in range(57)]
        ordered_indices = list(range(len(locations)))
        mock_optimize_route.return_value = {
            "ordered_indices": ordered_indices,
            "total_distance_meters": 999999,
        }

        response = client.post("/optimize", json={"locations": locations})

        assert response.status_code == 200
        assert len(response.json()["routeIndexes"]) == 57
        assert len(response.json()["orderedLocations"]) == 57
        start, stops, end = mock_optimize_route.call_args.args[:3]
        assert len(stops) == 55
        assert start.lat == locations[0][1]
        assert end.lat == locations[-1][1]
