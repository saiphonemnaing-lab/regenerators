#!/usr/bin/env python3
"""Download the public CIMIS station list and write a normalized catalog.

The CIMIS station metadata endpoint is public and does not require an API key:

    https://et.water.ca.gov/api/station

The script writes two files:

* ``data/cimis_stations_raw.json`` -- untouched API payload plus provenance.
* ``data/cimis_stations.csv``     -- one row per station with decimal
  coordinates, elevation in both metres and feet, and normalized dates.

Run from the repository root:

    python scripts/build_cimis_catalog.py
"""

from __future__ import annotations

import csv
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
SOURCE_URL = "https://et.water.ca.gov/api/station"
USER_AGENT = "orchard-station-selection/0.1 (+CIMIS station catalog builder)"

CSV_COLUMNS = [
    "station_id",
    "name",
    "city",
    "county",
    "regional_office",
    "latitude",
    "longitude",
    "elevation_m",
    "elevation_ft",
    "connect_date",
    "disconnect_date",
    "is_active",
    "is_eto_station",
    "ground_cover",
    "source",
]

# CIMIS uses 12/31/2050 as its "no planned disconnect" sentinel.
DISCONNECT_SENTINEL = date(2050, 12, 31)
FEET_PER_METRE = 3.280839895013123


def parse_decimal(value: object) -> float:
    """Parse a CIMIS coordinate such as ``36º48'52N / 36.814444``."""
    if value is None:
        raise ValueError("missing coordinate")
    text = str(value).strip()
    if not text:
        raise ValueError("empty coordinate")
    if "/" in text:
        text = text.rsplit("/", 1)[1].strip()
    return float(text)


def parse_date(value: object) -> date | None:
    """Parse the date formats used by CIMIS."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unrecognized date: {value!r}")


def parse_bool(value: object) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "t", "yes", "y", "1", "active"}:
        return True
    if text in {"false", "f", "no", "n", "0", "inactive"}:
        return False
    raise ValueError(f"unrecognized boolean: {value!r}")


def clean_station(raw: dict[str, object]) -> dict[str, object]:
    station_id = str(raw.get("StationNbr", "")).strip()
    if not station_id:
        raise ValueError("station has no number")

    elevation_ft = float(str(raw.get("Elevation", "")).strip())
    disconnect = parse_date(raw.get("DisconnectDate"))
    if disconnect == DISCONNECT_SENTINEL:
        disconnect = None

    return {
        "station_id": station_id,
        "name": str(raw.get("Name", "")).strip(),
        "city": str(raw.get("City", "") or "").strip(),
        "county": str(raw.get("County", "") or "").strip(),
        "regional_office": str(raw.get("RegionalOffice", "") or "").strip(),
        "latitude": round(parse_decimal(raw.get("HmsLatitude")), 6),
        "longitude": round(parse_decimal(raw.get("HmsLongitude")), 6),
        "elevation_m": round(elevation_ft / FEET_PER_METRE, 1),
        "elevation_ft": round(elevation_ft, 1),
        "connect_date": parse_date(raw.get("ConnectDate")),
        "disconnect_date": disconnect,
        "is_active": parse_bool(raw.get("IsActive")),
        "is_eto_station": parse_bool(raw.get("IsEtoStation")),
        "ground_cover": str(raw.get("GroundCover", "") or "").strip(),
        "source": "CIMIS",
    }


def fetch_payload(attempts: int = 4) -> dict:
    """Fetch the station list, retrying transient CIMIS 5xx responses."""
    request = urllib.request.Request(SOURCE_URL, headers={"User-Agent": USER_AGENT})
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        print(
            f"Fetching {SOURCE_URL} (attempt {attempt}/{attempts}) ...",
            file=sys.stderr,
        )
        try:
            with urllib.request.urlopen(request, timeout=90) as response:  # noqa: S310
                return json.load(response)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < attempts:
                delay = 2 * attempt
                print(f"  temporary failure: {exc}; retrying in {delay}s", file=sys.stderr)
                time.sleep(delay)
    raise RuntimeError(f"could not fetch CIMIS station list: {last_error}") from last_error


def main() -> int:
    payload = fetch_payload()
    raw_stations = payload.get("Stations", [])
    if not raw_stations:
        print("No stations returned by the CIMIS API.", file=sys.stderr)
        return 1

    rows: list[dict[str, object]] = []
    failures: list[str] = []
    for raw in raw_stations:
        try:
            rows.append(clean_station(raw))
        except (TypeError, ValueError) as exc:
            failures.append(f"station {raw.get('StationNbr', '?')}: {exc}")

    rows.sort(key=lambda row: int(str(row["station_id"])))

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    fetched_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    raw_path = DATA_DIR / "cimis_stations_raw.json"
    raw_path.write_text(
        json.dumps(
            {
                "source": SOURCE_URL,
                "fetched_at": fetched_at,
                "station_count": len(raw_stations),
                "stations": raw_stations,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    csv_path = DATA_DIR / "cimis_stations.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            record = dict(row)
            record["connect_date"] = row["connect_date"].isoformat() if row["connect_date"] else ""
            record["disconnect_date"] = (
                row["disconnect_date"].isoformat() if row["disconnect_date"] else ""
            )
            writer.writerow(record)

    print(f"Wrote {len(rows)} stations to {csv_path}", file=sys.stderr)
    print(f"Wrote raw payload to {raw_path}", file=sys.stderr)
    if failures:
        print(f"Skipped {len(failures)} malformed rows:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
