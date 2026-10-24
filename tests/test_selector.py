from __future__ import annotations

import json
import unittest
from datetime import date

from station_selection import (
    InsufficientStationsError,
    OrchardLocation,
    SelectionConfig,
    Station,
    select_stations,
)


def make_station(
    station_id: str,
    *,
    dlat: float = 0.0,
    dlon: float = 0.0,
    elevation_m: float = 100.0,
    **kwargs: object,
) -> Station:
    values: dict[str, object] = {
        "connect_date": date(2000, 1, 1),
        "is_active": True,
    }
    values.update(kwargs)
    return Station(
        station_id=station_id,
        name=f"Station {station_id}",
        latitude=36.0 + dlat,
        longitude=-120.0 + dlon,
        elevation_m=elevation_m,
        **values,
    )


class SelectionTest(unittest.TestCase):
    location = OrchardLocation(36.0, -120.0, 100.0, "Test orchard")
    as_of = date(2026, 1, 1)

    def select(self, stations, config=None):
        return select_stations(
            self.location,
            stations,
            config or SelectionConfig(),
            as_of=self.as_of,
        )

    def test_selects_three_nearest_when_elevation_matches(self) -> None:
        stations = [
            make_station("1", dlat=0.04),
            make_station("2", dlat=0.13),
            make_station("3", dlat=0.21),
            make_station("4", dlat=0.40),
        ]
        result = self.select(
            stations,
            SelectionConfig(station_count=3, alternate_count=1),
        )
        self.assertEqual(result.station_ids, ["1", "2", "3"])
        self.assertEqual([match.station.station_id for match in result.alternates], ["4"])
        self.assertTrue(all(match.quality == "preferred" for match in result.selected))
        spread = result.diagnostics["selected_spread"]
        self.assertGreater(spread["max_pairwise_distance_km"], 0)
        self.assertEqual(spread["elevation_range_m"], 0.0)

    def test_elevation_difference_penalizes_station(self) -> None:
        stations = [
            make_station("close-match", dlat=0.20, elevation_m=100),
            make_station("high", dlon=0.20, elevation_m=300),
        ]
        result = self.select(stations, SelectionConfig(station_count=1))
        self.assertEqual(result.station_ids, ["close-match"])

    def test_elevation_inside_tolerance_is_equivalent(self) -> None:
        # 80 m difference is inside the default 100 m tolerance.
        stations = [
            make_station("tolerant", dlat=0.10, elevation_m=180),
            make_station("exact", dlat=0.15, elevation_m=100),
        ]
        result = self.select(
            stations,
            SelectionConfig(station_count=1),
        )
        self.assertEqual(result.station_ids, ["tolerant"])

    def test_station_outside_history_window_is_rejected(self) -> None:
        stations = [
            make_station("current", dlat=0.05),
            make_station(
                "stale",
                dlat=0.10,
                disconnect_date=date(2020, 1, 1),
            ),
            make_station(
                "recent",
                dlat=0.15,
                connect_date=date(2024, 6, 1),
            ),
        ]
        result = self.select(
            stations,
            SelectionConfig(station_count=3, alternate_count=0),
        )
        self.assertEqual(result.station_ids, ["current", "recent"])
        self.assertIn("no_history_overlap", result.diagnostics["rejected"])
        recent = result.selected[1]
        self.assertLess(recent.history_coverage, 1.0)
        self.assertTrue(any("covers" in warning for warning in recent.warnings))

    def test_active_station_is_preferred_over_nearby_inactive_station(self) -> None:
        stations = [
            make_station("inactive-near", dlat=0.02, is_active=False),
            make_station("active-mid", dlat=0.30),
            make_station("active-far", dlat=0.50),
        ]
        result = self.select(
            stations,
            SelectionConfig(station_count=2, alternate_count=1),
        )
        self.assertEqual(result.station_ids, ["active-mid", "active-far"])
        self.assertEqual(
            [match.station.station_id for match in result.alternates],
            ["inactive-near"],
        )
        self.assertIs(result.selected[0].station.is_active, True)

    def test_prefer_active_can_be_disabled(self) -> None:
        stations = [
            make_station("inactive-near", dlat=0.02, is_active=False),
            make_station("active-far", dlat=0.50),
        ]
        result = self.select(
            stations,
            SelectionConfig(station_count=1, prefer_active=False),
        )
        self.assertEqual(result.station_ids, ["inactive-near"])

    def test_inactive_can_be_excluded_entirely(self) -> None:
        stations = [
            make_station("inactive-near", dlat=0.02, is_active=False),
            make_station("active-far", dlat=0.50),
        ]
        result = self.select(
            stations,
            SelectionConfig(station_count=1, include_inactive=False),
        )
        self.assertEqual(result.station_ids, ["active-far"])
        self.assertIn("inactive", result.diagnostics["rejected"])

    def test_minimum_separation_avoids_duplicate_sensors(self) -> None:
        stations = [
            make_station("a", dlat=0.0),
            make_station("b", dlat=0.01),
            make_station("c", dlat=0.20),
        ]
        result = self.select(
            stations,
            SelectionConfig(station_count=2, min_station_separation_km=8.0),
        )
        self.assertEqual(len(result.selected), 2)
        self.assertFalse({"a", "b"}.issubset(set(result.station_ids)))
        self.assertTrue(any("Near-duplicate" in warning for warning in result.warnings))

    def test_separation_is_relaxed_when_necessary(self) -> None:
        stations = [
            make_station("a", dlat=0.0),
            make_station("b", dlat=0.01),
            make_station("c", dlat=0.20),
        ]
        result = self.select(
            stations,
            SelectionConfig(station_count=3, min_station_separation_km=8.0),
        )
        self.assertEqual(len(result.selected), 3)
        self.assertTrue(result.diagnostics["separation_relaxed"])
        self.assertTrue(any("relaxed" in warning for warning in result.warnings))

    def test_strict_mode_raises_with_result(self) -> None:
        stations = [make_station("only", dlat=0.05)]
        with self.assertRaises(InsufficientStationsError) as context:
            self.select(
                stations,
                SelectionConfig(station_count=3, fail_if_fewer_than=3),
            )
        self.assertEqual(context.exception.result.station_ids, ["only"])

    def test_empty_catalog(self) -> None:
        result = self.select([])
        self.assertEqual(result.station_ids, [])
        self.assertTrue(any("No station" in warning for warning in result.warnings))

    def test_unknown_history_coverage_is_not_treated_as_complete(self) -> None:
        station = make_station(
            "undated",
            dlat=0.05,
            connect_date=None,
            disconnect_date=None,
        )
        result = self.select([station], SelectionConfig(station_count=1))
        self.assertEqual(result.station_ids, ["undated"])
        self.assertIsNone(result.selected[0].history_coverage)
        self.assertTrue(
            any("coverage is unknown" in warning for warning in result.selected[0].warnings)
        )

    def test_data_requirements(self) -> None:
        stations = [make_station("daily-only", dlat=0.05, has_hourly=False)]
        result = self.select(
            stations,
            SelectionConfig(station_count=1, require_hourly=True),
        )
        self.assertEqual(result.station_ids, [])
        self.assertIn("missing_hourly", result.diagnostics["rejected"])

    def test_distance_cap(self) -> None:
        stations = [make_station("too-far", dlat=1.0)]
        result = self.select([*stations], SelectionConfig(station_count=1))
        self.assertEqual(result.station_ids, [])
        self.assertIn("distance_cap", result.diagnostics["rejected"])

    def test_json_serialization_and_determinism(self) -> None:
        stations = [
            make_station("1", dlat=0.05),
            make_station("2", dlat=0.20),
            make_station("3", dlat=0.30),
        ]
        first = self.select(stations)
        second = self.select(stations)

        payload = json.loads(first.to_json())
        self.assertEqual(payload["selected_station_ids"], ["1", "2", "3"])
        self.assertEqual(payload["module"], "station_selection")
        self.assertEqual(payload["schema_version"], "1.0")

        first_dict = first.to_dict()
        second_dict = second.to_dict()
        first_dict.pop("generated_at")
        second_dict.pop("generated_at")
        self.assertEqual(first_dict, second_dict)

    def test_location_is_echoed_in_output(self) -> None:
        result = self.select([make_station("1")])
        payload = result.to_dict()
        self.assertEqual(payload["location"]["name"], "Test orchard")
        self.assertEqual(payload["history_window"]["years"], 3)
        self.assertEqual(payload["history_window"]["end"], "2026-01-01")


if __name__ == "__main__":
    unittest.main()
