from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from station_selection import StationCatalog


class CatalogCsvTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)

    def write(self, name: str, content: str) -> Path:
        path = self.root / name
        path.write_text(content, encoding="utf-8")
        return path

    def test_alias_columns_and_types(self) -> None:
        path = self.write(
            "stations.csv",
            "Station Nbr,Station Name,Latitude,Longitude,Elevation (ft),"
            "Start Date,End Date,Is Active,Ground Cover,Is Eto Station,Source\n"
            "244,Biggs,39.4,-121.7,72,1/1/2000,12/31/2050,True,Grass,True,CIMIS\n",
        )
        catalog = StationCatalog.from_csv(path)
        self.assertEqual(len(catalog), 1)
        self.assertEqual(catalog.warnings, ())
        station = catalog.stations[0]
        self.assertEqual(station.station_id, "244")
        self.assertEqual(station.name, "Biggs")
        self.assertAlmostEqual(station.elevation_m, 21.95, places=2)
        self.assertEqual(station.connect_date, date(2000, 1, 1))
        self.assertEqual(station.disconnect_date, date(2050, 12, 31))
        self.assertIs(station.is_active, True)
        self.assertEqual(station.metadata["ground_cover"], "Grass")
        self.assertIs(station.metadata["is_eto_station"], True)
        self.assertEqual(station.station_type, "cimis")

    def test_numeric_sort_and_deduplication(self) -> None:
        path = self.write(
            "stations.csv",
            "station_id,name,latitude,longitude,elevation_m\n"
            "10,Ten,36.0,-120.0,100\n"
            "2,Two,36.1,-120.1,110\n"
            "10,Duplicate,36.2,-120.2,120\n",
        )
        catalog = StationCatalog.from_csv(path)
        self.assertEqual(catalog.station_ids, ["2", "10"])
        self.assertEqual(len(catalog.warnings), 1)
        self.assertIn("duplicate", catalog.warnings[0])

    def test_bad_rows_are_skipped_with_warnings(self) -> None:
        path = self.write(
            "stations.csv",
            "station_id,name,latitude,longitude,elevation_m,connect_date\n"
            "1,Good,36.0,-120.0,100,2020-01-01\n"
            "2,No Coordinates,,,100,2020-01-01\n"
            "3,Bad Date,36.0,-120.0,100,not-a-date\n",
        )
        catalog = StationCatalog.from_csv(path)
        self.assertEqual(catalog.station_ids, ["1"])
        self.assertEqual(len(catalog.warnings), 2)

    def test_unlabelled_elevation_requires_unit(self) -> None:
        path = self.write(
            "stations.csv",
            "station_id,name,latitude,longitude,elevation\n"
            "1,Test,36.0,-120.0,100\n",
        )
        ambiguous = StationCatalog.from_csv(path)
        self.assertEqual(len(ambiguous), 0)
        self.assertIn("default_elevation_unit", ambiguous.warnings[0])

        metres = StationCatalog.from_csv(path, default_elevation_unit="m")
        self.assertAlmostEqual(metres.stations[0].elevation_m, 100.0)

        feet = StationCatalog.from_csv(path, default_elevation_unit="ft")
        self.assertAlmostEqual(feet.stations[0].elevation_m, 30.48, places=2)

    def test_has_hourly_and_daily_flags(self) -> None:
        path = self.write(
            "stations.csv",
            "station_id,name,latitude,longitude,elevation_m,has_hourly,has_daily\n"
            "1,Test,36.0,-120.0,100,no,yes\n",
        )
        station = StationCatalog.from_csv(path).stations[0]
        self.assertIs(station.has_hourly, False)
        self.assertIs(station.has_daily, True)


class CatalogJsonTest(unittest.TestCase):
    def test_json_object(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "stations.json"
            path.write_text(
                json.dumps(
                    {
                        "stations": [
                            {
                                "station_id": "1",
                                "name": "Test",
                                "latitude": 36.0,
                                "longitude": -120.0,
                                "elevation_m": 100,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            catalog = StationCatalog.from_json(path)
            self.assertEqual(catalog.station_ids, ["1"])

    def test_json_list(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "stations.json"
            path.write_text(
                json.dumps(
                    [
                        {
                            "station_id": "1",
                            "name": "Test",
                            "latitude": 36.0,
                            "longitude": -120.0,
                            "elevation_ft": 328.084,
                        }
                    ]
                ),
                encoding="utf-8",
            )
            station = StationCatalog.from_json(path).stations[0]
            self.assertAlmostEqual(station.elevation_m, 100.0, places=2)


if __name__ == "__main__":
    unittest.main()
