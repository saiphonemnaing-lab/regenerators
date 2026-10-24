"""Tunable parameters that define what counts as a suitable station.

The defaults are deliberately conservative enough for California's Central
Valley, where CIMIS coverage is sparse, while still preferring stations that
share the orchard's elevation. Every threshold is configurable because the
right values differ between a flat valley floor and a foothill orchard.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from typing import Any


@dataclass(frozen=True, slots=True)
class SelectionConfig:
    """Thresholds and weights for :func:`station_selection.select_stations`.

    Hard caps (``max_*``) decide which stations are eligible at all. The
    preferred/acceptable thresholds only assign a quality label and warnings;
    a station between the acceptable and hard limits can still be selected
    when nothing better exists, but it is marked ``marginal``.
    """

    # How many stations to return, plus optional backups.
    station_count: int = 3
    alternate_count: int = 2

    # Length of the historical baseline used for coverage scoring.
    history_years: int = 3
    # Minimum fraction of the window a station must cover to be eligible.
    # 0.5 means at least ~18 months of a 3-year window.
    min_history_coverage: float = 0.5

    # Hard eligibility caps. Raise the distance cap for very sparse regions.
    max_distance_km: float = 100.0
    max_elevation_diff_m: float = 600.0

    # Soft quality bands: preferred -> acceptable -> marginal.
    preferred_distance_km: float = 25.0
    acceptable_distance_km: float = 50.0
    preferred_elevation_diff_m: float = 100.0
    acceptable_elevation_diff_m: float = 250.0
    preferred_history_coverage: float = 0.95
    acceptable_history_coverage: float = 0.80

    # Similarity constants. Distance similarity decays exponentially.
    distance_decay_km: float = 25.0
    # Elevation differences inside this band are treated as equivalent.
    # Beyond it, similarity decays exponentially with the excess difference.
    # The default matches the "preferred" elevation band below.
    elevation_tolerance_m: float = 100.0
    elevation_decay_m: float = 100.0

    # Weights are renormalized over whichever components are known.
    weight_distance: float = 0.40
    weight_elevation: float = 0.40
    weight_history: float = 0.20

    # Avoid choosing near-duplicate stations (e.g. two sensors on the same
    # ranch) when a spatially independent station is available.
    min_station_separation_km: float = 8.0

    # Standard environmental lapse rate, used only to report a first-order
    # air-temperature correction; it does not affect the ranking.
    lapse_rate_c_per_km: float = 6.5

    # Data requirements. Set these when the catalog records availability.
    # Active stations are considered first; inactive ones are only used when
    # there are not enough active stations, and always carry a warning.
    prefer_active: bool = True
    include_inactive: bool = True
    require_hourly: bool = False  # chill-hours models need hourly data
    require_daily: bool = False  # GDD models need daily max/min

    # If > 0, raise InsufficientStationsError when fewer stations are found.
    fail_if_fewer_than: int = 0

    def __post_init__(self) -> None:
        if self.station_count < 1:
            raise ValueError("station_count must be at least 1")
        if self.alternate_count < 0:
            raise ValueError("alternate_count must be zero or greater")
        if self.history_years < 1:
            raise ValueError("history_years must be at least 1")
        if not 0.0 <= self.min_history_coverage <= 1.0:
            raise ValueError("min_history_coverage must be between 0 and 1")

        if self.max_distance_km <= 0:
            raise ValueError("max_distance_km must be positive")
        if self.max_elevation_diff_m < 0:
            raise ValueError("max_elevation_diff_m must be zero or greater")
        if not 0 < self.preferred_distance_km <= self.acceptable_distance_km <= self.max_distance_km:
            raise ValueError(
                "distance bands must satisfy 0 < preferred <= acceptable <= max"
            )
        if not (
            0
            <= self.preferred_elevation_diff_m
            <= self.acceptable_elevation_diff_m
            <= self.max_elevation_diff_m
        ):
            raise ValueError(
                "elevation bands must satisfy 0 <= preferred <= acceptable <= max"
            )
        if not 0.0 <= self.acceptable_history_coverage <= self.preferred_history_coverage <= 1.0:
            raise ValueError(
                "history bands must satisfy 0 <= acceptable <= preferred <= 1"
            )
        if self.distance_decay_km <= 0:
            raise ValueError("distance_decay_km must be positive")
        if not 0 <= self.elevation_tolerance_m <= self.max_elevation_diff_m:
            raise ValueError(
                "elevation_tolerance_m must be between 0 and max_elevation_diff_m"
            )
        if self.elevation_decay_m <= 0:
            raise ValueError("elevation_decay_m must be positive")
        if self.min_station_separation_km < 0:
            raise ValueError("min_station_separation_km must be zero or greater")

        weights = (self.weight_distance, self.weight_elevation, self.weight_history)
        if any(weight < 0 for weight in weights):
            raise ValueError("weights must be zero or greater")
        if sum(weights) <= 0:
            raise ValueError("at least one score weight must be positive")

        if self.lapse_rate_c_per_km < 0:
            raise ValueError("lapse_rate_c_per_km must be zero or greater")
        if self.fail_if_fewer_than < 0:
            raise ValueError("fail_if_fewer_than must be zero or greater")
        if self.fail_if_fewer_than > self.station_count:
            raise ValueError("fail_if_fewer_than cannot exceed station_count")

    @classmethod
    def from_dict(cls, values: dict[str, Any]) -> "SelectionConfig":
        """Build a config from a JSON object, rejecting unknown field names."""
        known = {field.name for field in fields(cls)}
        unknown = sorted(set(values) - known)
        if unknown:
            raise ValueError(f"unknown config field(s): {', '.join(unknown)}")
        return cls(**values)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
