"""Select the best weather stations for an orchard (Member 1 deliverable).

Given an orchard's latitude, longitude and elevation, rank the available
weather-station catalog and return the stations that are most likely to
represent the orchard's microclimate.

The module implements the team contract::

    selected = select_stations(
        latitude=38.54,
        longitude=-121.74,
        elevation=20,
        stations_df=all_stations,   # station_id, station_name, latitude,
                                    # longitude, elevation_m
        n_stations=3,
        max_distance_km=30,
        max_elevation_diff_m=100,
    )

Two signals are used:

* **Distance** -- great-circle (haversine) distance from the orchard.
* **Elevation difference** -- a first-order proxy for microclimate. A station
  that is close but 300 m higher is often a worse proxy than one farther away
  at the same elevation, especially for chill accumulation.

Scoring
-------
Suitability is a penalty, so **lower is better**::

    d = distance_km / max_distance_km
    e = elevation_diff_m / max_elevation_diff_m
    score = 0.60 * d + 0.40 * e

Stations outside either search limit receive an extra penalty of ``1.0``. That
keeps the score interpretable:

* ``score <= 1.0`` -- the station met both search limits;
* ``score > 1.0``  -- the station exceeded at least one limit.

Only the ranking matters; the absolute value is not a probability.

Fewer than ``n_stations`` suitable stations
-------------------------------------------
Thresholds are treated as preferences for a sparse region, not as a silent
excuse to return nothing. If fewer than ``n_stations`` stations meet the limits,
the function:

1. returns every station that did meet them,
2. fills the remaining slots with the best available stations outside the
   limits,
3. leaves the real ``distance_km`` and ``elevation_diff_m`` values in place,
4. emits a :class:`StationSelectionWarning` naming each fallback and why it
   failed the limits.

Callers that require hard limits should check the returned distances and
elevation differences (or catch the warning and treat it as an error) instead
of assuming that three rows are three good stations.

Dependencies: pandas (plus the standard library).
"""

from __future__ import annotations

import math
import operator
import warnings
from typing import Final

import pandas as pd

__all__ = [
    "StationSelectionWarning",
    "calculate_distance",
    "calculate_suitability_score",
    "select_stations",
]

DEFAULT_N_STATIONS: Final = 3
DEFAULT_MAX_DISTANCE_KM: Final = 30.0
DEFAULT_MAX_ELEVATION_DIFF_M: Final = 100.0

#: Columns the caller must provide.
REQUIRED_COLUMNS: Final = (
    "station_id",
    "station_name",
    "latitude",
    "longitude",
    "elevation_m",
)

#: Accepted alternative header names, mapped to the contract name.
COLUMN_ALIASES: Final = {
    "station_name": ("name",),
}

#: Columns returned by :func:`select_stations`, in order.
OUTPUT_COLUMNS: Final = REQUIRED_COLUMNS + (
    "distance_km",
    "elevation_diff_m",
    "suitability_score",
)

# Distance is weighted slightly above elevation: temperature is spatially
# correlated, and elevation only becomes decisive when distance is comparable.
_DISTANCE_WEIGHT: Final = 0.60
_ELEVATION_WEIGHT: Final = 0.40

# Added when either search limit is exceeded so every fallback ranks below
# every station that met the limits.
_OUT_OF_LIMITS_PENALTY: Final = 1.0

_EARTH_RADIUS_KM: Final = 6371.0088


class StationSelectionWarning(UserWarning):
    """Raised as a warning when the result is weaker than requested.

    Examples: fewer than ``n_stations`` stations met the limits, or the
    catalog contains fewer stations than requested. Subclassing
    :class:`UserWarning` lets callers filter it with
    ``warnings.simplefilter("error", StationSelectionWarning)`` when a strict
    selection is required.
    """


