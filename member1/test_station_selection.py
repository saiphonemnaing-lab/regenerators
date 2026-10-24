"""Tests for the Member 1 station-selection contract.

Run from the repository root::

    python -m unittest discover -s member1 -p "test_*.py" -v

The tests deliberately use small synthetic catalogs so the expected distances
and eligibility decisions are obvious.
"""

from __future__ import annotations

import sys
import unittest
import warnings
from pathlib import Path

import pandas as pd

# The deliverable is a flat module meant to be dropped into a team repo. Put
# its directory first so it wins over the repository's station_selection
# package while these tests run.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from station_selection import (  # noqa: E402
    StationSelectionWarning,
    calculate_distance,
    calculate_suitability_score,
    select_stations,
)

ORCHARD = {"latitude": 38.5, "longitude": -121.7, "elevation": 20.0}


def make_catalog() -> pd.DataFrame:
    """Five stations: three pass the defaults, two fail one limit each."""
    return pd.DataFrame(
        {
            "station_id": ["A", "B", "C", "D", "E"],
            "station_name": ["AtOrchard", "Near", "Close", "Far", "High"],
            "latitude": [38.50, 38.55, 38.60, 39.00, 38.51],
            "longitude": [-121.70, -121.70, -121.70, -121.70, -121.70],
            "elevation_m": [20.0, 30.0, 80.0, 20.0, 300.0],
        }
    )


class DistanceTests(unittest.TestCase):
    def test_same_point_is_zero(self) -> None:
        self.assertEqual(calculate_distance(38.5, -121.7, 38.5, -121.7), 0.0)

    def test_one_degree_of_longitude_at_equator(self) -> None:
        # 2*pi*6371.0088/360 = 111.195 km
        self.assertAlmostEqual(
            calculate_distance(0.0, 0.0, 0.0, 1.0), 111.1949, places=3
        )

    def test_distance_is_symmetric(self) -> None:
        forward = calculate_distance(38.5, -121.7, 39.0, -121.5)
        reverse = calculate_distance(39.0, -121.5, 38.5, -121.7)
        self.assertAlmostEqual(forward, reverse, places=9)

    def test_rejects_invalid_coordinates(self) -> None:
        with self.assertRaises(ValueError):
            calculate_distance(91.0, 0.0, 0.0, 0.0)
        with self.assertRaises(ValueError):
            calculate_distance(0.0, -181.0, 0.0, 0.0)
        with self.assertRaises(ValueError):
            calculate_distance(float("nan"), 0.0, 0.0, 0.0)
        with self.assertRaises(TypeError):
            calculate_distance(None, 0.0, 0.0, 0.0)


class ScoreTests(unittest.TestCase):
    def test_orchard_itself_scores_zero(self) -> None:
        self.assertEqual(calculate_suitability_score(0.0, 0.0), 0.0)

    def test_both_limits_score_one(self) -> None:
        self.assertAlmostEqual(
            calculate_suitability_score(30.0, 100.0), 1.0, places=12
        )

    def test_exceeding_either_limit_adds_penalty(self) -> None:
        self.assertGreater(calculate_suitability_score(30.1, 0.0), 1.0)
        self.assertGreater(calculate_suitability_score(0.0, 100.1), 1.0)

    def test_lower_is_better(self) -> None:
        base = calculate_suitability_score(5.0, 10.0)
        self.assertLess(base, calculate_suitability_score(10.0, 10.0))
        self.assertLess(base, calculate_suitability_score(5.0, 20.0))

    def test_custom_limits_are_used_for_normalisation(self) -> None:
        self.assertAlmostEqual(
            calculate_suitability_score(
                15.0,
                50.0,
                max_distance_km=15.0,
                max_elevation_diff_m=50.0,
            ),
            1.0,
            places=12,
        )

    def test_rejects_negative_inputs_and_zero_limits(self) -> None:
        with self.assertRaises(ValueError):
            calculate_suitability_score(-1.0, 0.0)
        with self.assertRaises(ValueError):
            calculate_suitability_score(0.0, -1.0)
        with self.assertRaises(ValueError):
            calculate_suitability_score(0.0, 0.0, max_distance_km=0.0)
        with self.assertRaises(ValueError):
            calculate_suitability_score(
                0.0, 0.0, max_elevation_diff_m=0.0
            )


