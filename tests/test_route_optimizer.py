from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from route_optimizer import (
    Location,
    _load_env,
    _load_input,
    _ors_distance_to_int,
    _parse_coordinate,
    _resolve_api_key,
    build_distance_matrix_ors,
    compute_vrp_time_limit,
    main,
    optimize_balanced_multi_route,
    optimize_route,
)


class TestLocationFromValue:
    def test_from_lat_lng_list(self) -> None:
        loc = Location.from_value([40.85, -73.89])
        assert loc.lat == 40.85
        assert loc.lng == -73.89
        assert loc.street_address_1 == ""

    def test_from_lat_lng_tuple(self) -> None:
        loc = Location.from_value((40.85, -73.89))
        assert loc.lat == 40.85
        assert loc.lng == -73.89

    def test_from_dict_minimal(self) -> None:
        loc = Location.from_value({"lat": 40.85, "lng": -73.89})
        assert loc.lat == 40.85
        assert loc.lng == -73.89

    def test_from_dict_full(self) -> None:
        loc = Location.from_value(
            {
                "lat": 40.85,
                "lng": -73.89,
                "street_address_1": "123 Main St",
                "city": "Bronx",
                "state": "NY",
                "zip": "10451",
            }
        )
        assert loc.street_address_1 == "123 Main St"
        assert loc.city == "Bronx"
        assert loc.state == "NY"
        assert loc.zip == "10451"

    def test_rejects_dict_missing_lat(self) -> None:
        with pytest.raises(ValueError, match="must include 'lat' and 'lng'"):
            Location.from_value({"lng": -73.89})

    def test_rejects_dict_missing_lng(self) -> None:
        with pytest.raises(ValueError, match="must include 'lat' and 'lng'"):
            Location.from_value({"lat": 40.85})

    def test_rejects_invalid_sequence(self) -> None:
        with pytest.raises(ValueError, match="Expected \\[lat, lng\\]"):
            Location.from_value([40.85])

    def test_rejects_non_sequence(self) -> None:
        with pytest.raises(ValueError, match="Expected \\[lat, lng\\]"):
            Location.from_value("40.85,-73.89")


class TestLocationToOrs:
    def test_returns_longitude_latitude_order(self) -> None:
        loc = Location(lat=40.85, lng=-73.89)
        assert loc.to_ors() == [-73.89, 40.85]


class TestLocationLabel:
    def test_full_address(self) -> None:
        loc = Location(
            lat=40.85,
            lng=-73.89,
            street_address_1="123 Main St",
            city="Bronx",
            state="NY",
            zip="10451",
        )
        assert loc.label == "123 Main St, Bronx, NY 10451"

    def test_street_only(self) -> None:
        loc = Location(lat=40.85, lng=-73.89, street_address_1="123 Main St")
        assert loc.label == "123 Main St"

    def test_city_state_zip_only(self) -> None:
        loc = Location(lat=40.85, lng=-73.89, city="Bronx", state="NY", zip="10451")
        assert loc.label == "Bronx, NY 10451"

    def test_falls_back_to_coordinates(self) -> None:
        loc = Location(lat=40.85, lng=-73.89)
        assert loc.label == "40.85, -73.89"

    def test_strips_whitespace(self) -> None:
        loc = Location(
            lat=40.85,
            lng=-73.89,
            street_address_1="  123 Main St  ",
            city="  Bronx  ",
        )
        assert loc.label == "123 Main St, Bronx"


class TestLocationToDict:
    def test_includes_all_fields_and_label(self) -> None:
        loc = Location(
            lat=40.85,
            lng=-73.89,
            street_address_1="123 Main St",
            city="Bronx",
            state="NY",
            zip="10451",
        )
        assert loc.to_dict() == {
            "lat": 40.85,
            "lng": -73.89,
            "street_address_1": "123 Main St",
            "city": "Bronx",
            "state": "NY",
            "zip": "10451",
            "label": "123 Main St, Bronx, NY 10451",
        }


class TestOrsDistanceToInt:
    def test_same_index_returns_zero(self) -> None:
        assert _ors_distance_to_int(999.0, 0, 0) == 0
        assert _ors_distance_to_int(None, 2, 2) == 0

    def test_rounds_distance(self) -> None:
        assert _ors_distance_to_int(1234.6, 0, 1) == 1235
        assert _ors_distance_to_int(1234.4, 0, 1) == 1234

    def test_none_raises_runtime_error(self) -> None:
        with pytest.raises(RuntimeError, match="No route found between location 0 and 1"):
            _ors_distance_to_int(None, 0, 1)