def calculate_distance(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
) -> float:
    """Return the great-circle distance between two points, in kilometres.

    Uses the haversine formula on a spherical Earth (mean radius
    6371.0088 km), which is accurate to roughly 0.5% -- far better than the
    station spacing this module is choosing between.

    Parameters
    ----------
    lat1, lon1:
        Latitude and longitude of the first point, in decimal degrees.
    lat2, lon2:
        Latitude and longitude of the second point, in decimal degrees.

    Returns
    -------
    float
        Distance in kilometres, always non-negative.

    Raises
    ------
    TypeError
        If a coordinate is not a real number.
    ValueError
        If a coordinate is non-finite or outside its valid range.
    """
    lat1 = _validate_latitude("lat1", lat1)
    lon1 = _validate_longitude("lon1", lon1)
    lat2 = _validate_latitude("lat2", lat2)
    lon2 = _validate_longitude("lon2", lon2)

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    )
    # Clamp for floating-point safety; a can exceed 1 by an ulp.
    a = min(1.0, max(0.0, a))
    return 2.0 * _EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def calculate_suitability_score(
    distance_km: float,
    elevation_diff_m: float,
    max_distance_km: float = DEFAULT_MAX_DISTANCE_KM,
    max_elevation_diff_m: float = DEFAULT_MAX_ELEVATION_DIFF_M,
) -> float:
    """Return a station's suitability penalty; lower is better.

    The score normalises both signals by their search limits, so a value of
    ``1.0`` means the station sits exactly on both limits. Stations outside
    either limit receive an extra penalty of ``1.0`` and therefore always rank
    below stations that met the limits.

    Parameters
    ----------
    distance_km:
        Distance from the orchard, in kilometres. Must be non-negative.
    elevation_diff_m:
        Absolute elevation difference from the orchard, in metres. Must be
        non-negative.
    max_distance_km:
        Distance limit used to normalise ``distance_km``. Must be positive.
    max_elevation_diff_m:
        Elevation limit used to normalise ``elevation_diff_m``. Must be
        positive.

    Returns
    -------
    float
        ``0.0`` for a station at the orchard, ``1.0`` at both limits, and
        greater than ``1.0`` when a limit is exceeded.
    """
    distance_km = _validate_non_negative("distance_km", distance_km)
    elevation_diff_m = _validate_non_negative(
        "elevation_diff_m", elevation_diff_m
    )
    max_distance_km = _validate_positive(
        "max_distance_km", max_distance_km
    )
    max_elevation_diff_m = _validate_positive(
        "max_elevation_diff_m", max_elevation_diff_m
    )

    distance_ratio = distance_km / max_distance_km
    elevation_ratio = elevation_diff_m / max_elevation_diff_m

    score = (
        _DISTANCE_WEIGHT * distance_ratio
        + _ELEVATION_WEIGHT * elevation_ratio
    )
    if distance_ratio > 1.0 or elevation_ratio > 1.0:
        score += _OUT_OF_LIMITS_PENALTY
    return score


