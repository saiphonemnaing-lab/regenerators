from __future__ import annotations

import unittest
from datetime import date

from station_selection import (
    OrchardLocation,
    SelectionConfig,
    Station,
    StationMatch,
)


class ModelTest(unittest.TestCase):
    def test_orchard_location_validation(self) -> None:
        with self.assertRaises(ValueError):
            OrchardLocation(latitude=91, longitude=0, elevation_m=10)
        with self.assertRaises(ValueError):
            OrchardLocation(latitude=0, longitude=181, elevation_m=10)
        with self.assertRaises(ValueError):
            OrchardLocation(latitude=0, longitude=0, elevation_m=float("nan"))

    def test_orchard_from_feet(self) -> None:
        location = OrchardLocation.from_feet(39.0, -121.0, 100.0, name="  Demo  ")
        self.assertAlmostEqual(location.elevation_m, 30.48, places=6)
        self.assertAlmostEqual(location.elevation_ft, 100.0, places=6)
        self.assertEqual(location.name, "Demo")

    def test_station_validation(self) -> None:
        with self.assertRaises(ValueError):
            Station(
                station_id="",
                name="x",
                latitude=0,
                longitude=0,
                elevation_m=0,
            )
        with self.assertRaises(ValueError):
            Station(
                station_id="1",
                name="x",
                latitude=0,
                longitude=0,
                elevation_m=0,
                connect_date=date(2020, 1, 1),
                disconnect_date=date(2019, 1, 1),
            )

    def test_station_feet_and_dict(self) -> None:
        station = Station(
            station_id="244",
            name="Biggs",
            latitude=39.4,
            longitude=-121.7,
            elevation_m=21.9,
            source="CIMIS",
            county="Butte",
            is_active=True,
        )
        self.assertAlmostEqual(station.elevation_ft, 71.85, places=2)
        payload = station.to_dict()
        self.assertEqual(payload["id"], "244")
        self.assertEqual(payload["elevation_ft"], 71.9)
        self.assertEqual(payload["county"], "Butte")

    def test_config_rejects_bad_bands(self) -> None:
        with self.assertRaises(ValueError):
            SelectionConfig(preferred_distance_km=60, acceptable_distance_km=50)
        with self.assertRaises(ValueError):
            SelectionConfig(preferred_elevation_diff_m=300, acceptable_elevation_diff_m=200)
        with self.assertRaises(ValueError):
            SelectionConfig(
                weight_distance=0,
                weight_elevation=0,
                weight_history=0,
            )
        with self.assertRaises(ValueError):
            SelectionConfig(elevation_tolerance_m=700, max_elevation_diff_m=600)

    def test_config_from_dict_rejects_unknown(self) -> None:
        with self.assertRaises(ValueError):
            SelectionConfig.from_dict({"max_distance_km": 50, "typo": 1})

    def test_config_roundtrip(self) -> None:
        config = SelectionConfig(station_count=4, history_years=10)
        restored = SelectionConfig.from_dict(config.to_dict())
        self.assertEqual(config, restored)


class ResultTest(unittest.TestCase):
    def test_match_imperial_fields(self) -> None:
        station = Station(
            station_id="1",
            name="Test",
            latitude=0,
            longitude=0,
            elevation_m=100,
        )
        match = StationMatch(
            rank=1,
            station=station,
            distance_km=10.0,
            bearing_deg=90.0,
            elevation_delta_m=-30.0,
            lapse_correction_c=0.195,
            score=0.8,
            score_components={"distance": 0.7, "elevation": 0.9},
            history_coverage=1.0,
            quality="preferred",
            warnings=(),
            rationale="test",
        )
        payload = match.to_dict()
        self.assertEqual(payload["bearing"], "E")
        self.assertAlmostEqual(payload["distance_mi"], 6.21, places=2)
        self.assertAlmostEqual(payload["elevation_delta_ft"], -98.4, places=1)


if __name__ == "__main__":
    unittest.main()
