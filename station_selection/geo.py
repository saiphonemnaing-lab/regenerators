"""Geospatial and unit helpers for station selection.

All distances are great-circle distances on a spherical Earth (WGS84
coordinates, mean Earth radius). For station-to-orchard screening at the
tens-of-kilometres scale, the error versus an ellipsoidal calculation is
negligible.
"""

from __future__ import annotations

import math

EARTH_RADIUS_KM = 6371.0088
KM_PER_MILE = 1.609344
FEET_PER_METRE = 3.280839895013123
METRES_PER_FOOT = 1.0 / FEET_PER_METRE

# 16-point compass, starting at north and moving clockwise.
_COMPASS_POINTS = (
    "N",
    "NNE",
    "NE",
    "ENE",
    "E",
    "ESE",
    "SE",
    "SSE",
    "S",
    "SSW",
    "SW",
    "WSW",
    "W",
    "WNW",
    "NW",
    "NNW",
)


def haversine_km(
    latitude_a: float,
    longitude_a: float,
    latitude_b: float,
    longitude_b: float,
) -> float:
    """Return the great-circle distance between two points, in kilometres."""
    lat_a = math.radians(latitude_a)
    lat_b = math.radians(latitude_b)
    delta_lat = lat_b - lat_a
    delta_lon = math.radians(longitude_b - longitude_a)

    hav = (
        math.sin(delta_lat / 2.0) ** 2
        + math.cos(lat_a) * math.cos(lat_b) * math.sin(delta_lon / 2.0) ** 2
    )
    # Guard against floating-point drift outside the domain of asin.
    hav = min(1.0, max(0.0, hav))
    return 2.0 * EARTH_RADIUS_KM * math.asin(math.sqrt(hav))


def initial_bearing_deg(
    latitude_a: float,
    longitude_a: float,
    latitude_b: float,
    longitude_b: float,
) -> float:
    """Return the initial bearing from point A to point B, in degrees [0, 360).

    The bearing is the direction you would travel from the orchard to reach
    the station, which is useful context for slope aspect and prevailing
    weather patterns.
    """
    lat_a = math.radians(latitude_a)
    lat_b = math.radians(latitude_b)
    delta_lon = math.radians(longitude_b - longitude_a)

    y = math.sin(delta_lon) * math.cos(lat_b)
    x = math.cos(lat_a) * math.sin(lat_b) - math.sin(lat_a) * math.cos(lat_b) * math.cos(
        delta_lon
    )
    bearing = math.degrees(math.atan2(y, x))
    return (bearing + 360.0) % 360.0


def compass_point(bearing_deg: float) -> str:
    """Convert a bearing to a 16-point compass label (e.g. ``SSW``)."""
    index = int((bearing_deg % 360.0 + 11.25) // 22.5) % 16
    return _COMPASS_POINTS[index]


def feet_to_metres(feet: float) -> float:
    return feet * METRES_PER_FOOT


def metres_to_feet(metres: float) -> float:
    return metres * FEET_PER_METRE


def km_to_miles(km: float) -> float:
    return km / KM_PER_MILE