def select_stations(
    latitude: float,
    longitude: float,
    elevation: float,
    stations_df: pd.DataFrame,
    n_stations: int = DEFAULT_N_STATIONS,
    max_distance_km: float = DEFAULT_MAX_DISTANCE_KM,
    max_elevation_diff_m: float = DEFAULT_MAX_ELEVATION_DIFF_M,
) -> pd.DataFrame:
    """Return the best weather stations for an orchard.

    Parameters
    ----------
    latitude, longitude:
        Orchard coordinates in decimal degrees (WGS84).
    elevation:
        Orchard elevation in metres above sea level.
    stations_df:
        Catalog of candidate stations. Must contain ``station_id``,
        ``station_name``, ``latitude``, ``longitude`` and ``elevation_m``;
        ``name`` is accepted as an alias for ``station_name``. Extra columns
        are ignored.
    n_stations:
        Number of stations to return. Defaults to 3.
    max_distance_km:
        Preferred maximum distance from the orchard. Defaults to 30 km.
    max_elevation_diff_m:
        Preferred maximum absolute elevation difference. Defaults to 100 m.

    Returns
    -------
    pandas.DataFrame
        Up to ``n_stations`` rows, best first, with the columns
        ``station_id``, ``station_name``, ``latitude``, ``longitude``,
        ``elevation_m``, ``distance_km``, ``elevation_diff_m`` and
        ``suitability_score`` (lower is better). The index is reset to
        ``0..n-1``.

    Raises
    ------
    TypeError
        If an argument has the wrong type.
    ValueError
        If a coordinate, elevation, limit or ``n_stations`` is invalid, if
        required catalog columns are missing, or if no usable station rows
        remain after cleaning.

    Warns
    -----
    StationSelectionWarning
        If fewer than ``n_stations`` stations meet the search limits, or if
        the catalog has fewer than ``n_stations`` usable rows. Fallback rows
        keep their true distances and elevation differences; they are never
        presented as if they had passed the limits.

    Notes
    -----
    ``stations_df`` is not modified. Rows with missing, non-numeric,
    non-finite or out-of-range coordinates/elevation are discarded with a
    warning rather than silently coerced to zero.

    Examples
    --------
    >>> stations = pd.DataFrame(
    ...     {
    ...         "station_id": ["A", "B"],
    ...         "station_name": ["Near", "Far"],
    ...         "latitude": [38.54, 39.00],
    ...         "longitude": [-121.74, -121.74],
    ...         "elevation_m": [20.0, 40.0],
    ...     }
    ... )
    >>> select_stations(38.54, -121.74, 20, stations)  # doctest: +SKIP
       station_id station_name  latitude  ...  distance_km  elevation_diff_m  suitability_score
    0           A         Near     38.54  ...          0.0                0.0                0.0
    1           B          Far     39.00  ...         51.15               20.0                2.103
    """
    latitude = _validate_latitude("latitude", latitude)
    longitude = _validate_longitude("longitude", longitude)
    elevation = _validate_finite("elevation", elevation)
    max_distance_km = _validate_positive(
        "max_distance_km", max_distance_km
    )
    max_elevation_diff_m = _validate_positive(
        "max_elevation_diff_m", max_elevation_diff_m
    )
    n_stations = _validate_station_count(n_stations)

    candidates = _prepare_candidates(stations_df)

    candidates["distance_km"] = [
        calculate_distance(latitude, longitude, row_lat, row_lon)
        for row_lat, row_lon in zip(
            candidates["latitude"], candidates["longitude"], strict=True
        )
    ]
    candidates["elevation_diff_m"] = (
        candidates["elevation_m"] - elevation
    ).abs()
    candidates["suitability_score"] = [
        calculate_suitability_score(
            distance_km,
            elevation_diff_m,
            max_distance_km=max_distance_km,
            max_elevation_diff_m=max_elevation_diff_m,
        )
        for distance_km, elevation_diff_m in zip(
            candidates["distance_km"],
            candidates["elevation_diff_m"],
            strict=True,
        )
    ]

    sort_columns = ["suitability_score", "distance_km", "elevation_diff_m"]
    candidates = candidates.sort_values(
        sort_columns, kind="mergesort"
    ).reset_index(drop=True)

    within_limits = (
        (candidates["distance_km"] <= max_distance_km)
        & (candidates["elevation_diff_m"] <= max_elevation_diff_m)
    )
    eligible = candidates[within_limits]
    fallbacks = candidates[~within_limits]

    # Take eligible stations first; only fill leftover slots with fallbacks.
    selected_parts = [eligible.head(n_stations)]
    remaining = n_stations - len(eligible.head(n_stations))
    if remaining > 0 and not fallbacks.empty:
        selected_parts.append(fallbacks.head(remaining))

    selected = pd.concat(selected_parts, ignore_index=True)
    selected = selected.sort_values(
        sort_columns, kind="mergesort"
    ).reset_index(drop=True)

    _warn_if_weak_selection(
        selected=selected,
        eligible_count=len(eligible),
        available_count=len(candidates),
        n_stations=n_stations,
        max_distance_km=max_distance_km,
        max_elevation_diff_m=max_elevation_diff_m,
    )

    result = selected.loc[:, list(OUTPUT_COLUMNS)].copy()
    result["distance_km"] = result["distance_km"].round(3)
    result["elevation_diff_m"] = result["elevation_diff_m"].round(1)
    result["suitability_score"] = result["suitability_score"].round(4)
    return result.reset_index(drop=True)


def _prepare_candidates(stations_df: pd.DataFrame) -> pd.DataFrame:
    """Validate the catalog and return a clean working copy."""
    if not isinstance(stations_df, pd.DataFrame):
        raise TypeError(
            "stations_df must be a pandas.DataFrame, "
            f"got {type(stations_df).__name__}"
        )

    frame = stations_df
    for canonical, aliases in COLUMN_ALIASES.items():
        if canonical not in frame.columns:
            for alias in aliases:
                if alias in frame.columns:
                    frame = frame.rename(columns={alias: canonical})
                    break

    missing = [name for name in REQUIRED_COLUMNS if name not in frame.columns]
    if missing:
        raise ValueError(
            "stations_df is missing required column(s): "
            + ", ".join(missing)
            + ". Required: "
            + ", ".join(REQUIRED_COLUMNS)
        )
    if frame.empty:
        raise ValueError("stations_df is empty; no station can be selected")

    candidates = frame.loc[:, list(REQUIRED_COLUMNS)].copy()

    usable = pd.Series(True, index=candidates.index)
    for column in ("latitude", "longitude", "elevation_m"):
        values = pd.to_numeric(candidates[column], errors="coerce")
        usable &= values.notna() & values.map(math.isfinite)
        candidates[column] = values

    usable &= candidates["latitude"].between(-90.0, 90.0)
    usable &= candidates["longitude"].between(-180.0, 180.0)

    dropped = int((~usable).sum())
    if dropped:
        warnings.warn(
            f"Ignoring {dropped} station row(s) with missing, non-numeric, "
            "non-finite or out-of-range coordinates/elevation.",
            StationSelectionWarning,
            stacklevel=3,
        )

    candidates = candidates[usable].copy()
    if candidates.empty:
        raise ValueError(
            "No usable station rows remain after discarding missing or "
            "invalid coordinates/elevation."
        )
    return candidates