class TestBuildDistanceMatrixOrs:
    def test_empty_locations_returns_empty_matrix(self) -> None:
        assert build_distance_matrix_ors([], api_key="key") == []

    def test_rejects_chunk_size_too_large(self) -> None:
        locations = [Location(lat=40.0 + i * 0.01, lng=-73.0) for i in range(3)]
        with pytest.raises(ValueError, match="chunk_size must be"):
            build_distance_matrix_ors(locations, api_key="key", chunk_size=60)

    @patch("route_optimizer.openrouteservice.Client")
    def test_builds_matrix_from_ors_response(self, mock_client_cls: MagicMock) -> None:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.distance_matrix.return_value = {
            "distances": [
                [0.0, 1000.4, 2000.6],
                [1100.0, 0.0, 500.0],
                [2100.0, 600.0, 0.0],
            ]
        }

        locations = [
            Location(lat=40.0, lng=-73.0),
            Location(lat=40.1, lng=-73.1),
            Location(lat=40.2, lng=-73.2),
        ]
        matrix = build_distance_matrix_ors(locations, api_key="test-key", chunk_size=10)

        assert matrix == [
            [0, 1000, 2001],
            [1100, 0, 500],
            [2100, 600, 0],
        ]
        mock_client_cls.assert_called_once_with(key="test-key")
        mock_client.distance_matrix.assert_called_once()
        call_kwargs = mock_client.distance_matrix.call_args.kwargs
        assert call_kwargs["locations"] == [
            [-73.0, 40.0],
            [-73.1, 40.1],
            [-73.2, 40.2],
        ]
        assert call_kwargs["profile"] == "driving-car"
        assert call_kwargs["metrics"] == ["distance"]
        assert call_kwargs["units"] == "m"

    @patch("route_optimizer.openrouteservice.Client")
    def test_chunks_large_matrices(self, mock_client_cls: MagicMock) -> None:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        def fake_matrix(**kwargs: object) -> dict:
            sources = kwargs["sources"]
            destinations = kwargs["destinations"]
            rows = []
            for src in sources:
                rows.append([abs(src - dst) * 100 for dst in destinations])
            return {"distances": rows}

        mock_client.distance_matrix.side_effect = fake_matrix

        locations = [Location(lat=40.0, lng=-73.0 + i * 0.01) for i in range(4)]
        matrix = build_distance_matrix_ors(locations, api_key="key", chunk_size=2)

        assert len(matrix) == 4
        assert all(len(row) == 4 for row in matrix)
        assert matrix[0][0] == 0
        assert mock_client.distance_matrix.call_count == 4


class TestOptimizeRoute:
    START = Location(lat=40.0, lng=-73.0)
    STOP_A = Location(lat=40.1, lng=-73.1)
    STOP_B = Location(lat=40.2, lng=-73.2)
    END = Location(lat=40.3, lng=-73.3)

    @patch("route_optimizer.build_distance_matrix_ors")
    def test_start_to_end_only(self, mock_build_matrix: MagicMock) -> None:
        mock_build_matrix.return_value = [[0, 5000], [5000, 0]]

        result = optimize_route(
            self.START,
            [],
            self.END,
            api_key="test-key",
        )

        assert result["ordered_indices"] == [0, 1]
        assert result["stop_order"] == []
        assert result["total_distance_meters"] == 5000
        assert result["distance_source"] == "openrouteservice"
        assert result["profile"] == "driving-car"
        assert result["ordered_coordinates"] == [
            [40.0, -73.0],
            [40.3, -73.3],
        ]
        assert len(result["ordered_locations"]) == 2

    @patch("route_optimizer.build_distance_matrix_ors")
    def test_reorders_stops_by_shortest_path(self, mock_build_matrix: MagicMock) -> None:
        # Indices: 0=start, 1=stop_a, 2=stop_b, 3=end
        mock_build_matrix.return_value = [
            [0, 100, 500, 200],
            [100, 0, 150, 300],
            [500, 150, 0, 100],
            [200, 300, 100, 0],
        ]

        result = optimize_route(
            self.START,
            [self.STOP_A, self.STOP_B],
            self.END,
            api_key="test-key",
            time_limit_seconds=5,
        )

        assert result["ordered_indices"] == [0, 1, 2, 3]
        assert result["stop_order"] == [0, 1]
        assert result["total_distance_meters"] > 0
        assert len(result["ordered_locations"]) == 4

    @patch("route_optimizer.build_distance_matrix_ors")
    def test_distance_callback_uses_matrix(
        self, mock_build_matrix: MagicMock
    ) -> None:
        matrix = [
            [0, 100, 500, 200],
            [100, 0, 150, 300],
            [500, 150, 0, 100],
            [200, 300, 100, 0],
        ]
        mock_build_matrix.return_value = matrix

        result = optimize_route(
            self.START,
            [self.STOP_A, self.STOP_B],
            self.END,
            api_key="test-key",
        )

        # PATH_CHEAPEST_ARC on this matrix: 0->1 (100) + 1->2 (150) + 2->3 (100)
        assert result["total_distance_meters"] == 350


