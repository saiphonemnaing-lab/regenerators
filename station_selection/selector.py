"""Select the most suitable weather stations for an orchard.

The selector combines three signals:

1. great-circle distance from the orchard,
2. absolute elevation difference (a proxy for microclimate),
3. coverage of the required historical baseline.

Hard caps reject implausible candidates. Remaining stations are scored with
exponential similarity curves, and a minimum-separation rule prevents three
sensors on the same ranch from being returned as if they were independent.
The result always explains its choices and warns when the best available
station is still a poor microclimate match.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from .catalog import StationCatalog
from .config import SelectionConfig
from .geo import compass_point, haversine_km, initial_bearing_deg
from .models import (
    HistoryWindow,
    OrchardLocation,
    SelectionResult,
    Station,
    StationMatch,
)

_QUALITY_ORDER = {"preferred": 0, "acceptable": 1, "marginal": 2}

_REJECTION_LABELS = {
    "distance_cap": "beyond maximum distance",
    "elevation_cap": "beyond maximum elevation difference",
    "no_history_overlap": "no overlap with the history window",
    "history_below_min": "history coverage below the minimum",
    "inactive": "station is inactive",
    "missing_hourly": "hourly data unavailable",
    "missing_daily": "daily data unavailable",
}


class InsufficientStationsError(RuntimeError):
    """Raised only when ``fail_if_fewer_than`` is configured above zero."""

    def __init__(self, result: SelectionResult) -> None:
        self.result = result
        super().__init__(
            f"Only {len(result.selected)} station(s) met the constraints; "
            f"{result.config.fail_if_fewer_than} required."
        )


@dataclass(frozen=True, slots=True)
class _Candidate:
    station: Station
    distance_km: float
    bearing_deg: float
    elevation_delta_m: float
    lapse_correction_c: float
    score: float
    score_components: dict[str, float]
    history_coverage: float | None
    quality: str
    warnings: tuple[str, ...]
    reasons: tuple[str, ...]
    rationale: str

    @property
    def sort_key(self) -> tuple[float, float, float, str]:
        return (
            -self.score,
            self.distance_km,
            abs(self.elevation_delta_m),
            self.station.station_id,
        )


def select_stations(
    location: OrchardLocation,
    stations: StationCatalog | Iterable[Station],
    config: SelectionConfig | None = None,
    as_of: date | None = None,
) -> SelectionResult:
    """Return the best stations for ``location``.

    Parameters
    ----------
    location:
        Orchard coordinates and elevation.
    stations:
        A :class:`StationCatalog` or any iterable of :class:`Station`.
    config:
        Thresholds and weights; defaults to :class:`SelectionConfig`.
    as_of:
        End of the historical window (defaults to today). The window starts
        ``config.history_years`` years earlier, inclusive.
    """
    config = config or SelectionConfig()
    as_of = as_of or date.today()
    if not isinstance(as_of, date):
        raise TypeError("as_of must be a datetime.date")

    catalog = (
        stations
        if isinstance(stations, StationCatalog)
        else StationCatalog(tuple(stations))
    )
    window = _history_window(as_of, config.history_years)

    candidates = [
        _evaluate(location, station, window, config) for station in catalog.stations
    ]
    eligible = [candidate for candidate in candidates if not candidate.reasons]
    ranked = _rank_candidates(eligible, config)

    warnings: list[str] = []

    selected, deferred, separation_relaxed = _select_diverse(ranked, config)
    if separation_relaxed:
        warnings.append(
            f"Minimum station separation of {config.min_station_separation_km:g} km "
            "was relaxed because too few independent stations were available."
        )
    elif deferred:
        warnings.append(
            f"Near-duplicate station(s) within {config.min_station_separation_km:g} km "
            "were skipped in favour of spatially independent stations."
        )

    alternates = _pick_alternates(ranked, selected, config)

    matches = tuple(
        _to_match(candidate, rank)
        for rank, candidate in enumerate(selected, start=1)
    )
    alternate_matches = tuple(
        _to_match(candidate, rank)
        for rank, candidate in enumerate(
            alternates, start=len(matches) + 1
        )
    )

    if len(matches) < config.station_count:
        warnings.append(
            f"Only {len(matches)} of {config.station_count} requested stations met "
            "the eligibility constraints; consider relaxing the caps or adding "
            "in-orchard stations."
        )
    if not matches:
        warnings.append(
            "No station passed the distance, elevation and history constraints."
        )

    marginal = [match for match in matches if match.quality == "marginal"]
    if marginal:
        warnings.append(
            f"{len(marginal)} selected station(s) are marginal: "
            + ", ".join(match.station.label for match in marginal)
            + "."
        )

    inactive = [
        match
        for match in matches
        if match.station.is_active is False
    ]
    if inactive:
        warnings.append(
            "Selected station(s) not currently active: "
            + ", ".join(match.station.label for match in inactive)
            + ". They can support historical baselines but not live updates."
        )

    for catalog_warning in catalog.warnings[:5]:
        warnings.append(f"Catalog: {catalog_warning}")
    if len(catalog.warnings) > 5:
        warnings.append(
            f"Catalog: {len(catalog.warnings) - 5} additional metadata warning(s) omitted."
        )

    result = SelectionResult(
        location=location,
        selected=matches,
        alternates=alternate_matches,
        config=config,
        as_of=as_of,
        history_window=window,
        warnings=tuple(warnings),
        diagnostics=_diagnostics(
            catalog,
            candidates,
            matches,
            alternate_matches,
            separation_relaxed,
        ),
    )

    if config.fail_if_fewer_than and len(matches) < config.fail_if_fewer_than:
        raise InsufficientStationsError(result)
    return result


def select_for_coordinates(
    latitude: float,
    longitude: float,
    elevation_m: float,
    stations: StationCatalog | Iterable[Station],
    config: SelectionConfig | None = None,
    as_of: date | None = None,
    name: str = "",
) -> SelectionResult:
    """Convenience wrapper for the common "coordinates + elevation" call."""
    return select_stations(
        OrchardLocation(
            latitude=latitude,
            longitude=longitude,
            elevation_m=elevation_m,
            name=name,
        ),
        stations,
        config,
        as_of,
    )


def _history_window(as_of: date, years: int) -> HistoryWindow:
    try:
        start = as_of.replace(year=as_of.year - years)
    except ValueError:  # 29 February -> 28 February
        start = date(as_of.year - years, 2, 28)
    start += timedelta(days=1)
    days = (as_of - start).days + 1
    return HistoryWindow(start=start, end=as_of, days=days, years=years)


def _coverage(station: Station, window: HistoryWindow) -> float | None:
    """Fraction of the history window covered by the station's service dates.

    Returns ``None`` when the catalog has no service dates, rather than
    pretending the station has complete coverage.
    """
    if station.connect_date is None and station.disconnect_date is None:
        return None
    start = max(window.start, station.connect_date or window.start)
    end = min(window.end, station.disconnect_date or window.end)
    if end < start:
        return 0.0
    covered_days = (end - start).days + 1
    return min(1.0, covered_days / window.days)


def _grade(value: float, preferred: float, acceptable: float) -> str:
    if value <= preferred:
        return "preferred"
    if value <= acceptable:
        return "acceptable"
    return "marginal"


def _grade_history(value: float, preferred: float, acceptable: float) -> str:
    if value >= preferred:
        return "preferred"
    if value >= acceptable:
        return "acceptable"
    return "marginal"


def _evaluate(
    location: OrchardLocation,
    station: Station,
    window: HistoryWindow,
    config: SelectionConfig,
) -> _Candidate:
    distance = haversine_km(
        location.latitude,
        location.longitude,
        station.latitude,
        station.longitude,
    )
    bearing = initial_bearing_deg(
        location.latitude,
        location.longitude,
        station.latitude,
        station.longitude,
    )
    delta_elevation = station.elevation_m - location.elevation_m
    lapse_correction = delta_elevation * config.lapse_rate_c_per_km / 1000.0
    coverage = _coverage(station, window)

    elevation_excess = max(
        0.0, abs(delta_elevation) - config.elevation_tolerance_m
    )
    components: dict[str, float] = {
        "distance": math.exp(-distance / config.distance_decay_km),
        # Elevation differences inside the tolerance band are considered
        # equivalent; only the excess drives the penalty.
        "elevation": math.exp(-elevation_excess / config.elevation_decay_m),
    }
    weights = {
        "distance": config.weight_distance,
        "elevation": config.weight_elevation,
    }
    if coverage is not None:
        components["history"] = coverage
        weights["history"] = config.weight_history

    total_weight = sum(weights.values())
    score = sum(
        weights[name] * components[name] for name in components
    ) / total_weight

    quality_grades = [
        _grade(
            distance,
            config.preferred_distance_km,
            config.acceptable_distance_km,
        ),
        _grade(
            abs(delta_elevation),
            config.preferred_elevation_diff_m,
            config.acceptable_elevation_diff_m,
        ),
    ]
    if coverage is not None:
        quality_grades.append(
            _grade_history(
                coverage,
                config.preferred_history_coverage,
                config.acceptable_history_coverage,
            )
        )
    quality = max(quality_grades, key=lambda grade: _QUALITY_ORDER[grade])

    reasons: list[str] = []
    if distance > config.max_distance_km:
        reasons.append("distance_cap")
    if abs(delta_elevation) > config.max_elevation_diff_m:
        reasons.append("elevation_cap")
    if coverage is not None:
        if coverage <= 0.0:
            reasons.append("no_history_overlap")
        elif coverage < config.min_history_coverage:
            reasons.append("history_below_min")
    if not config.include_inactive and station.is_active is False:
        reasons.append("inactive")
    if config.require_hourly and station.has_hourly is False:
        reasons.append("missing_hourly")
    if config.require_daily and station.has_daily is False:
        reasons.append("missing_daily")

    warnings: list[str] = []
    if distance > config.acceptable_distance_km:
        warnings.append(
            f"distance {distance:.1f} km exceeds the "
            f"{config.acceptable_distance_km:g} km acceptable radius"
        )
    if abs(delta_elevation) > config.acceptable_elevation_diff_m:
        warnings.append(
            f"elevation difference {abs(delta_elevation):.0f} m exceeds the "
            f"{config.acceptable_elevation_diff_m:g} m acceptable band"
        )
    if coverage is not None and coverage < config.acceptable_history_coverage:
        warnings.append(
            f"station covers {coverage:.0%} of the {window.years}-year history "
            f"window (below {config.acceptable_history_coverage:.0%})"
        )
    if coverage is None:
        warnings.append(
            "history coverage is unknown because the catalog has no service dates"
        )
    if station.is_active is False:
        warnings.append(
            "station is not currently active; suitable for historical baselines "
            "but not live or forecast updates"
        )
    if station.has_hourly is False:
        warnings.append(
            "catalog metadata says hourly data is unavailable (needed for chill hours)"
        )
    if station.has_daily is False:
        warnings.append(
            "catalog metadata says daily data is unavailable (needed for GDD)"
        )
    if abs(lapse_correction) >= 1.0:
        warnings.append(
            f"lapse correction is {lapse_correction:+.1f} C; temperature inversions "
            "can invalidate a simple elevation adjustment"
        )

    if abs(delta_elevation) < 1.0:
        elevation_phrase = "same elevation as orchard"
    else:
        direction = "lower" if delta_elevation < 0 else "higher"
        elevation_phrase = f"{abs(delta_elevation):.0f} m {direction} than orchard"
    rationale_parts = [
        f"{distance:.1f} km {compass_point(bearing)}",
        elevation_phrase,
    ]
    if coverage is not None:
        rationale_parts.append(
            f"{coverage:.0%} of {window.years}-year history window"
        )
    rationale_parts.append(f"similarity {score:.2f}")

    return _Candidate(
        station=station,
        distance_km=distance,
        bearing_deg=bearing,
        elevation_delta_m=delta_elevation,
        lapse_correction_c=lapse_correction,
        score=score,
        score_components=components,
        history_coverage=coverage,
        quality=quality,
        warnings=tuple(warnings),
        reasons=tuple(reasons),
        rationale="; ".join(rationale_parts),
    )


def _separated(
    candidate: _Candidate,
    chosen: list[_Candidate],
    minimum_km: float,
) -> bool:
    if minimum_km <= 0:
        return True
    return all(
        haversine_km(
            candidate.station.latitude,
            candidate.station.longitude,
            other.station.latitude,
            other.station.longitude,
        )
        >= minimum_km
        for other in chosen
    )


def _rank_candidates(
    eligible: list[_Candidate],
    config: SelectionConfig,
) -> list[_Candidate]:
    """Rank active stations ahead of inactive ones when configured.

    Score alone can favour a recently closed station over a live one many
    kilometres away. For current-season chill/GDD accumulation that trade-off
    is wrong, so active stations form a first tier; inactive stations remain
    eligible as fallbacks and are clearly flagged in the result.
    """
    ordered = sorted(eligible, key=lambda candidate: candidate.sort_key)
    if not config.prefer_active:
        return ordered
    active = [candidate for candidate in ordered if candidate.station.is_active is True]
    other = [candidate for candidate in ordered if candidate.station.is_active is not True]
    return active + other


def _select_diverse(
    ranked: list[_Candidate],
    config: SelectionConfig,
) -> tuple[list[_Candidate], list[_Candidate], bool]:
    selected: list[_Candidate] = []
    deferred: list[_Candidate] = []
    relaxed = False

    for candidate in ranked:
        if len(selected) >= config.station_count:
            break
        if _separated(candidate, selected, config.min_station_separation_km):
            selected.append(candidate)
        else:
            deferred.append(candidate)

    if len(selected) < config.station_count and deferred:
        relaxed = True
        for candidate in deferred:
            if len(selected) >= config.station_count:
                break
            selected.append(candidate)

    return selected, deferred, relaxed


def _pick_alternates(
    ranked: list[_Candidate],
    selected: list[_Candidate],
    config: SelectionConfig,
) -> list[_Candidate]:
    if config.alternate_count <= 0:
        return []

    selected_ids = {candidate.station.station_id for candidate in selected}
    remaining = [
        candidate
        for candidate in ranked
        if candidate.station.station_id not in selected_ids
    ]

    alternates: list[_Candidate] = []
    for candidate in remaining:
        if len(alternates) >= config.alternate_count:
            break
        if _separated(
            candidate,
            selected + alternates,
            config.min_station_separation_km,
        ):
            alternates.append(candidate)

    if len(alternates) < config.alternate_count:
        alternate_ids = {candidate.station.station_id for candidate in alternates}
        for candidate in remaining:
            if len(alternates) >= config.alternate_count:
                break
            if candidate.station.station_id in alternate_ids:
                continue
            alternates.append(candidate)

    return alternates


def _to_match(candidate: _Candidate, rank: int) -> StationMatch:
    return StationMatch(
        rank=rank,
        station=candidate.station,
        distance_km=candidate.distance_km,
        bearing_deg=candidate.bearing_deg,
        elevation_delta_m=candidate.elevation_delta_m,
        lapse_correction_c=candidate.lapse_correction_c,
        score=candidate.score,
        score_components=candidate.score_components,
        history_coverage=candidate.history_coverage,
        quality=candidate.quality,
        warnings=candidate.warnings,
        rationale=candidate.rationale,
    )


def _diagnostics(
    catalog: StationCatalog,
    candidates: list[_Candidate],
    selected: tuple[StationMatch, ...],
    alternates: tuple[StationMatch, ...],
    separation_relaxed: bool,
) -> dict[str, Any]:
    rejected: dict[str, int] = {}
    for candidate in candidates:
        for reason in candidate.reasons:
            rejected[reason] = rejected.get(reason, 0) + 1

    selected_ids = {match.station.station_id for match in selected}
    alternate_ids = {match.station.station_id for match in alternates}

    nearest = []
    for candidate in sorted(candidates, key=lambda item: item.distance_km)[:5]:
        station_id = candidate.station.station_id
        if station_id in selected_ids:
            status = "selected"
        elif station_id in alternate_ids:
            status = "alternate"
        elif candidate.reasons:
            status = "excluded"
        else:
            status = "eligible"
        nearest.append(
            {
                "station_id": station_id,
                "name": candidate.station.name,
                "distance_km": round(candidate.distance_km, 2),
                "elevation_delta_m": round(candidate.elevation_delta_m, 1),
                "status": status,
                "reasons": [
                    {
                        "code": reason,
                        "message": _REJECTION_LABELS.get(reason, reason),
                    }
                    for reason in candidate.reasons
                ],
            }
        )

    spread = None
    if len(selected) >= 2:
        pairwise = [
            haversine_km(
                left.station.latitude,
                left.station.longitude,
                right.station.latitude,
                right.station.longitude,
            )
            for index, left in enumerate(selected)
            for right in selected[index + 1 :]
        ]
        elevations = [match.station.elevation_m for match in selected]
        spread = {
            "max_pairwise_distance_km": round(max(pairwise), 2),
            "elevation_range_m": round(max(elevations) - min(elevations), 1),
        }

    return {
        "catalog_total": len(catalog),
        "catalog_active": sum(
            1 for station in catalog if station.is_active is True
        ),
        "catalog_inactive": sum(
            1 for station in catalog if station.is_active is False
        ),
        "catalog_active_unknown": sum(
            1 for station in catalog if station.is_active is None
        ),
        "eligible": sum(1 for candidate in candidates if not candidate.reasons),
        "selected_currently_active": sum(
            1 for match in selected if match.station.is_active is True
        ),
        "selected_spread": spread,
        "rejected": rejected,
        "separation_relaxed": separation_relaxed,
        "nearest": nearest,
    }