def _warn_if_weak_selection(
    selected: pd.DataFrame,
    eligible_count: int,
    available_count: int,
    n_stations: int,
    max_distance_km: float,
    max_elevation_diff_m: float,
) -> None:
    """Explain any shortfall instead of silently returning weak stations."""
    fallback_count = len(selected) - min(eligible_count, len(selected))
    short_on_catalog = available_count < n_stations
    short_on_quality = eligible_count < min(n_stations, available_count)

    if not short_on_catalog and not short_on_quality:
        return

    lines: list[str] = []
    if short_on_catalog:
        lines.append(
            f"Only {available_count} usable station(s) are available; "
            f"{n_stations} were requested."
        )
    if short_on_quality:
        lines.append(
            f"Only {eligible_count} of {n_stations} requested station(s) met "
            f"the search limits ({max_distance_km:g} km, "
            f"{max_elevation_diff_m:g} m elevation difference)."
        )
        for row in selected.tail(fallback_count).itertuples(index=False):
            reasons = _describe_limit_violations(
                row, max_distance_km, max_elevation_diff_m
            )
            lines.append(
                f"  Fallback outside the limits: {row.station_id} "
                f"({row.station_name}) -- {reasons}."
            )
    if len(selected) < n_stations:
        lines.append(
            f"Returning {len(selected)} station(s) instead of {n_stations}."
        )
    if eligible_count == 0:
        lines.append(
            "No station met the limits; treat these stations as a weak "
            "microclimate proxy and consider adding an in-orchard sensor."
        )

    warnings.warn("\n".join(lines), StationSelectionWarning, stacklevel=3)


def _describe_limit_violations(
    row: object,
    max_distance_km: float,
    max_elevation_diff_m: float,
) -> str:
    """Return a human-readable reason a fallback failed the search limits."""
    reasons: list[str] = []
    if row.distance_km > max_distance_km:
        reasons.append(
            f"distance {row.distance_km:.1f} km > {max_distance_km:g} km"
        )
    if row.elevation_diff_m > max_elevation_diff_m:
        reasons.append(
            f"elevation difference {row.elevation_diff_m:.0f} m > "
            f"{max_elevation_diff_m:g} m"
        )
    return "; ".join(reasons) or "unknown reason"


def _validate_station_count(value: object) -> int:
    if isinstance(value, bool):
        raise TypeError("n_stations must be an integer, not bool")
    try:
        count = operator.index(value)  # accepts numpy integers as well
    except TypeError as exc:
        raise TypeError(
            f"n_stations must be an integer, got {type(value).__name__}"
        ) from exc
    if count < 1:
        raise ValueError(f"n_stations must be at least 1, got {count}")
    return count


def _validate_finite(name: str, value: object) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise TypeError(
            f"{name} must be a real number, got {value!r}"
        ) from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite, got {value!r}")
    return number


def _validate_non_negative(name: str, value: object) -> float:
    number = _validate_finite(name, value)
    if number < 0.0:
        raise ValueError(f"{name} must be non-negative, got {number!r}")
    return number


def _validate_positive(name: str, value: object) -> float:
    number = _validate_finite(name, value)
    if number <= 0.0:
        raise ValueError(f"{name} must be positive, got {number!r}")
    return number


def _validate_latitude(name: str, value: object) -> float:
    number = _validate_finite(name, value)
    if not -90.0 <= number <= 90.0:
        raise ValueError(f"{name} must be between -90 and 90, got {number!r}")
    return number


def _validate_longitude(name: str, value: object) -> float:
    number = _validate_finite(name, value)
    if not -180.0 <= number <= 180.0:
        raise ValueError(
            f"{name} must be between -180 and 180, got {number!r}"
        )
    return number
