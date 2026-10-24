# Orchard Location and Station Selection

Given an orchard's coordinates and elevation, choose the **three most suitable
weather stations** from a catalog of CIMIS and/or low-cost in-orchard stations,
and return their metadata with enough evidence to explain the choice.

This is the location/station-selection module of the chill and growing-degree-day
(GDD) pipeline. It does not download weather data or compute chill/GDD itself;
it produces the station IDs and metadata that those modules consume.

- **Function:** `select_stations(location, stations, config, as_of)`
- **Main output:** `SelectionResult` with selected station metadata, alternates,
  scores, coverage, warnings and diagnostics (JSON-serializable).
- **Catalog:** 276 real CIMIS stations, including coordinates and elevations,
  committed in [`data/cimis_stations.csv`](data/cimis_stations.csv).
- **No runtime dependencies:** Python 3.10+ standard library only.

---

## Quick start

```bash
# Human-readable selection
python -m station_selection \
  --lat 39.7285 --lon -121.8375 --elevation 60 \
  --name "Chico almond demo" --as-of 2026-10-24

# Machine-readable metadata
python -m station_selection \
  --lat 39.7285 --lon -121.8375 --elevation 60 \
  --as-of 2026-10-24 --format json --output selected.json

# Four-site demo + full JSON artifact
python examples/demo.py --as-of 2026-10-24
```

```python
from datetime import date

from station_selection import (
    OrchardLocation, SelectionConfig, StationCatalog, select_stations,
)

catalog = StationCatalog.from_csv("data/cimis_stations.csv")
result = select_stations(
    OrchardLocation(latitude=39.7285, longitude=-121.8375, elevation_m=60.0),
    catalog,
    SelectionConfig(),         # optional; defaults below
    as_of=date(2026, 10, 24),  # optional; defaults to today
)

print(result.station_ids)    # ['244', '222', '250']
print(result.to_json())      # full selected-station metadata
```

`as_of` accepts a `datetime.date` from Python; the CLI parses `YYYY-MM-DD`.

### CLI options

| Option | Purpose |
| --- | --- |
| `--lat`, `--lon`, `--elevation`, `--elevation-unit {m,ft}` | Orchard location |
| `--name` | Human-readable site name echoed in the output |
| `--catalog` | CSV or JSON station catalog (default: bundled CIMIS snapshot) |
| `--default-elevation-unit {m,ft}` | Required only for a catalog with an unlabelled `elevation` column |
| `--as-of` | End of the historical window (default: today) |
| `--format {text,json}` | Output format |
| `--output` | Write output to a file |
| `--config` | JSON file of `SelectionConfig` overrides |
| `--count`, `--history-years`, `--max-distance-km`, `--max-elevation-diff-m`, `--min-separation-km`, `--min-history-coverage` | Common threshold overrides |
| `--active-only` | Exclude stations known to be inactive |
| `--require-hourly`, `--require-daily` | Exclude stations whose metadata says that data is unavailable |
| `--strict` | Exit with code 3 if fewer than `--count` stations are found |

---

## Why this is not just "nearest station"

CIMIS stations can be tens of kilometres away, sit at a different elevation, or
be in a different microclimate. In California, elevation drives both chill
(inversions can make valley floors colder than nearby slopes) and GDD, so a
station that is close but 400 m higher is often worse than one farther away at
the same elevation. The selector therefore combines:

1. **Distance** — great-circle distance from the orchard.
2. **Elevation similarity** — differences inside a tolerance band count as
   equivalent; larger differences are penalized exponentially.
3. **History coverage** — whether the station has data for the required
   baseline (default: the last 3 years).

Hard caps reject implausible stations. The rest are scored, and a
minimum-separation rule prevents three sensors on the same ranch from being
returned as if they were independent.

### Eligible vs. preferred

The selector distinguishes between *hard limits* (never select) and *quality
bands* (select only if nothing better exists).

| Dimension | Preferred | Acceptable | Marginal | Hard cap |
| --- | --- | --- | --- | --- |
| Distance | ≤ 25 km | ≤ 50 km | > 50 km | ≤ 100 km |
| Elevation difference | ≤ 100 m | ≤ 250 m | > 250 m | ≤ 600 m |
| History coverage | ≥ 95% | ≥ 80% | < 80% | ≥ 50% |

A station's overall quality is the **worst** of its known dimensions. Marginal
stations are still useful when a region is sparse, but they carry warnings and
should be treated as uncertain microclimate proxies.

