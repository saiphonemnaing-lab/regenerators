"""Command-line entry point for location and station selection."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any, Sequence

from .catalog import StationCatalog
from .config import SelectionConfig
from .models import OrchardLocation, SelectionResult
from .selector import InsufficientStationsError, select_stations
from .version import __version__

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_CATALOG = _REPO_ROOT / "data" / "cimis_stations.csv"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="select-stations",
        description=(
            "Select the most suitable weather stations for an orchard, "
            "using distance, elevation similarity and history coverage."
        ),
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--lat", type=float, required=True, help="orchard latitude (WGS84)")
    parser.add_argument("--lon", type=float, required=True, help="orchard longitude (WGS84)")
    parser.add_argument(
        "--elevation",
        type=float,
        required=True,
        help="orchard elevation (metres unless --elevation-unit ft)",
    )
    parser.add_argument(
        "--elevation-unit",
        choices=("m", "ft"),
        default="m",
        help="unit for --elevation (default: m)",
    )
    parser.add_argument("--name", default="", help="human-readable site name")
    parser.add_argument(
        "--catalog",
        default=str(_DEFAULT_CATALOG),
        help="station catalog CSV or JSON (default: bundled CIMIS snapshot)",
    )
    parser.add_argument(
        "--default-elevation-unit",
        choices=("m", "ft"),
        default=None,
        help="unit for a catalog with an unlabelled 'elevation' column",
    )
    parser.add_argument(
        "--as-of",
        type=date.fromisoformat,
        default=None,
        help="end of the history window, YYYY-MM-DD (default: today)",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="output format (default: text)",
    )
    parser.add_argument("--output", help="write output to this file instead of stdout")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit with status 3 if fewer than --count stations are found",
    )
    parser.add_argument(
        "--config",
        help="JSON file with SelectionConfig overrides (e.g. thresholds/weights)",
    )

    overrides = parser.add_argument_group("selection overrides")
    overrides.add_argument("--count", type=int, default=None, help="stations to select")
    overrides.add_argument("--history-years", type=int, default=None)
    overrides.add_argument("--max-distance-km", type=float, default=None)
    overrides.add_argument("--max-elevation-diff-m", type=float, default=None)
    overrides.add_argument("--min-separation-km", type=float, default=None)
    overrides.add_argument("--min-history-coverage", type=float, default=None)
    overrides.add_argument(
        "--active-only",
        action="store_true",
        default=None,
        help="exclude stations that are not currently active",
    )
    overrides.add_argument(
        "--require-hourly",
        action="store_true",
        default=None,
        help="exclude stations whose metadata says hourly data is unavailable",
    )
    overrides.add_argument(
        "--require-daily",
        action="store_true",
        default=None,
        help="exclude stations whose metadata says daily data is unavailable",
    )
    return parser


def _build_config(args: argparse.Namespace) -> SelectionConfig:
    values: dict[str, Any] = {}
    if args.config:
        payload = json.loads(Path(args.config).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("--config must contain a JSON object")
        values.update(payload)

    override_map = {
        "station_count": args.count,
        "history_years": args.history_years,
        "max_distance_km": args.max_distance_km,
        "max_elevation_diff_m": args.max_elevation_diff_m,
        "min_station_separation_km": args.min_separation_km,
        "min_history_coverage": args.min_history_coverage,
        "require_hourly": args.require_hourly,
        "require_daily": args.require_daily,
    }
    for key, value in override_map.items():
        if value is not None:
            values[key] = value
    if args.active_only:
        values["include_inactive"] = False

    config = SelectionConfig.from_dict(values)
    if args.strict:
        config = SelectionConfig.from_dict(
            {**config.to_dict(), "fail_if_fewer_than": config.station_count}
        )
    return config


def _load_catalog(path: str, default_elevation_unit: str | None) -> StationCatalog:
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(
            f"catalog not found: {target}. Run scripts/build_cimis_catalog.py "
            "or pass --catalog."
        )
    suffix = target.suffix.lower()
    if suffix == ".json":
        return StationCatalog.from_json(
            target, default_elevation_unit=default_elevation_unit
        )
    if suffix in {".csv", ".txt"}:
        return StationCatalog.from_csv(
            target, default_elevation_unit=default_elevation_unit
        )
    raise ValueError(f"unsupported catalog format: {target.suffix}")


def _format_text(result: SelectionResult) -> str:
    location = result.location
    catalog = result.diagnostics
    lines = [
        f"Orchard: {location.name or '(unnamed)'}",
        f"Coordinates: {location.latitude:.6f}, {location.longitude:.6f} | "
        f"elevation {location.elevation_m:.1f} m ({location.elevation_ft:.1f} ft)",
        f"History window: {result.history_window.start.isoformat()} to "
        f"{result.history_window.end.isoformat()} "
        f"({result.history_window.days} days, {result.history_window.years} years)",
        f"Catalog: {catalog.get('catalog_total', 0)} stations | "
        f"{catalog.get('catalog_active', 0)} active, "
        f"{catalog.get('catalog_inactive', 0)} inactive, "
        f"{catalog.get('catalog_active_unknown', 0)} unknown status | "
        f"{catalog.get('eligible', 0)} eligible",
        "",
    ]

    if result.selected:
        lines.append(f"Selected stations ({len(result.selected)}):")
        for match in result.selected:
            lines.extend(_format_match(match))
    else:
        lines.append("Selected stations: none")

    if result.alternates:
        lines.append("")
        lines.append(f"Alternates ({len(result.alternates)}):")
        for match in result.alternates:
            lines.extend(_format_match(match, detailed=False))

    if result.warnings:
        lines.append("")
        lines.append("Warnings:")
        lines.extend(f"  - {warning}" for warning in result.warnings)

    rejected = catalog.get("rejected") or {}
    if rejected:
        lines.append("")
        lines.append(
            "Rejection checks triggered (stations can fail more than one): "
            + ", ".join(f"{key}={value}" for key, value in sorted(rejected.items()))
        )

    return "\n".join(lines)


def _format_match(match: Any, detailed: bool = True) -> list[str]:
    station = match.station
    nearest = (
        f"{match.distance_km:.1f} km {match.compass} "
        f"({match.distance_mi:.1f} mi)"
    )
    if abs(match.elevation_delta_m) < 1.0:
        delta_text = "same elevation as orchard"
    else:
        direction = "lower" if match.elevation_delta_m < 0 else "higher"
        delta_text = f"{abs(match.elevation_delta_m):.0f} m {direction}"
    elevation = (
        f"{station.elevation_m:.1f} m ({station.elevation_ft:.0f} ft), {delta_text}"
    )
    coverage = (
        f"{match.history_coverage:.0%}"
        if match.history_coverage is not None
        else "unknown"
    )
    lines = [
        f"  {match.rank}. #{station.station_id} {station.name} "
        f"[{match.quality}] score {match.score:.3f}",
        f"     {nearest} | {elevation} | history {coverage}",
    ]
    if detailed:
        county = f" | {station.county} County" if station.county else ""
        lines.append(
            f"     lapse correction {match.lapse_correction_c:+.2f} C | "
            f"source {station.source}{county}"
        )
        lines.append(f"     why: {match.rationale}")
        for warning in match.warnings:
            lines.append(f"     ! {warning}")
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        config = _build_config(args)
        catalog = _load_catalog(args.catalog, args.default_elevation_unit)
        if args.elevation_unit == "ft":
            location = OrchardLocation.from_feet(
                latitude=args.lat,
                longitude=args.lon,
                elevation_ft=args.elevation,
                name=args.name,
            )
        else:
            location = OrchardLocation(
                latitude=args.lat,
                longitude=args.lon,
                elevation_m=args.elevation,
                name=args.name,
            )
        result = select_stations(location, catalog, config, as_of=args.as_of)
    except InsufficientStationsError as exc:
        _emit(exc.result, args)
        print(f"error: {exc}", file=sys.stderr)
        return 3
    except (OSError, ValueError, TypeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    _emit(result, args)
    return 0


def _emit(result: SelectionResult, args: argparse.Namespace) -> None:
    if args.format == "json":
        rendered = result.to_json()
    else:
        rendered = _format_text(result)

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered + "\n", encoding="utf-8")
        print(
            f"Wrote {args.format} output to {output_path} "
            f"({len(result.selected)} station(s))",
            file=sys.stderr,
        )
    else:
        print(rendered)


if __name__ == "__main__":
    raise SystemExit(main())
