#!/usr/bin/env python3
"""Run station selection for four California demo orchards.

    python examples/demo.py --as-of 2026-10-24

Prints a human-readable summary and writes the full JSON metadata for all
sites to ``examples/selected_stations.json``.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from station_selection import (  # noqa: E402
    OrchardLocation,
    SelectionConfig,
    StationCatalog,
    select_stations,
)

DEFAULT_CATALOG = ROOT / "data" / "cimis_stations.csv"
DEFAULT_OUTPUT = ROOT / "examples" / "selected_stations.json"

# Approximate town-centre coordinates, not surveyed orchard boundaries.
DEMO_SITES = [
    {
        "name": "Chico almond demo",
        "latitude": 39.7285,
        "longitude": -121.8375,
        "elevation_m": 60.0,
        "crop": "almond",
    },
    {
        "name": "Fresno stone fruit demo",
        "latitude": 36.7468,
        "longitude": -119.7726,
        "elevation_m": 94.0,
        "crop": "peach/nectarine",
    },
    {
        "name": "Santa Maria strawberry demo",
        "latitude": 34.9530,
        "longitude": -120.4357,
        "elevation_m": 66.0,
        "crop": "strawberry",
    },
    {
        "name": "Camino apple demo",
        "latitude": 38.7385,
        "longitude": -120.6697,
        "elevation_m": 1000.0,
        "crop": "apple/pear",
    },
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--as-of",
        type=date.fromisoformat,
        default=date.today(),
        help="end of the history window (default: today)",
    )
    parser.add_argument("--catalog", default=str(DEFAULT_CATALOG))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument(
        "--history-years",
        type=int,
        default=3,
        help="years of history the selected stations must cover (default: 3)",
    )
    args = parser.parse_args()

    catalog = StationCatalog.from_csv(args.catalog)
    config = SelectionConfig(history_years=args.history_years)

    payload = {
        "schema_version": "1.0",
        "as_of": args.as_of.isoformat(),
        "catalog": str(Path(args.catalog).resolve()),
        "catalog_station_count": len(catalog),
        "sites": [],
    }

    for site in DEMO_SITES:
        location = OrchardLocation(
            latitude=site["latitude"],
            longitude=site["longitude"],
            elevation_m=site["elevation_m"],
            name=site["name"],
        )
        result = select_stations(location, catalog, config, as_of=args.as_of)
        record = result.to_dict()
        record["crop"] = site["crop"]
        payload["sites"].append(record)

        print(result.summary)
        for match in result.selected:
            coverage = (
                f"{match.history_coverage:.0%}"
                if match.history_coverage is not None
                else "unknown"
            )
            if abs(match.elevation_delta_m) < 1.0:
                delta = "same elevation"
            else:
                delta = f"{match.elevation_delta_m:+.0f} m"
            print(
                f"  {match.rank}. #{match.station.station_id} "
                f"{match.station.name} [{match.quality}] "
                f"{match.distance_km:.1f} km {match.compass}, "
                f"{delta}, coverage {coverage}"
            )
        for warning in result.warnings:
            print(f"  ! {warning}")
        print()

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
