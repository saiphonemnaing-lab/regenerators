from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from station_selection.cli import main

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "data" / "cimis_stations.csv"


class CliTest(unittest.TestCase):
    def run_cli(self, *args: str) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(list(args))
        return code, stdout.getvalue(), stderr.getvalue()

    def test_json_output(self) -> None:
        if not CATALOG.exists():
            self.skipTest("missing catalog snapshot")
        code, stdout, stderr = self.run_cli(
            "--lat",
            "39.7285",
            "--lon",
            "-121.8375",
            "--elevation",
            "60",
            "--name",
            "Chico",
            "--catalog",
            str(CATALOG),
            "--as-of",
            "2026-10-24",
            "--format",
            "json",
        )
        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["selected_station_ids"], ["244", "222", "250"])
        self.assertEqual(payload["location"]["name"], "Chico")

    def test_text_output_and_feet(self) -> None:
        if not CATALOG.exists():
            self.skipTest("missing catalog snapshot")
        code, stdout, stderr = self.run_cli(
            "--lat",
            "39.7285",
            "--lon",
            "-121.8375",
            "--elevation",
            "196.9",
            "--elevation-unit",
            "ft",
            "--catalog",
            str(CATALOG),
            "--as-of",
            "2026-10-24",
        )
        self.assertEqual(code, 0, stderr)
        self.assertIn("Selected stations (3)", stdout)
        self.assertIn("#244 Biggs", stdout)

    def test_strict_mode_exit_code(self) -> None:
        with TemporaryDirectory() as tempdir:
            catalog = Path(tempdir) / "one.csv"
            catalog.write_text(
                "station_id,name,latitude,longitude,elevation_m\n"
                "1,Only,39.7,-121.8,60\n",
                encoding="utf-8",
            )
            code, stdout, stderr = self.run_cli(
                "--lat",
                "39.7285",
                "--lon",
                "-121.8375",
                "--elevation",
                "60",
                "--catalog",
                str(catalog),
                "--as-of",
                "2026-10-24",
                "--strict",
                "--format",
                "json",
            )
        self.assertEqual(code, 3)
        self.assertIn("error:", stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["selected_station_ids"], ["1"])

    def test_missing_catalog_is_an_error(self) -> None:
        code, stdout, stderr = self.run_cli(
            "--lat",
            "39.7285",
            "--lon",
            "-121.8375",
            "--elevation",
            "60",
            "--catalog",
            "does-not-exist.csv",
        )
        self.assertEqual(code, 2)
        self.assertIn("catalog not found", stderr)
        self.assertEqual(stdout, "")


if __name__ == "__main__":
    unittest.main()
