"""Load heterogeneous station metadata into one normalized catalog.

The primary source is the public CIMIS station list, but the same loader
accepts low-cost in-orchard stations as long as the CSV/JSON provides
coordinates and an elevation with an explicit unit.
"""

from __future__ import annotations

import csv
import json
import math
import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .geo import feet_to_metres
from .models import Station

# Column aliases are matched after lower-casing and removing punctuation, so
# "Elevation (ft)", "elevation_ft" and "ElevationFt" all collapse to
# "elevationft".
_ALIASES: dict[str, tuple[str, ...]] = {
    "station_id": (
        "stationid",
        "stationnbr",
        "stationnumber",
        "stationno",
        "station",
        "id",
    ),
    "name": ("name", "stationname"),
    "latitude": ("latitude", "lat"),
    "longitude": ("longitude", "lon", "lng", "long"),
    "elevation_m": ("elevationm", "elevm", "elevationmeters", "elevationmetres"),
    "elevation_ft": ("elevationft", "elevft", "elevationfeet"),
    "bare_elevation": ("elevation", "elev"),
    "connect_date": ("connectdate", "startdate", "historystart", "datastart"),
    "disconnect_date": ("disconnectdate", "enddate", "historyend", "dataend"),
    "is_active": ("isactive", "active"),
    "has_hourly": ("hashourly", "hourly", "hourlydata"),
    "has_daily": ("hasdaily", "daily", "dailydata"),
    "source": ("source", "network", "provider"),
    "station_type": ("stationtype", "type"),
    "city": ("city", "town"),
    "county": ("county",),
    "regional_office": ("regionaloffice", "region"),
    "ground_cover": ("groundcover",),
    "is_eto_station": ("isetostation", "iseto"),
}

_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d")


def _normalize_key(key: object) -> str:
    return "".join(character for character in str(key).lower() if character.isalnum())


def _lookup(row: Mapping[str, Any], field_name: str) -> Any:
    for alias in _ALIASES[field_name]:
        if alias in row:
            value = row[alias]
            if value is not None and str(value).strip() != "":
                return value
    return None


def _parse_float(value: Any, field_name: str) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    # Tolerate unit suffixes such as "340 ft" or "104 m".
    text = re.sub(r"\s*(ft|feet|m|meters|metres)$", "", text, flags=re.IGNORECASE).strip()
    try:
        number = float(text)
    except ValueError as exc:
        raise ValueError(f"invalid {field_name}: {value!r}") from exc
    if not math.isfinite(number):
        raise ValueError(f"invalid {field_name}: {value!r}")
    return number


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    for date_format in _DATE_FORMATS:
        try:
            return datetime.strptime(text, date_format).date()
        except ValueError:
            continue
    raise ValueError(f"unrecognized date: {value!r}")


def _parse_optional_bool(value: Any, field_name: str) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if not text:
        return None
    if text in {"true", "t", "yes", "y", "1", "active", "open"}:
        return True
    if text in {"false", "f", "no", "n", "0", "inactive", "closed"}:
        return False
    raise ValueError(f"invalid boolean for {field_name}: {value!r}")