### Score

```
D = exp(-distance_km / 25)
E = 1                                     if |Δh| ≤ 100 m
    exp(-(|Δh| - 100) / 100)              otherwise
H = station_coverage_fraction             (0..1, omitted if dates unknown)

score = (0.40·D + 0.40·E + 0.20·H) / (sum of weights that are known)
```

Weights are renormalized when history coverage is unknown, so a catalog without
service dates is not unfairly penalized. The score is only used to rank
stations that already passed the hard caps.

### Station tiers and independence

- **Active stations first.** A live station is normally more useful than a
  recently closed one, even if the latter is closer. If there are not enough
  active stations, inactive stations fill the remaining slots and are flagged.
  Set `prefer_active=False` to rank purely by score.
- **8 km minimum separation.** Near-duplicate stations are skipped when a
  spatially independent alternative exists. If too few independent stations
  exist, the rule is relaxed with a warning.
- **Alternates.** The next two eligible stations are returned so a failed
  sensor or missing dataset has a ready backup.

### History window

The default window is the **last 3 years, inclusive**. For an `as_of` date of
2026-10-24 the window is 2023-10-25 through 2026-10-24 (1,096 days), which
contains three complete chill seasons.

Coverage is based on the catalog's `connect_date`/`disconnect_date`. It is a
service-period proxy, **not** a measurement of actual data completeness. If you
have gap/QC metadata, join it into the catalog before selecting (see
[Adding in-orchard stations](#adding-in-orchard-stations)).

### Lapse correction (reported, not applied)

For each selected station the output includes:

```
lapse_correction_c = (station_elevation_m − orchard_elevation_m) × 6.5 / 1000
```

Add this to the station's air temperature to estimate the orchard's air
temperature under a standard lapse rate. Example: a station 153 m below a
1,000 m orchard gets `−0.99 °C`; the orchard is expected to be about 1 °C
cooler. This is a first-order correction only. **Temperature inversions can
reverse the sign**, which is why large corrections carry an explicit warning.
Bark-temperature models for chill (as in the UC cherry chill tool) remain a
separate downstream step.

---

## How much historical data should we pull?

For the demo: **3 years of hourly data for chill and 3 years of daily data for
GDD**, from the three selected stations.

| Use | Recommendation |
| --- | --- |
| Demo / hackathon baseline | 3 years (3 complete chill seasons) |
| Operational recent baseline | 3–5 years |
| Climatological normals | 10–30 years, if the station record is homogeneous |
| Forecast horizon | 7–14 days from a weather forecast, not historical data |

Why 3 years:

- It covers year-to-year variability without pretending to be a climate normal.
- CIMIS stations are added, moved and removed; long records are often
  inhomogeneous, and old inactive stations may not overlap the recent period.
- Three years is enough to rank stations and to show inter-station spread, but
  **report that spread as uncertainty**. If the three selected stations disagree
  substantially over the same period, the orchard's microclimate is not well
  represented by any of them.

The window is configurable (`history_years`), and `min_history_coverage`
prevents a newly installed station with only a few months of data from being
selected silently.

Chill is computed from **hourly** temperatures (hours between 32–45 °F, or the
Utah model); GDD is computed from **daily** Tmax/Tmin:

```
chill_hours = ((temps >= 32) & (temps <= 45)).sum()
gdd         = max(0, (tmax + tmin) / 2 - base)
```

Set `require_hourly=True` and/or `require_daily=True` when the catalog records
those capabilities. The bundled CIMIS snapshot does not, because the public
station-list endpoint does not expose hourly/daily flags; absence of a flag is
treated as unknown, not as "unavailable".

---

## Output

The full Chico example is in
[`examples/selected_stations.json`](examples/selected_stations.json). A trimmed
selection entry:

```json
{
  "rank": 1,
  "station": {
    "id": "244",
    "name": "Biggs",
    "source": "CIMIS",
    "type": "cimis",
    "latitude": 39.386632,
    "longitude": -121.835278,
    "elevation_m": 21.9,
    "elevation_ft": 71.9,
    "county": "Butte",
    "connect_date": "2015-06-18",
    "disconnect_date": null,
    "is_active": true
  },
  "distance_km": 38.01,
  "distance_mi": 23.62,
  "bearing_deg": 179.7,
  "bearing": "S",
  "elevation_delta_m": -38.1,
  "elevation_delta_ft": -125.0,
  "lapse_correction_c": -0.25,
  "score": 0.6874,
  "score_components": {
    "distance": 0.2186,
    "elevation": 1.0,
    "history": 1.0
  },
  "history_coverage": 1.0,
  "quality": "acceptable",
  "rationale": "38.0 km S; 38 m lower than orchard; 100% of 3-year history window; similarity 0.69",
  "warnings": []
}
```

Result-level fields:

| Field | Meaning |
| --- | --- |
| `selected_station_ids` | Convenience list for the hourly/daily fetch modules |
| `selected` | Ranked stations with full metadata and evidence |
| `alternates` | Next-best stations as backups |
| `history_window` | Exact baseline dates and day count |
| `summary` | One-line human-readable result |
| `warnings` | Microclimate, coverage and fallback warnings |
| `diagnostics` | Catalog counts, rejection checks and the nearest five stations |
| `config` | Fully resolved parameters, so a run can be reproduced |

### Station metadata contract

`Station` supports any source, not just CIMIS:

```python
Station(
    station_id="ranch-07",
    name="North block sensor",
    latitude=39.7281,
    longitude=-121.8380,
    elevation_m=61.2,
    source="On-Farm",
    station_type="in_orchard",
    connect_date=date(2023, 3, 1),
    is_active=True,
    has_hourly=True,
    has_daily=True,
)
```

An in-orchard station at the target coordinates will score at or near the top
because distance ≈ 0 and elevation difference ≈ 0. If its record is short, the
history coverage component keeps it from outranking a long-record CIMIS station
without warning. Low-cost sensors should be QC'd separately; this module
assumes the catalog's dates and active flag are trustworthy.

---

## Demo results

Run `python examples/demo.py --as-of 2026-10-24`. Approximate town-centre
coordinates are used, not surveyed orchard boundaries.

| Site | Selected stations (distance, elevation delta, quality) |
| --- | --- |
| Chico almond | 244 Biggs (38.0 km, −38 m, acceptable); 222 Gerber South (43.9 km, +15 m, acceptable); 250 Williams (64.2 km, −39 m, **marginal**) |
| Fresno stone fruit | 80 Fresno State (8.7 km, +9 m, preferred); 39 Parlier (29.1 km, +9 m, acceptable); 2 FivePoints (54.9 km, −7 m, **marginal**) |
| Santa Maria strawberry | 232 Santa Maria II (5.1 km, 0 m, preferred); 202 Nipomo (14.1 km, +12 m, preferred); 165 Sisquoc (23.8 km, +97 m, preferred) |
| Camino apple | 13 Camino (5.8 km, −153 m, acceptable); 228 Diamond Springs (15.6 km, −375 m, **marginal**); 227 Plymouth (28.0 km, −537 m, **marginal**) |

The Chico and Fresno results are the motivating story: the nearest live CIMIS
stations are 38–55 km away. The Camino result shows the opposite problem: a
nearby station exists, but the orchard is 153–537 m higher, so the selected
stations are weak microclimate proxies. That is exactly where in-orchard
stations add value.

---

## Parameters

All parameters live in `SelectionConfig`. The CLI exposes the common ones; pass
a JSON file with `--config` to override any field.

| Parameter | Default | Meaning |
| --- | --- | --- |
| `station_count` | 3 | Stations to select |
| `alternate_count` | 2 | Backups to return |
| `history_years` | 3 | Baseline length |
| `min_history_coverage` | 0.5 | Hard minimum coverage fraction |
| `max_distance_km` | 100 | Hard distance cap |
| `max_elevation_diff_m` | 600 | Hard elevation cap |
| `preferred_distance_km` | 25 | Preferred band |
| `acceptable_distance_km` | 50 | Acceptable band |
| `preferred_elevation_diff_m` | 100 | Preferred band |
| `acceptable_elevation_diff_m` | 250 | Acceptable band |
| `preferred_history_coverage` | 0.95 | Preferred band |
| `acceptable_history_coverage` | 0.80 | Acceptable band |
| `distance_decay_km` | 25 | Exponential distance decay |
| `elevation_tolerance_m` | 100 | Elevation differences treated as equivalent |
| `elevation_decay_m` | 100 | Decay beyond the tolerance |
| `weight_distance` | 0.40 | Score weight |
| `weight_elevation` | 0.40 | Score weight |
| `weight_history` | 0.20 | Score weight |
| `min_station_separation_km` | 8 | Avoid near-duplicate sensors |
| `lapse_rate_c_per_km` | 6.5 | Reported correction only |
| `prefer_active` | `true` | Consider live stations first |
| `include_inactive` | `true` | Allow inactive stations as fallbacks |
| `require_hourly` | `false` | Exclude stations without hourly data |
| `require_daily` | `false` | Exclude stations without daily data |
| `fail_if_fewer_than` | 0 | Optional strict failure threshold |

Tuning guidance:

- **Flat valley:** distance and history dominate; elevation tolerance of 100 m
  is generous but harmless.
- **Foothills / mountains:** lower `elevation_tolerance_m` and
  `max_elevation_diff_m` (e.g. 50 m and 300 m), and raise the elevation weight.
  Expect fewer than three good stations; the warnings are the honest answer.
- **Sparse region:** raise `max_distance_km` before raising the elevation cap.
  A distant station at the same elevation is usually safer than a close station
  on a different slope.
- **Season-specific work (e.g. bud break):** re-rank stations using only the
  months that matter once hourly data are available. Selection is currently
  season-agnostic by design.

---

## Catalog format

CSV or JSON. Column names are matched case-insensitively and ignore spaces and
punctuation, so `Station Nbr`, `station_id`, `Elevation (ft)` and
`elevation_ft` all work.

| Concept | Accepted aliases |
| --- | --- |
| Station ID | `station_id`, `Station Nbr`, `station_number`, `id` |
| Name | `name`, `Station Name` |
| Latitude / longitude | `latitude`/`lat`, `longitude`/`lon`/`lng` |
| Elevation | `elevation_m`, `elevation_ft`, or an unlabelled `elevation` **plus** `default_elevation_unit` |
| Service dates | `connect_date`/`start_date`, `disconnect_date`/`end_date` |
| Status | `is_active`/`active` |
| Data availability | `has_hourly`, `has_daily` |
| Source / type | `source`/`network`, `station_type`/`type` |
| Context | `city`, `county`, `regional_office`, `ground_cover` |

Unlabelled elevations are rejected unless the caller states the unit; silently
assuming feet when a file is in metres would corrupt every downstream
temperature comparison.

Adding in-orchard stations is a matter of appending rows with the same columns.
Keep a `source` value so the output shows provenance.

---

## Integration with chill and GDD

```python
from station_selection import select_stations

result = select_stations(location, catalog)
hourly_ids = result.station_ids           # feed hourly chill ingestion
daily_ids = result.station_ids            # feed daily GDD ingestion

for match in result.selected:
    print(match.station.station_id, match.lapse_correction_c)
```

Downstream, a sensible workflow is:

1. Fetch hourly and daily data for all selected **and alternate** stations.
2. Compute chill (hourly) and GDD (daily) for the same window on each station.
3. Compare the three station series. If they disagree strongly, report a range
   rather than a single number.
4. Apply the lapse correction only as a sensitivity check; do not let it hide
   inversion risk.
5. Weight by month/hour where the crop model requires it (chill is hourly,
   GDD daily). Station selection is independent of those weights.

---

## Assumptions and limitations

- Air temperature is used as a proxy for orchard microclimate. Bark temperature,
  radiative frost, soil temperature and humidity are not modeled here.
- Elevation is a scalar, not terrain. Two sites 50 m apart in elevation can
  differ substantially if one is on a slope and the other in a cold-air pool.
  A DEM/aspect-aware refinement is a natural next step; the output already
  includes bearing and elevation delta to support it.
- Coverage is date-based. Actual CIMIS gaps, sensor drift and QC flags are not
  in the public station-list snapshot.
- The snapshot is dated 2026-10-24. Refresh it before operational use.
- `is_active` reflects the fetch date, not real-time telemetry.
- Forecasts are out of scope. Historical selection is not a forecast model.

## Testing

```bash
python -m unittest discover -s tests -v
```

45 tests cover geodesy, catalog parsing, scoring, tiering, coverage, separation,
strict mode, CLI behaviour and the committed CIMIS snapshot. CI needs no
network access.

## Data provenance

Station metadata: California Department of Water Resources, CIMIS public
station list (`https://et.water.ca.gov/api/station`). See
[`data/README.md`](data/README.md) for fields, refresh instructions and the raw
payload.