class TestComputeVrpTimeLimit:
    def test_scales_with_stops_and_routes(self) -> None:
        assert compute_vrp_time_limit(10, 2) == 45
        assert compute_vrp_time_limit(100, 10) == 215

    def test_respects_minimum(self) -> None:
        assert compute_vrp_time_limit(0, 1) == 30

    def test_respects_maximum(self) -> None:
        assert compute_vrp_time_limit(1000, 100) == 300


class TestOptimizeBalancedMultiRoute:
    DEPOT = Location(lat=40.0, lng=-73.0)
    STOP_A = Location(lat=40.1, lng=-73.1)
    STOP_B = Location(lat=40.2, lng=-73.2)
    STOP_C = Location(lat=40.15, lng=-73.15)

    @patch("route_optimizer.build_distance_matrix_ors")
    def test_splits_stops_across_routes(
        self, mock_build_matrix: MagicMock
    ) -> None:
        # depot=0, stop_a=1, stop_b=2, stop_c=3
        mock_build_matrix.return_value = [
            [0, 100, 200, 150],
            [100, 0, 100, 50],
            [200, 100, 0, 80],
            [150, 50, 80, 0],
        ]

        result = optimize_balanced_multi_route(
            self.DEPOT,
            [self.STOP_A, self.STOP_B, self.STOP_C],
            2,
            api_key="test-key",
            time_limit_seconds=30,
        )

        assert result["num_routes"] == 2
        assert result["split_mode"] == "balanced_distance"
        assert len(result["routes"]) == 2
        assert result["total_distance_meters"] > 0
        all_stops = []
        for route in result["routes"]:
            assert route["route_number"] in (1, 2)
            assert route["distance_meters"] > 0
            all_stops.extend(route["stop_order"])
        assert sorted(all_stops) == [0, 1, 2]

    def test_rejects_more_routes_than_stops(self) -> None:
        with pytest.raises(ValueError, match="Cannot create 3 routes"):
            optimize_balanced_multi_route(
                self.DEPOT,
                [self.STOP_A],
                3,
                api_key="test-key",
            )

    def test_rejects_empty_stops(self) -> None:
        with pytest.raises(ValueError, match="At least one stop"):
            optimize_balanced_multi_route(
                self.DEPOT,
                [],
                1,
                api_key="test-key",
            )


class TestParseCoordinate:
    def test_parses_valid_coordinate(self) -> None:
        loc = _parse_coordinate("40.85,-73.89")
        assert loc == Location(lat=40.85, lng=-73.89)

    def test_parses_with_spaces(self) -> None:
        loc = _parse_coordinate(" 40.85 , -73.89 ")
        assert loc == Location(lat=40.85, lng=-73.89)

    def test_rejects_wrong_number_of_parts(self) -> None:
        with pytest.raises(argparse.ArgumentTypeError, match="Invalid coordinate"):
            _parse_coordinate("40.85")

    def test_rejects_non_numeric_values(self) -> None:
        with pytest.raises(argparse.ArgumentTypeError, match="must be numbers"):
            _parse_coordinate("abc,def")


class TestLoadInput:
    def test_loads_valid_json(self, tmp_path: Path) -> None:
        data = {
            "start": [40.0, -73.0],
            "end": [40.3, -73.3],
            "stops": [[40.1, -73.1]],
        }
        path = tmp_path / "route.json"
        path.write_text(json.dumps(data), encoding="utf-8")

        loaded = _load_input(str(path))
        assert loaded == data

    def test_raises_when_key_missing(self, tmp_path: Path) -> None:
        path = tmp_path / "route.json"
        path.write_text(json.dumps({"start": [40.0, -73.0]}), encoding="utf-8")

        with pytest.raises(ValueError, match="Input JSON must include 'end'"):
            _load_input(str(path))


