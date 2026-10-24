from __future__ import annotations

import json
import unittest
from datetime import date
from pathlib import Path

from station_selection import OrchardLocation, SelectionConfig, StationCatalog, select_stations

ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "data" / "cimis_stations.csv"


class SnapshotTest(unittest.TestCase):
    """Smoke tests against the committed CIMIS station snapshot."""

    @classmethod
    def setUpClass(cls) -> None:
        if not CATALOG_PATH.exists():
            raise unittest.SkipTest(f"missing catalog snapshot: {CATALOG_PATH}")
        cls.catalog = StationCatalog.from_csv(CATALOG_PATH)

    def test_catalog_has_expected_size(self) -> None:
        self.assertGreaterEqual(len(self.catalog), 250)
        self.assertEqual(self.catalog.warnings, ())

    def test_chico_demo(self) -> None:
        result = select_stations(
            OrchardLocation(39.7285, -121.8375, 60.0, "Chico almond demo"),
            self.catalog,
            SelectionConfig(),
            as_of=date(2026, 10, 24),
        )
        self.assertEqual(result.station_ids, ["244", "222", "250"])
        self.assertTrue(all(match.distance_km <= 100 for match in result.selected))
        self.assertTrue(all(match.station.is_active for match in result.selected))
        payload = json.loads(result.to_json())
        self.assertEqual(
            [entry["station"]["county"] for entry in payload["selected"]],
            ["Butte", "Tehama", "Colusa"],
        )

    def test_foothill_demo_flags_elevation_risk(self) -> None:
        result = select_stations(
            OrchardLocation(38.7385, -120.6697, 1000.0, "Camino apple demo"),
            self.catalog,
            SelectionConfig(),
            as_of=date(2026, 10, 24),
        )
        self.assertEqual(result.station_ids[0], "13")
        self.assertEqual(len(result.selected), 3)
        self.assertTrue(
            any(
                "elevation difference" in warning
                for match in result.selected
                for warning in match.warnings
            )
        )
        marginal = [m for m in result.selected if m.quality == "marginal"]
        self.assertEqual(len(marginal), 2)

    def test_every_selected_station_respects_hard_caps(self) -> None:
        result = select_stations(
            OrchardLocation(34.9530, -120.4357, 66.0, "Santa Maria demo"),
            self.catalog,
            SelectionConfig(),
            as_of=date(2026, 10, 24),
        )
        self.assertEqual(len(result.selected), 3)
        for match in result.selected:
            self.assertLessEqual(match.distance_km, 100.0)
            self.assertLessEqual(abs(match.elevation_delta_m), 600.0)
            self.assertGreaterEqual(match.history_coverage or 0.0, 0.5)


if __name__ == "__main__":
    unittest.main()
