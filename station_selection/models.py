"""Data models returned by the location and station selection module."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from .config import SelectionConfig
from .geo import METRES_PER_FOOT, compass_point, km_to_miles, metres_to_feet
from .version import __version__

SCHEMA_VERSION = "1.0"


def _require_finite(name: str, value: float) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{name} must be a number")
    if not math.isfinite(float(value)):
        raise ValueError(f"{name} must be finite")


def _require_coordinate(name: str, value: float, minimum: float, maximum: float) -> None:
    _require_finite(name, value)
    if not minimum <= float(value) <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")


@dataclass(frozen=True, slots=True)
class OrchardLocation:
    """The orchard or field for which stations are being selected."""

    latitude: float
    longitude: float
    elevation_m: float
    name: str = ""

    def __post_init__(self) -> None:
        _require_coordinate("latitude", self.latitude, -90.0, 90.0)
        _require_coordinate("longitude", self.longitude, -180.0, 180.0)
        _require_finite("elevation_m", self.elevation_m)
        if not -500.0 <= float(self.elevation_m) <= 9000.0:
            raise ValueError("elevation_m must be between -500 and 9000 metres")
        object.__setattr__(self, "name", (self.name or "").strip())

    @classmethod
    def from_feet(
        cls,
        latitude: float,
        longitude: float,
        elevation_ft: float,
        name: str = "",
    ) -> "OrchardLocation":
        return cls(
            latitude=latitude,
            longitude=longitude,
            elevation_m=elevation_ft * METRES_PER_FOOT,
            name=name,
        )

    @property
    def elevation_ft(self) -> float:
        return metres_to_feet(float(self.elevation_m))

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "latitude": round(float(self.latitude), 6),
            "longitude": round(float(self.longitude), 6),
            "elevation_m": round(float(self.elevation_m), 1),
            "elevation_ft": round(self.elevation_ft, 1),
        }


@dataclass(frozen=True, slots=True)
class Station:
    """Normalized metadata for any weather station source.

    ``station_type`` is free-form (for example ``cimis`` or ``in_orchard``) so
    low-cost in-orchard sensors can live in the same catalog as CIMIS sites.
    ``connect_date``/``disconnect_date`` describe the period for which data is
    expected; a missing disconnect date means the station is ongoing.
    """

    station_id: str
    name: str
    latitude: float
    longitude: float
    elevation_m: float
    source: str = "unknown"
    station_type: str = "unknown"
    city: str | None = None
    county: str | None = None
    regional_office: str | None = None
    connect_date: date | None = None
    disconnect_date: date | None = None
    is_active: bool | None = None
    has_hourly: bool | None = None
    has_daily: bool | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        station_id = (self.station_id or "").strip()
        if not station_id:
            raise ValueError("station_id must not be empty")
        _require_coordinate("latitude", self.latitude, -90.0, 90.0)
        _require_coordinate("longitude", self.longitude, -180.0, 180.0)
        _require_finite("elevation_m", self.elevation_m)
        if not -500.0 <= float(self.elevation_m) <= 9000.0:
            raise ValueError("station elevation_m must be between -500 and 9000 metres")
        if (
            self.connect_date is not None
            and self.disconnect_date is not None
            and self.connect_date > self.disconnect_date
        ):
            raise ValueError("connect_date cannot be later than disconnect_date")

        object.__setattr__(self, "station_id", station_id)
        object.__setattr__(self, "name", (self.name or "").strip())
        object.__setattr__(self, "source", (self.source or "unknown").strip())
        object.__setattr__(self, "station_type", (self.station_type or "unknown").strip())

    @property
    def elevation_ft(self) -> float:
        return metres_to_feet(float(self.elevation_m))

    @property
    def label(self) -> str:
        return f"{self.station_id} {self.name}".strip()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.station_id,
            "name": self.name,
            "source": self.source,
            "type": self.station_type,
            "latitude": round(float(self.latitude), 6),
            "longitude": round(float(self.longitude), 6),
            "elevation_m": round(float(self.elevation_m), 1),
            "elevation_ft": round(self.elevation_ft, 1),
            "city": self.city,
            "county": self.county,
            "regional_office": self.regional_office,
            "connect_date": self.connect_date.isoformat() if self.connect_date else None,
            "disconnect_date": (
                self.disconnect_date.isoformat() if self.disconnect_date else None
            ),
            "is_active": self.is_active,
            "has_hourly": self.has_hourly,
            "has_daily": self.has_daily,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class HistoryWindow:
    """The historical baseline used to score station coverage."""

    start: date
    end: date
    days: int
    years: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "days": self.days,
            "years": self.years,
        }


@dataclass(frozen=True, slots=True)
class StationMatch:
    """A scored station plus the evidence behind its rank."""

    rank: int
    station: Station
    distance_km: float
    bearing_deg: float
    elevation_delta_m: float
    lapse_correction_c: float
    score: float
    score_components: Mapping[str, float]
    history_coverage: float | None
    quality: str
    warnings: tuple[str, ...]
    rationale: str

    @property
    def compass(self) -> str:
        return compass_point(self.bearing_deg)

    @property
    def distance_mi(self) -> float:
        return km_to_miles(self.distance_km)

    @property
    def elevation_delta_ft(self) -> float:
        return metres_to_feet(self.elevation_delta_m)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "station": self.station.to_dict(),
            "distance_km": round(self.distance_km, 2),
            "distance_mi": round(self.distance_mi, 2),
            "bearing_deg": round(self.bearing_deg, 1),
            "bearing": self.compass,
            "elevation_delta_m": round(self.elevation_delta_m, 1),
            "elevation_delta_ft": round(self.elevation_delta_ft, 1),
            "lapse_correction_c": round(self.lapse_correction_c, 2),
            "score": round(self.score, 4),
            "score_components": {
                key: round(value, 4) for key, value in self.score_components.items()
            },
            "history_coverage": (
                round(self.history_coverage, 4)
                if self.history_coverage is not None
                else None
            ),
            "quality": self.quality,
            "rationale": self.rationale,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True, slots=True)
class SelectionResult:
    """Complete, JSON-serializable output of one selection run."""

    location: OrchardLocation
    selected: tuple[StationMatch, ...]
    alternates: tuple[StationMatch, ...]
    config: SelectionConfig
    as_of: date
    history_window: HistoryWindow
    warnings: tuple[str, ...] = ()
    diagnostics: Mapping[str, Any] = field(default_factory=dict)
    generated_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc).replace(microsecond=0)
    )

    @property
    def station_ids(self) -> list[str]:
        """IDs ready to pass to the hourly/daily data-ingest modules."""
        return [match.station.station_id for match in self.selected]

    @property
    def summary(self) -> str:
        place = self.location.name or (
            f"{self.location.latitude:.4f}, {self.location.longitude:.4f}"
        )
        if not self.selected:
            return f"No suitable stations found for {place}."
        parts = []
        for match in self.selected:
            if abs(match.elevation_delta_m) < 1.0:
                elevation = "same elevation"
            else:
                direction = "lower" if match.elevation_delta_m < 0 else "higher"
                elevation = f"{abs(match.elevation_delta_m):.0f} m {direction}"
            parts.append(
                f"#{match.station.station_id} {match.station.name} "
                f"({match.distance_km:.1f} km {match.compass}, {elevation})"
            )
        return f"{len(self.selected)} stations selected for {place}: " + "; ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "module": "station_selection",
            "version": __version__,
            "generated_at": self.generated_at.isoformat(),
            "as_of": self.as_of.isoformat(),
            "location": self.location.to_dict(),
            "history_window": self.history_window.to_dict(),
            "config": self.config.to_dict(),
            "summary": self.summary,
            "selected_station_ids": self.station_ids,
            "selected": [match.to_dict() for match in self.selected],
            "alternates": [match.to_dict() for match in self.alternates],
            "warnings": list(self.warnings),
            "diagnostics": dict(self.diagnostics),
        }

    def to_json(self, indent: int = 2) -> str:
        import json

        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)