class TestLoadEnv:
    @patch("route_optimizer.load_dotenv")
    def test_loads_dotenv_from_project_directory(self, mock_load_dotenv: MagicMock) -> None:
        _load_env()
        mock_load_dotenv.assert_called_once()
        env_path = mock_load_dotenv.call_args.args[0]
        assert env_path.name == ".env"
        assert env_path.parent.name == "route-optimizer"


class TestResolveApiKey:
    def test_returns_explicit_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ORS_API_KEY", raising=False)
        assert _resolve_api_key("explicit-key") == "explicit-key"

    def test_returns_env_var_when_no_explicit_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ORS_API_KEY", "env-key")
        assert _resolve_api_key(None) == "env-key"

    def test_explicit_key_overrides_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ORS_API_KEY", "env-key")
        assert _resolve_api_key("explicit-key") == "explicit-key"

    def test_exits_when_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ORS_API_KEY", raising=False)
        with pytest.raises(SystemExit, match="OpenRouteService API key required"):
            _resolve_api_key(None)


class TestMain:
    @patch("route_optimizer.optimize_route")
    @patch("route_optimizer._load_env")
    def test_cli_with_coordinates(
        self,
        mock_load_env: MagicMock,
        mock_optimize: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("ORS_API_KEY", "test-key")
        mock_optimize.return_value = {
            "ordered_locations": [],
            "ordered_coordinates": [],
            "ordered_indices": [0, 1, 2],
            "stop_order": [0],
            "total_distance_meters": 1000,
            "distance_source": "openrouteservice",
            "profile": "driving-car",
        }

        exit_code = main(
            [
                "--start",
                "40.0,-73.0",
                "--end",
                "40.3,-73.3",
                "--stop",
                "40.1,-73.1",
            ]
        )

        assert exit_code == 0
        mock_load_env.assert_called_once()
        mock_optimize.assert_called_once()
        start, stops, end = mock_optimize.call_args.args[:3]
        assert mock_optimize.call_args.kwargs["api_key"] == "test-key"
        assert start == Location(lat=40.0, lng=-73.0)
        assert end == Location(lat=40.3, lng=-73.3)
        assert stops == [Location(lat=40.1, lng=-73.1)]

        output = json.loads(capsys.readouterr().out)
        assert output["total_distance_meters"] == 1000

    @patch("route_optimizer.optimize_route")
    @patch("route_optimizer._load_env")
    def test_cli_with_input_file(
        self,
        mock_load_env: MagicMock,
        mock_optimize: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.setenv("ORS_API_KEY", "test-key")
        mock_optimize.return_value = {
            "ordered_locations": [],
            "ordered_coordinates": [],
            "ordered_indices": [0, 1],
            "stop_order": [],
            "total_distance_meters": 500,
            "distance_source": "openrouteservice",
            "profile": "driving-car",
        }

        input_path = tmp_path / "route.json"
        input_path.write_text(
            json.dumps(
                {
                    "start": [40.0, -73.0],
                    "end": [40.3, -73.3],
                    "stops": [],
                    "profile": "cycling-regular",
                    "time_limit_seconds": 10,
                }
            ),
            encoding="utf-8",
        )

        exit_code = main(["--input", str(input_path)])

        assert exit_code == 0
        call_kwargs = mock_optimize.call_args.kwargs
        assert call_kwargs["profile"] == "cycling-regular"
        assert call_kwargs["time_limit_seconds"] == 10

    def test_cli_errors_without_input_or_coordinates(self) -> None:
        with pytest.raises(SystemExit):
            main([])

    @patch("route_optimizer._load_env")
    def test_cli_exits_without_api_key(
        self, mock_load_env: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ORS_API_KEY", raising=False)
        with pytest.raises(SystemExit, match="OpenRouteService API key required"):
            main(
                [
                    "--start",
                    "40.0,-73.0",
                    "--end",
                    "40.3,-73.3",
                    "--stop",
                    "40.1,-73.1",
                ]
            )


# --- Second batch: additional edge cases and invalid inputs ---


class TestLocationFromValueEdgeCases:
    def test_rejects_empty_list(self) -> None:
        with pytest.raises(ValueError, match="Expected \\[lat, lng\\]"):
            Location.from_value([])

    def test_rejects_empty_dict(self) -> None:
        with pytest.raises(ValueError, match="must include 'lat' and 'lng'"):
            Location.from_value({})

    def test_rejects_three_element_list(self) -> None:
        with pytest.raises(ValueError, match="Expected \\[lat, lng\\]"):
            Location.from_value([40.0, -73.0, 0.0])

    def test_rejects_none(self) -> None:
        with pytest.raises(ValueError, match="Expected \\[lat, lng\\]"):
            Location.from_value(None)

    def test_rejects_non_numeric_lat_in_dict(self) -> None:
        with pytest.raises(ValueError):
            Location.from_value({"lat": "north", "lng": -73.0})

    def test_rejects_non_numeric_lng_in_list(self) -> None:
        with pytest.raises(ValueError):
            Location.from_value(["forty", -73.0])

    def test_coerces_string_numbers_in_dict(self) -> None:
        loc = Location.from_value({"lat": "40.85", "lng": "-73.89"})
        assert loc.lat == 40.85
        assert loc.lng == -73.89


class TestLocationLabelEdgeCases:
    def test_state_only(self) -> None:
        loc = Location(lat=40.85, lng=-73.89, state="NY")
        assert loc.label == "NY"

    def test_zip_only(self) -> None:
        loc = Location(lat=40.85, lng=-73.89, zip="10451")
        assert loc.label == "10451"

    def test_all_whitespace_address_falls_back_to_coordinates(self) -> None:
        loc = Location(
            lat=40.85,
            lng=-73.89,
            street_address_1="   ",
            city="  ",
            state="",
            zip="\t",
        )
        assert loc.label == "40.85, -73.89"


class TestLocationToOrsAndToDictEdgeCases:
    def test_negative_coordinates(self) -> None:
        loc = Location(lat=-33.87, lng=-151.21)
        assert loc.to_ors() == [-151.21, -33.87]

    def test_zero_coordinates(self) -> None:
        loc = Location(lat=0.0, lng=0.0)
        assert loc.to_ors() == [0.0, 0.0]
        assert loc.label == "0.0, 0.0"

    def test_to_dict_round_trip_preserves_fields(self) -> None:
        loc = Location(lat=40.85, lng=-73.89, city="Bronx")
        d = loc.to_dict()
        restored = Location.from_value(d)
        assert restored.lat == loc.lat
        assert restored.lng == loc.lng
        assert restored.city == loc.city
        assert d["label"] == loc.label


class TestBuildDistanceMatrixOrsEdgeCases:
    @patch("route_optimizer.openrouteservice.Client")
    def test_single_location_matrix(self, mock_client_cls: MagicMock) -> None:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.distance_matrix.return_value = {"distances": [[0.0]]}

        matrix = build_distance_matrix_ors(
            [Location(lat=40.0, lng=-73.0)], api_key="key"
        )

        assert matrix == [[0]]
        mock_client.distance_matrix.assert_called_once()

    @patch("route_optimizer.openrouteservice.Client")
    def test_ors_none_distance_raises(self, mock_client_cls: MagicMock) -> None:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.distance_matrix.return_value = {
            "distances": [[0.0, None], [100.0, 0.0]]
        }

        locations = [
            Location(lat=40.0, lng=-73.0),
            Location(lat=40.1, lng=-73.1),
        ]
        with pytest.raises(RuntimeError, match="No route found between location 0 and 1"):
            build_distance_matrix_ors(locations, api_key="key")


class TestOptimizeRouteEdgeCases:
    START = Location(lat=40.0, lng=-73.0)
    STOP_A = Location(lat=40.1, lng=-73.1)
    STOP_B = Location(lat=40.2, lng=-73.2)
    END = Location(lat=40.3, lng=-73.3)

    @patch("route_optimizer.build_distance_matrix_ors")
    def test_reorders_stops_when_input_order_is_suboptimal(
        self, mock_build_matrix: MagicMock
    ) -> None:
        # Cheapest path is start -> stop_b -> stop_a -> end (indices 0, 2, 1, 3)
        mock_build_matrix.return_value = [
            [0, 500, 50, 200],
            [500, 0, 50, 200],
            [50, 50, 0, 50],
            [200, 200, 50, 0],
        ]

        result = optimize_route(
            self.START,
            [self.STOP_A, self.STOP_B],
            self.END,
            api_key="test-key",
        )

        assert result["ordered_indices"] == [0, 2, 1, 3]
        assert result["stop_order"] == [1, 0]
        assert result["total_distance_meters"] == 300

    @patch("route_optimizer.build_distance_matrix_ors")
    @patch("route_optimizer.pywrapcp.RoutingModel")
    @patch("route_optimizer.pywrapcp.RoutingIndexManager")
    def test_raises_when_solver_finds_no_solution(
        self,
        mock_manager_cls: MagicMock,
        mock_routing_model_cls: MagicMock,
        mock_build_matrix: MagicMock,
    ) -> None:
        mock_build_matrix.return_value = [
            [0, 100, 100, 100],
            [100, 0, 100, 100],
            [100, 100, 0, 100],
            [100, 100, 100, 0],
        ]
        mock_routing = MagicMock()
        mock_routing.SolveWithParameters.return_value = None
        mock_routing_model_cls.return_value = mock_routing

        with pytest.raises(RuntimeError, match="No solution found"):
            optimize_route(
                self.START,
                [self.STOP_A],
                self.END,
                api_key="test-key",
            )


class TestParseCoordinateEdgeCases:
    def test_rejects_empty_string(self) -> None:
        with pytest.raises(argparse.ArgumentTypeError, match="Invalid coordinate"):
            _parse_coordinate("")

    def test_rejects_three_parts(self) -> None:
        with pytest.raises(argparse.ArgumentTypeError, match="Invalid coordinate"):
            _parse_coordinate("40.0,-73.0,0.0")

    def test_rejects_whitespace_only_values(self) -> None:
        with pytest.raises(argparse.ArgumentTypeError, match="must be numbers"):
            _parse_coordinate(" , ")

    def test_rejects_extra_commas(self) -> None:
        with pytest.raises(argparse.ArgumentTypeError, match="Invalid coordinate"):
            _parse_coordinate("40.0,-73.0,extra")


class TestLoadInputEdgeCases:
    def test_raises_when_start_missing(self, tmp_path: Path) -> None:
        path = tmp_path / "route.json"
        path.write_text(
            json.dumps({"end": [40.3, -73.3], "stops": []}),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="Input JSON must include 'start'"):
            _load_input(str(path))

    def test_raises_when_stops_missing(self, tmp_path: Path) -> None:
        path = tmp_path / "route.json"
        path.write_text(
            json.dumps({"start": [40.0, -73.0], "end": [40.3, -73.3]}),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="Input JSON must include 'stops'"):
            _load_input(str(path))

    def test_raises_on_malformed_json(self, tmp_path: Path) -> None:
        path = tmp_path / "route.json"
        path.write_text("{not valid json", encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            _load_input(str(path))

    def test_raises_when_file_not_found(self) -> None:
        with pytest.raises(FileNotFoundError):
            _load_input("/nonexistent/path/route.json")


class TestResolveApiKeyEdgeCases:
    def test_empty_explicit_key_falls_through_to_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ORS_API_KEY", "env-key")
        assert _resolve_api_key("") == "env-key"

    def test_empty_env_var_exits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ORS_API_KEY", "")
        with pytest.raises(SystemExit, match="OpenRouteService API key required"):
            _resolve_api_key(None)

    def test_empty_explicit_and_empty_env_exits(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ORS_API_KEY", "")
        with pytest.raises(SystemExit, match="OpenRouteService API key required"):
            _resolve_api_key("")


class TestMainEdgeCases:
    def test_errors_with_start_only(self) -> None:
        with pytest.raises(SystemExit):
            main(["--start", "40.0,-73.0"])

    def test_errors_with_start_and_end_but_no_stops(self) -> None:
        with pytest.raises(SystemExit):
            main(["--start", "40.0,-73.0", "--end", "40.3,-73.3"])

    @patch("route_optimizer._load_env")
    def test_raises_when_input_file_missing(
        self, mock_load_env: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ORS_API_KEY", "test-key")
        with pytest.raises(FileNotFoundError):
            main(["--input", "/nonexistent/route.json"])

    @patch("route_optimizer._load_env")
    def test_raises_when_input_file_invalid_json(
        self, mock_load_env: MagicMock, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("ORS_API_KEY", "test-key")
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("not json", encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            main(["--input", str(bad_file)])

    @patch("route_optimizer._load_env")
    def test_raises_when_input_has_invalid_location(
        self, mock_load_env: MagicMock, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("ORS_API_KEY", "test-key")
        path = tmp_path / "route.json"
        path.write_text(
            json.dumps(
                {
                    "start": [40.0, -73.0],
                    "end": [40.3, -73.3],
                    "stops": ["not-a-location"],
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="Expected \\[lat, lng\\]"):
            main(["--input", str(path)])
