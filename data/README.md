# CIMIS station snapshot

`cimis_stations.csv` is a normalized snapshot of the public CIMIS station list,
and `cimis_stations_raw.json` is the untouched API response it was built from.

- **Source:** <https://et.water.ca.gov/api/station> (public station metadata, no key)
- **Fetched:** 2026-10-24T18:35:14Z
- **Stations:** 276 total, 147 active at fetch time
- **Builder:** [`scripts/build_cimis_catalog.py`](../scripts/build_cimis_catalog.py)
- **Attribution:** California Department of Water Resources, California
  Irrigation Management Information System (CIMIS).

## Fields

| Column | Notes |
| --- | --- |
| `station_id` | CIMIS station number, stored as text |
| `name` | Station name |
| `city`, `county`, `regional_office` | CIMIS administrative metadata |
| `latitude`, `longitude` | Decimal WGS84 degrees, parsed from CIMIS deg/min/sec strings |
| `elevation_m` | Derived from the CIMIS value in feet |
| `elevation_ft` | Original CIMIS elevation (feet) |
| `connect_date` | First day the station is expected to have data |
| `disconnect_date` | Last day of data; blank means ongoing. CIMIS's `12/31/2050` sentinel is normalized to blank |
| `is_active` | `True`/`False` at fetch time |
| `is_eto_station` | Whether CIMIS designates the site as an ETo station |
| `ground_cover` | CIMIS siting description (usually `Grass`) |
| `source` | `CIMIS` |

The CSV deliberately does **not** claim to know about data gaps, sensor QC, or
hourly-vs-daily availability. Selection uses service dates as a coverage proxy.
See the main README for how to attach real coverage/quality metadata.

## Refresh

```bash
python scripts/build_cimis_catalog.py
```

The endpoint occasionally returns a transient HTTP 500; the script retries with
backoff. Review the diff after refreshing, because CIMIS adds, removes and
relocates stations. Keep the raw JSON so every selection run is reproducible.
