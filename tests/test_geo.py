from __future__ import annotations

import math
import unittest

from station_selection.geo import (
    compass_point,
    feet_to_metres,
    haversine_km,
    initial_bearing_deg,
    km_to_miles,
    metres_to_feet,
)


class GeoTest(unittest.TestCase):
    def test_haversine_identity(self) -> None:
        self.assertAlmostEqual(haversine_km(36.0, -120.0, 36.0, -120.0), 0.0)

    def test_haversine_one_degree_at_equator(self) -> None:
        # One degree of longitude at the equator is about 111.2 km.
        distance = haversine_km(0.0, 0.0, 0.0, 1.0)
        self.assertAlmostEqual(distance, 111.195, places=2)

    def test_haversine_is_symmetric(self) -> None:
        forward = haversine_km(39.73, -121.84, 36.75, -119.77)
        reverse = haversine_km(36.75, -119.77, 39.73, -121.84)
        self.assertAlmostEqual(forward, reverse, places=9)

    def test_bearing_cardinal_directions(self) -> None:
        self.assertAlmostEqual(initial_bearing_deg(0.0, 0.0, 1.0, 0.0), 0.0)
        self.assertAlmostEqual(initial_bearing_deg(0.0, 0.0, 0.0, 1.0), 90.0)
        self.assertAlmostEqual(initial_bearing_deg(0.0, 0.0, -1.0, 0.0), 180.0)
        self.assertAlmostEqual(initial_bearing_deg(0.0, 0.0, 0.0, -1.0), 270.0)

    def test_compass_labels(self) -> None:
        self.assertEqual(compass_point(0.0), "N")
        self.assertEqual(compass_point(90.0), "E")
        self.assertEqual(compass_point(180.0), "S")
        self.assertEqual(compass_point(270.0), "W")
        self.assertEqual(compass_point(337.5), "NNW")
        self.assertEqual(compass_point(360.0), "N")

    def test_unit_conversions(self) -> None:
        self.assertAlmostEqual(feet_to_metres(100.0), 30.48, places=6)
        self.assertAlmostEqual(metres_to_feet(30.48), 100.0, places=6)
        self.assertAlmostEqual(km_to_miles(1.609344), 1.0, places=9)


if __name__ == "__main__":
    unittest.main()