@dataclass(frozen=True, slots=True)
class StationCatalog:
    """A validated, de-duplicated set of candidate stations."""

    stations: tuple[Station, ...] = ()
    warnings: tuple[str, ...] = ()
    source: str = "unknown"

    def __len__(self) -> int:
        return len(self.stations)

    def __iter__(self) -> Iterator[Station]:
        return iter(self.stations)

    @property
    def station_ids(self) -> list[str]:
        return [station.station_id for station in self.stations]

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "count": len(self.stations),
            "warnings": list(self.warnings),
            "stations": [station.to_dict() for station in self.stations],
        }

    @classmethod
    def from_csv(
        cls,
        path: str | Path,
        *,
        default_elevation_unit: str | None = None,
    ) -> "StationCatalog":
        """Load a catalog from CSV.

        ``default_elevation_unit`` (``"m"`` or ``"ft"``) is only required when
        the file has an unlabelled ``elevation`` column. An explicit
        ``elevation_m`` or ``elevation_ft`` column is always preferred.
        """
        path = Path(path)
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            records = list(csv.DictReader(handle))
        return cls.from_records(
            records,
            source=str(path),
            default_elevation_unit=default_elevation_unit,
        )

    @classmethod
    def from_json(
        cls,
        path: str | Path,
        *,
        default_elevation_unit: str | None = None,
    ) -> "StationCatalog":
        """Load a catalog from JSON (either a list or ``{"stations": [...]}``)."""
        path = Path(path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, Mapping):
            records = payload.get("stations")
        else:
            records = payload
        if not isinstance(records, list):
            raise ValueError(
                f"{path}: expected a JSON list or an object with a 'stations' list"
            )
        return cls.from_records(
            records,
            source=str(path),
            default_elevation_unit=default_elevation_unit,
        )

    @classmethod
    def from_records(
        cls,
        records: Iterable[Mapping[str, Any]],
        *,
        source: str = "unknown",
        default_elevation_unit: str | None = None,
    ) -> "StationCatalog":
        """Build a catalog from normalized or alias-named records."""
        if default_elevation_unit not in (None, "m", "ft"):
            raise ValueError("default_elevation_unit must be 'm', 'ft' or None")

        stations: list[Station] = []
        warnings: list[str] = []
        seen: dict[str, int] = {}

        for index, original in enumerate(records, start=1):
            row = {
                _normalize_key(key): value
                for key, value in original.items()
                if key is not None
            }
            label = f"record {index}"

            try:
                station_id = _lookup(row, "station_id")
                if station_id is None:
                    raise ValueError("missing station id")
                station_id = str(station_id).strip()

                latitude = _parse_float(_lookup(row, "latitude"), "latitude")
                longitude = _parse_float(_lookup(row, "longitude"), "longitude")
                if latitude is None or longitude is None:
                    raise ValueError("missing latitude/longitude")

                elevation_m = _parse_float(_lookup(row, "elevation_m"), "elevation_m")
                elevation_ft = _parse_float(_lookup(row, "elevation_ft"), "elevation_ft")
                if elevation_m is None and elevation_ft is None:
                    bare = _parse_float(_lookup(row, "bare_elevation"), "elevation")
                    if bare is None:
                        raise ValueError("missing elevation")
                    if default_elevation_unit == "m":
                        elevation_m = bare
                    elif default_elevation_unit == "ft":
                        elevation_ft = bare
                    else:
                        raise ValueError(
                            "unlabelled 'elevation' column: pass "
                            "default_elevation_unit='m' or 'ft'"
                        )
                if elevation_m is None:
                    assert elevation_ft is not None
                    elevation_m = feet_to_metres(elevation_ft)

                raw_source = _lookup(row, "source")
                source_name = str(raw_source).strip() if raw_source else "unknown"
                raw_type = _lookup(row, "station_type")
                station_type = (
                    str(raw_type).strip()
                    if raw_type
                    else ("unknown" if source_name == "unknown" else source_name.lower())
                )

                metadata: dict[str, Any] = {}
                ground_cover = _lookup(row, "ground_cover")
                if ground_cover:
                    metadata["ground_cover"] = str(ground_cover).strip()
                is_eto = _parse_optional_bool(
                    _lookup(row, "is_eto_station"), "is_eto_station"
                )
                if is_eto is not None:
                    metadata["is_eto_station"] = is_eto

                station = Station(
                    station_id=station_id,
                    name=str(_lookup(row, "name") or "").strip(),
                    latitude=float(latitude),
                    longitude=float(longitude),
                    elevation_m=float(elevation_m),
                    source=source_name,
                    station_type=station_type,
                    city=_optional_text(_lookup(row, "city")),
                    county=_optional_text(_lookup(row, "county")),
                    regional_office=_optional_text(_lookup(row, "regional_office")),
                    connect_date=_parse_date(_lookup(row, "connect_date")),
                    disconnect_date=_parse_date(_lookup(row, "disconnect_date")),
                    is_active=_parse_optional_bool(
                        _lookup(row, "is_active"), "is_active"
                    ),
                    has_hourly=_parse_optional_bool(
                        _lookup(row, "has_hourly"), "has_hourly"
                    ),
                    has_daily=_parse_optional_bool(
                        _lookup(row, "has_daily"), "has_daily"
                    ),
                    metadata=metadata,
                )
            except (TypeError, ValueError) as exc:
                warnings.append(f"{label}: skipped ({exc})")
                continue

            if station.station_id in seen:
                warnings.append(
                    f"{label}: skipped duplicate station_id {station.station_id!r} "
                    f"(first seen at record {seen[station.station_id]})"
                )
                continue
            seen[station.station_id] = index
            stations.append(station)

        stations.sort(key=_station_sort_key)
        return cls(
            stations=tuple(stations),
            warnings=tuple(warnings),
            source=source,
        )


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _station_sort_key(station: Station) -> tuple[int, str]:
    """Sort numerically when IDs are numeric, otherwise alphabetically."""
    try:
        return (0, f"{int(station.station_id):020d}")
    except ValueError:
        return (1, station.station_id.lower())
