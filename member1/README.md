# Member 1 — Location & Station Selection

`station_selection.py` implements the team contract:

```python
select_stations(
    latitude, longitude, elevation,
    stations_df,
    n_stations=3,
    max_distance_km=30,
    max_elevation_diff_m=100,
) -> pd.DataFrame
```

It returns up to `n_stations` stations, best first, with the catalog columns
plus `distance_km`, `elevation_diff_m` and `suitability_score` (lower is
better). The module depends only on pandas.

## Quick start

From the repository root:

```powershell
# Run the built-in four-site demo against data/cimis_stations.csv
python member1/demo.py

# Run one orchard
python member1/demo.py --lat 38.54 --lon -121.74 --elevation 20 --name "My orchard"

# Run the tests
python member1/test_station_selection.py -v
```

Or from Python:

```python
import pandas as pd
from station_selection import select_stations

all_stations = pd.read_csv("data/cimis_stations.csv")

selected = select_stations(
    latitude=38.54,
    longitude=-121.74,
    elevation=20,
    stations_df=all_stations,
)

print(selected[["station_id", "station_name", "distance_km",
                "elevation_diff_m", "suitability_score"]])
```

> **Import note:** the repository also contains a `station_selection/` package
> with a different API. Running Python from the repository root makes
> `import station_selection` resolve to that package, not this module. Either
> run from inside `member1/`, copy `station_selection.py` into the team repo,
> or put its directory on the path first (`sys.path.insert(0, "member1")`, as
> `demo.py` and the tests do).

The bundled CIMIS catalog calls its name column `name`; that is accepted as an
alias for `station_name`. The returned frame always uses `station_name`.

## How stations are ranked

```
d = distance_km / max_distance_km
e = elevation_diff_m / max_elevation_diff_m
score = 0.60 * d + 0.40 * e
```

If either limit is exceeded, `1.0` is added to the score. Therefore:

- `score <= 1.0` — the station met both search limits;
- `score > 1.0` — the station exceeded at least one limit.

Distance carries slightly more weight than elevation because temperature is
spatially correlated. Elevation becomes decisive when two candidates are
similarly distant, which is the common CIMIS problem in foothill orchards.
The weights and limits are module constants/arguments, not hidden state.

## Agreement: fewer than three suitable stations

The function never pads the result silently and never pretends a station met
the limits.

1. It returns every station that met both limits.
2. If slots remain, it fills them with the best available stations outside the
   limits, ordered by their real score.
3. `distance_km` and `elevation_diff_m` always contain the true values.
4. It emits a `StationSelectionWarning` naming each fallback and the limit it
   violated.

If the catalog itself has fewer than `n_stations` usable rows, it returns what
exists and warns. Callers that need hard limits should do one of:

```python
import warnings
from station_selection import StationSelectionWarning

with warnings.catch_warnings():
    warnings.simplefilter("error", StationSelectionWarning)
    selected = select_stations(...)   # raises instead of degrading
```

or check the returned columns directly:

```python
strict = selected[
    (selected["distance_km"] <= 30)
    & (selected["elevation_diff_m"] <= 100)
]
```

This is deliberate: in sparse regions a marginal station is more useful than
an empty result, but the grower must see that it is marginal.

## Validation and cleaning

- Required columns: `station_id`, `station_name` (or `name`), `latitude`,
  `longitude`, `elevation_m`.
- Input rows with missing, non-numeric, non-finite or out-of-range
  coordinates/elevation are dropped with a warning. If no usable rows remain,
  `ValueError` is raised.
- Coordinates must be in decimal degrees (WGS84); `elevation` is metres.
- `n_stations` must be a positive integer; limits must be positive.
- The input DataFrame is copied, never modified.
- Extra catalog columns are ignored, so the output schema is stable.

## Known limitations

- Station status is not part of the contract. If the catalog has `is_active`,
  `has_hourly` or `has_daily`, filter it before calling. Otherwise a nearby
  retired station can outrank a live one.
- Elevation is a scalar proxy, not terrain. Slope, aspect and cold-air pools
  are not modeled; the selected stations are candidates for comparison, not
  ground truth.
- Distance is great-circle distance, not travel or fetch distance.
- This module selects stations only. It does not fetch hourly/daily data or
  compute chill/GDD.

## Tests

```bash
python member1/test_station_selection.py -v
```

32 tests cover geodesy, scoring, ranking, fallback behaviour, warnings,
validation, cleaning and input immutability.
