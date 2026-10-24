# Member 2: Data Collection & Processing

This module takes selected station IDs from Member 1, retrieves hourly/daily CIMIS temperatures, cleans the observations, and calculates historical chill/GDD for Member 3 and Member 4.

## Quick start

Requires Python 3.10+ and the packages in requirements.txt.

Use the downloaded demo data without an API key:

```powershell
python data_processing.py --stations 6 139 226 --base-f 50
```

Or take Member 1's CSV, which must have exactly three distinct station_id values:

```powershell
python data_processing.py --selected-stations sample-output/selected_stations.csv --base-f 50
```

The command writes to processed/ by default. Set --output-dir to change the destination. No station selection runs inside this module. If the input CSV includes positive blend weights, an orchard estimate is calculated as well as the three station results. Without weights, only station results are produced.

## Download directly from CIMIS

Set CIMIS_APP_KEY in your environment using your team's own key, then run:

```powershell
python data_processing.py --stations 6 139 226 --download --hourly-start 2025-11-01 --hourly-end 2026-01-10 --daily-start 2023-01-01 --daily-end 2025-12-31
```

The downloader uses the current StationWeb/GetDataByStationNumber endpoint, header authentication, small batches, and up to three attempts per batch. It validates data envelopes, dates, station IDs, units, scopes, and duplicates before caching responses. HTML rejection pages are never saved as valid weather data. Existing validated batches are reused. No API key appears in the request URL, saved source data, or manifest. Source values and flags remain unchanged in raw JSON. A missing station in an API response may require obtaining additional source data; the processing stage reports absent station sources rather than substituting other stations.

Both date ranges are required for downloads. For offline processing, omit ranges to use all loaded dates, or supply both bounds for a source to restrict it. Explicit ranges create missing grid rows for absent observations, so coverage does not silently shrink to the available dates. The original PowerShell downloader remains available.

## Clean dataset contract

- clean_hourly.csv: station_id, timestamp_end_pst, source_date, temp_f, valid. One row per station/hour, including absent or rejected observations with blank temperature.
- clean_daily.csv: station_id, date, tmin_f, tmax_f, gdd, valid. One row per station/day; GDD is blank unless both temperatures are usable.
- daily_metrics.csv: entity, scope, season, date, daily increments, cumulative observed sums, and coverage. Chill rows include valid_hours, chill_hours, utah_units, chill_portions and corresponding cumulative fields. GDD rows include daily/cumulative GDD. Metric-specific columns are blank when not applicable.
- season_summary.csv: entity, scope, season, observed totals, expected_samples, valid_samples, coverage, incomplete, eligible_for_comparison.
- quality_report.csv: usable versus missing/rejected samples and coverage for each station/source.
- source_audit.csv: original values, units, flags, source date/hour, and source filename. Keeps overlapping source rows for traceability; final exports deduplicate by station/time.
- selected_stations.csv: the input handoff.
- manifest.json: input file SHA-256 hashes, parameters, source endpoint, timing conventions, counts, and limitations.

CSV booleans are True/False; missing numeric values are empty fields. IDs are integers. entity is the station ID as text, or orchard when blending is requested.

## Processing rules

Uses the shared existing orchard_analysis.py cleaning/calculation functions:
- Excludes source QC codes M,Q,R,S,N,H,Y.
- Rejects nonnumeric values and temperatures outside -100..140 F.
- Rejects inverted daily extrema and isolated hourly excursions over 15 F away from both neighboring hours.
- Drops duplicate station/time observations, keeping the last in sorted filename order.
- Preserves gaps; no temperature imputation.
- CIMIS hourly timestamps mark hour ends in fixed Pacific Standard Time. 2400 belongs to the previous source date; do not apply daylight-saving conversion.
- Chill models: hours between 32 and 45 F inclusive, Utah units, Dynamic chill portions. Seasons run Nov 1-Feb 28, matching the original code. Feb 29 is excluded.
- GDD: max(0, (Tmin+Tmax)/2-base_f), simple average, no upper cutoff, reset Jan 1. The default 50 F is a demo parameter, not a recommendation for every crop. Change --base-f for the chosen crop/model.
- Optional orchard blend uses supplied weights, renormalized over available stations at each timestep. Daily min/max use the same contributing stations. Metrics are computed after blending temperatures.

Totals sum observed increments and may be biased when incomplete. Dynamic chill holds its state through missing hours. Coverage eligibility defaults to 80%; this is a data-screening threshold, not model accuracy validation. Consumers should inspect incomplete and coverage before using seasonal totals or training/evaluating forecasts.

## Available sample and verification

Sample sources: Davis (6), Winters (139), Woodland (226); 5,112 hourly records from Nov 1, 2025-Jan 10, 2026 and 3,288 daily records covering 2023-2025. Source QC filtering reduces the usable counts. The hourly sample is not a full winter and does not support multiwinter chill comparisons.

The new Python downloader was verified against the live API: a one-day, three-station request returned 72 hourly observations and three daily observations.

```powershell
python -m unittest discover -s . -p "test_*.py" -v
```

19 tests pass across collection/processing and station analysis, including cache reuse, header authentication, rejected responses, source QC, missing days, and calculation boundaries.

## Python integration

```python
from data_processing import station_input, process_weather
selected = station_input(stations=[6, 139, 226])
tables, input_files = process_weather(selected, base_f=50)
daily = tables["daily_metrics"]
seasons = tables["season_summary"]
```

Prediction, evaluation metrics, and dashboard implementation belong to the downstream modules.
