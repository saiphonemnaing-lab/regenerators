# Station selection and historical analysis

This implements steps 1 and 2 of the team workflow in `orchard_analysis.py`. The original `hackathon.py` remains available and supplies the three chill formulas.

## Run

Install Python dependencies once:

```powershell
python -m pip install -r requirements.txt
```

From this folder, run with interactive prompts:

```powershell
python orchard_analysis.py
```

Or supply the inputs directly:

```powershell
python orchard_analysis.py --lat 38.55 --lon -121.80 --elevation-ft 60 --base-f 50
```

Output defaults to `results/`. Override with `--output-dir PATH`. Override the input directory with `--data-dir PATH`.

## Station selection

- Validates finite latitude, longitude, and elevation (feet).
- Requires three stations within 75 miles and 500 feet of the orchard elevation, with at least 80% valid hourly data and paired daily minimum/maximum data over each source's loaded date range.
- Ranks candidates by `distance / distance_limit + absolute_elevation_difference / elevation_limit`. This is a transparent heuristic, not a calibrated model. Elevation influences selection; temperatures are not lapse-rate adjusted.
- Returns station IDs, names, coordinates, elevation differences, distances, coverage, score, blend weights, and explanations.
- Limits can be changed with `--max-distance-mi`, `--max-elevation-diff-ft`, and `--min-coverage`. Fewer than three suitable stations produces an actionable error instead of silently using distant stations.
- Station activity is reported but historical stations are not excluded solely because they are now inactive.
- Selection uses locally downloaded weather data. All-station metadata alone cannot supply temperature history. For orchards outside the demo area, download hourly and daily temperatures for additional nearby stations first.

## Historical calculations

- Calculates chill hours (32-45 F inclusive), Utah units, and Dynamic chill portions using the existing model functions.
- Chill accumulation resets November 1 and ends February 28, matching the original project's convention (February 29 is excluded). Missing hourly temperatures are not interpolated; the Dynamic Model holds its state during missing hours.
- Calculates GDD with the simple average formula: `max(0, (Tmin + Tmax) / 2 - base_f)`, no upper cutoff. The GDD base defaults to 50 F as a demo value and must be configured for the intended crop/model. Reference: https://ucanr-igis.github.io/degday/reference/dd_calc.html
- GDD accumulation resets January 1. A crop-specific season/biofix is not implemented.
- Produces results per selected station and an orchard estimate. Orchard temperatures are blended first, then metrics are calculated. Weight is inverse squared distance, with a 0.5-mile floor; available stations' weights are renormalized at each time. Daily min/max use the same contributing stations.
- Filters the source's bad QC codes (M,Q,R,S,N,H,Y), physically implausible temperatures, inverted daily extrema, and isolated hourly spikes. This follows the existing project's QC policy.
- Missing periods remain missing in daily output. Cumulative values sum observed increments; these are incomplete estimates when data is missing. No weather or metrics are invented to fill gaps.

## Outputs

- `selected_stations.csv`: selected IDs, scores, coverage, weights, and reasons.
- `daily_analysis.csv`: daily increments, cumulative observed sums, coverage, station/orchard entity, and season. Columns not applicable to a metric are blank.
- `season_summary.csv`: observed totals, expected/valid sample counts, coverage, incomplete marker, and minimum-coverage eligibility. Eligibility means only that coverage clears the configured threshold, not that an estimate is validated or complete.
- `analysis_config.json`: inputs, thresholds, methodology, and limitations.

The downloaded sample has 71 days of hourly data and three calendar years of daily data. It supports historical GDD calculations; its chill season is incomplete and is not eligible for full-season comparison at the default threshold. Three years do not establish a long-term climatology. Prediction, year-to-year comparison presentation, and a dashboard are outside these two steps.

## Dashboard integration

```python
from orchard_analysis import run_analysis, validate_site
result = run_analysis(validate_site(38.55, -121.80, 60), base_f=50)
# result contains three pandas tables: selected_stations, daily_analysis, season_summary
```

## Verification

```powershell
python -m unittest discover -s . -p test_orchard_analysis.py -v
```

The tests cover input validation, distance/elevation/coverage selection, missing-value blending, GDD arithmetic, hourly date boundaries, leap years, annual reset, source units/QC handling, and an integration run with the included demo data.