class SelectStationsTests(unittest.TestCase):
    def test_returns_three_best_within_limits(self) -> None:
        result = select_stations(stations_df=make_catalog(), **ORCHARD)

        self.assertEqual(list(result["station_id"]), ["A", "B", "C"])
        self.assertEqual(len(result), 3)
        self.assertEqual(result.index.tolist(), [0, 1, 2])

    def test_output_has_contract_columns_in_order(self) -> None:
        result = select_stations(stations_df=make_catalog(), **ORCHARD)
        self.assertEqual(
            list(result.columns),
            [
                "station_id",
                "station_name",
                "latitude",
                "longitude",
                "elevation_m",
                "distance_km",
                "elevation_diff_m",
                "suitability_score",
            ],
        )

    def test_scores_increase_down_the_result(self) -> None:
        result = select_stations(stations_df=make_catalog(), **ORCHARD)
        self.assertEqual(
            result["suitability_score"].tolist(),
            sorted(result["suitability_score"].tolist()),
        )
        self.assertLess(result.loc[0, "suitability_score"], 1.0)

    def test_distance_and_elevation_values_are_real(self) -> None:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", StationSelectionWarning)
            result = select_stations(
                stations_df=make_catalog(), n_stations=5, **ORCHARD
            )

        by_id = result.set_index("station_id")
        self.assertEqual(by_id.loc["A", "distance_km"], 0.0)
        self.assertEqual(by_id.loc["A", "elevation_diff_m"], 0.0)
        self.assertAlmostEqual(
            by_id.loc["B", "distance_km"], 5.56, delta=0.01
        )
        self.assertEqual(by_id.loc["B", "elevation_diff_m"], 10.0)
        self.assertGreater(by_id.loc["D", "distance_km"], 30.0)
        self.assertGreater(by_id.loc["E", "elevation_diff_m"], 100.0)

    def test_unsorted_input_still_returns_best_first(self) -> None:
        shuffled = make_catalog().iloc[[4, 0, 3, 2, 1]]
        result = select_stations(stations_df=shuffled, **ORCHARD)
        self.assertEqual(list(result["station_id"]), ["A", "B", "C"])

    def test_extra_catalog_columns_are_ignored(self) -> None:
        catalog = make_catalog()
        catalog["county"] = "Butte"
        result = select_stations(stations_df=catalog, **ORCHARD)
        self.assertNotIn("county", result.columns)

    def test_name_alias_is_accepted(self) -> None:
        catalog = make_catalog().rename(
            columns={"station_name": "name"}
        )
        result = select_stations(stations_df=catalog, **ORCHARD)
        self.assertEqual(
            result["station_name"].tolist(),
            ["AtOrchard", "Near", "Close"],
        )

    def test_integer_station_ids_are_preserved(self) -> None:
        catalog = make_catalog()
        catalog["station_id"] = [1, 2, 3, 4, 5]
        result = select_stations(stations_df=catalog, **ORCHARD)
        self.assertEqual(result["station_id"].tolist(), [1, 2, 3])

    def test_input_is_not_mutated(self) -> None:
        catalog = make_catalog()
        original = catalog.copy(deep=True)
        select_stations(stations_df=catalog, **ORCHARD)
        pd.testing.assert_frame_equal(catalog, original)

    def test_custom_count_and_limits(self) -> None:
        result = select_stations(
            n_stations=2,
            max_distance_km=10.0,
            max_elevation_diff_m=100.0,
            stations_df=make_catalog(),
            **ORCHARD,
        )
        self.assertEqual(list(result["station_id"]), ["A", "B"])
        self.assertTrue(
            (result["distance_km"] <= 10.0).all(),
        )


