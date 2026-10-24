"""Run Member 1's station selection against the bundled CIMIS catalog.

From the repository root::

    python member1/demo.py

Or select stations for your own orchard::

    python member1/demo.py --lat 38.54 --lon -121.74 --elevation 20 \
        --name "My orchard"
"""

from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path

import pandas as pd

# Use the flat module next to this script, not the repository's package.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from station_selection import (  # noqa: E402
    StationSelectionWarning,
    select_stations,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = REPO_ROOT / "data" / "cimis_stations.csv"

DEMO_SITES: dict[str, tuple[float, float, float]] = {
    "Chico almond": (39.7285, -121.8375, 60.0),
    "Fresno stone fruit": (36.7378, -119.7871, 94.0),
    "Santa Maria strawberry": (34.9530, -120.4357, 66.0),
    "Camino apple": (38.7385, -120.6747, 954.0),
}

DISPLAY_COLUMNS = [
    "station_id",
    "station_name",
    "distance_km",
    "elevation_diff_m",
    "suitability_score",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Select the best weather stations for an orchard."
    )
    parser.add_argument("--lat", type=float, help="orchard latitude")
    parser.add_argument("--lon", type=float, help="orchard longitude")
    parser.add_argument(
        "--elevation", type=float, help="orchard elevation in metres"
    )
    parser.add_argument(
        "--name", default="Custom site", help="label shown in the output"
    )
    parser.add_argument(
        "--catalog",
        type=Path,
        default=DEFAULT_CATALOG,
        help=f"station catalog CSV (default: {DEFAULT_CATALOG})",
    )
    return parser.parse_args()


def show_site(
    name: str,
    latitude: float,
    longitude: float,
    elevation: float,
    stations: pd.DataFrame,
) -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", StationSelectionWarning)
        selected = select_stations(
            latitude=latitude,
            longitude=longitude,
            elevation=elevation,
            stations_df=stations,
        )

    print(f"\n{name}")
    print(f"  {latitude:.4f}, {longitude:.4f}, {elevation:.0f} m")
    print(selected[DISPLAY_COLUMNS].to_string(index=False))
    for warning in caught:
        for line in str(warning.message).splitlines():
            print(f"  warning: {line}")


def main() -> None:
    args = parse_args()

    provided = [args.lat, args.lon, args.elevation]
    if any(value is not None for value in provided):
        if any(value is None for value in provided):
            raise SystemExit(
                "Provide all three of --lat, --lon and --elevation, or none "
                "to run the built-in demo."
            )
        sites = {
            args.name: (args.lat, args.lon, args.elevation)
        }
    else:
        sites = DEMO_SITES

    if not args.catalog.exists():
        raise SystemExit(f"Catalog not found: {args.catalog}")

    stations = pd.read_csv(args.catalog)
    for name, (latitude, longitude, elevation) in sites.items():
        show_site(name, latitude, longitude, elevation, stations)


if __name__ == "__main__":
    main()