class FewerThanRequestedTests(unittest.TestCase):
    def test_fills_shortfall_and_warns_with_actual_values(self) -> None:
        catalog = make_catalog().iloc[[0, 1, 3, 4]]  # only A and B qualify
        with self.assertWarns(StationSelectionWarning) as caught:
            result = select_stations(stations_df=catalog, **ORCHARD)

        self.assertEqual(len(result), 3)
        self.assertEqual(list(result["station_id"]), ["A", "B", "D"])
        fallback = result.iloc[-1]
        self.assertGreater(fallback["distance_km"], 30.0)
        self.assertGreater(fallback["suitability_score"], 1.0)
        self.assertIn("Fallback outside the limits", str(caught.warning))
        self.assertIn("D", str(caught.warning))

    def test_no_station_meets_limits_returns_best_alternatives(self) -> None:
        catalog = make_catalog().iloc[[3, 4]]
        with self.assertWarns(StationSelectionWarning) as caught:
            result = select_stations(stations_df=catalog, **ORCHARD)

        self.assertEqual(len(result), 2)
        self.assertTrue((result["suitability_score"] > 1.0).all())
        self.assertIn("No station met the limits", str(caught.warning))

    def test_catalog_smaller_than_requested(self) -> None:
        catalog = make_catalog().iloc[[0, 1]]
        with self.assertWarns(StationSelectionWarning) as caught:
            result = select_stations(stations_df=catalog, **ORCHARD)

        self.assertEqual(len(result), 2)
        self.assertIn("Only 2 usable station(s)", str(caught.warning))

    def test_warning_can_be_treated_as_an_error(self) -> None:
        catalog = make_catalog().iloc[[0, 1]]
        with warnings.catch_warnings():
            warnings.simplefilter("error", StationSelectionWarning)
            with self.assertRaises(StationSelectionWarning):
                select_stations(stations_df=catalog, **ORCHARD)


class ValidationTests(unittest.TestCase):
    def test_missing_required_columns(self) -> None:
        catalog = make_catalog().drop(columns=["elevation_m"])
        with self.assertRaisesRegex(ValueError, "elevation_m"):
            select_stations(stations_df=catalog, **ORCHARD)

    def test_non_dataframe_input(self) -> None:
        with self.assertRaises(TypeError):
            select_stations(
                latitude=38.5,
                longitude=-121.7,
                elevation=20.0,
                stations_df=[{"station_id": "A"}],
            )

    def test_empty_catalog(self) -> None:
        with self.assertRaises(ValueError):
            select_stations(
                stations_df=pd.DataFrame(
                    columns=[
                        "station_id",
                        "station_name",
                        "latitude",
                        "longitude",
                        "elevation_m",
                    ]
                ),
                **ORCHARD,
            )

    def test_invalid_orchard_inputs(self) -> None:
        catalog = make_catalog()
        with self.assertRaises(ValueError):
            select_stations(91.0, -121.7, 20.0, catalog)
        with self.assertRaises(ValueError):
            select_stations(38.5, -181.0, 20.0, catalog)
        with self.assertRaises(ValueError):
            select_stations(38.5, -121.7, float("inf"), catalog)

    def test_invalid_station_count(self) -> None:
        catalog = make_catalog()
        with self.assertRaises(ValueError):
            select_stations(stations_df=catalog, n_stations=0, **ORCHARD)
        with self.assertRaises(TypeError):
            select_stations(stations_df=catalog, n_stations=True, **ORCHARD)
        with self.assertRaises(TypeError):
            select_stations(stations_df=catalog, n_stations=2.5, **ORCHARD)

    def test_invalid_limits(self) -> None:
        catalog = make_catalog()
        with self.assertRaises(ValueError):
            select_stations(
                stations_df=catalog, max_distance_km=0.0, **ORCHARD
            )
        with self.assertRaises(ValueError):
            select_stations(
                stations_df=catalog, max_elevation_diff_m=-1.0, **ORCHARD
            )

    def test_unusable_rows_are_dropped_with_warning(self) -> None:
        catalog = make_catalog()
        catalog.loc[2, "latitude"] = None
        catalog["elevation_m"] = catalog["elevation_m"].astype(object)
        catalog.loc[3, "elevation_m"] = "not a number"
        with self.assertWarns(StationSelectionWarning) as caught:
            result = select_stations(
                stations_df=catalog, n_stations=2, **ORCHARD
            )

        self.assertEqual(list(result["station_id"]), ["A", "B"])
        self.assertIn("Ignoring 2 station row(s)", str(caught.warning))

    def test_all_rows_unusable_raises(self) -> None:
        catalog = make_catalog()
        catalog["latitude"] = None
        with self.assertWarns(StationSelectionWarning):
            with self.assertRaises(ValueError):
                select_stations(stations_df=catalog, **ORCHARD)


if __name__ == "__main__":
    unittest.main()
