# Modules 3 and 4 — Predictions, evaluation, and dashboard

## What is implemented

- Module 3: 7- and 14-day forecasts for GDD, chill hours, Utah units, and Dynamic chill portions.
- Forecast method: mean accumulation in complete matching calendar windows from at least two prior anchor years. Cross-year windows may end in the following year, but must end before the issue date.
- Empirical 10th–90th percentile historical spread, explicitly not a calibrated prediction interval.
- Chronological rolling backtests every 14 days: MAE, RMSE, signed bias, and comparison with the previous seven days' mean accumulation carried forward.
- Every backtest uses earlier historical windows and complete held-out observations. Backtest actuals are limited to the selected analysis date.
- Module 4: responsive dashboard with orchard inputs, station selection, four metric choices, date replay, horizon switching, accumulation/forecast chart, station weights, evaluation tables, data-quality explanations, and CSV download.
- Browser computations match the Python implementation; no API key is included in dashboard assets.

## Try the dashboard locally

Double-click start_dashboard.cmd on Windows, or run:

```powershell
python -m http.server 8765 --bind 127.0.0.1 --directory dashboard/dist
```

Open http://127.0.0.1:8765 in your browser. The Windows launcher tries Codex's bundled Python first, so this computer does not need Python on PATH. If another server is already running on that port, use its existing dashboard or change the port.

The dashboard defaults to a historical replay through December 31, 2025. Try the Davis example, then change the metric, horizon, and evaluation tab. Current bundled data supports Davis, Winters, and Woodland. Unsupported locations produce an actionable error rather than substituting distant stations.

## Python prediction workflow

From this folder, with pandas/numpy/matplotlib installed:

```powershell
python orchard_analysis.py --lat 38.55 --lon -121.8 --elevation-ft 60
python prediction.py --selected-stations results/selected_stations.csv --as-of 2025-12-31
```

Results: predictions/forecasts.json, evaluation.csv, and backtests.csv.
If weights are absent from the selected-station CSV, the CLI forecasts the first station. For an orchard forecast, provide the station-selection output with its blend weights.

## Data expansion

Hourly observations were added for Nov 1–Feb 28 in the 2022–23, 2023–24, and 2024–25 winters. The Nov 2025–Jan 10, 2026 sample remains available. Daily data covers 2023–2025.
Summer hourly records were not downloaded. Station selection now evaluates hourly availability during winter months rather than counting unavailable summer hours against a chill dataset. Daily coverage continues to use the loaded daily period.

Source QC flags, missing readings, and the original chill-season convention are retained. GDD uses simple average with no upper cutoff; default base 50 F is a demo value. User-entered coordinates are processed in the browser, not sent to CIMIS.

## Evaluation limits

The available record is short. Some chill evaluations have only 2–4 eligible holdouts, and the historical baseline sometimes performs worse than recent-temperature persistence. The dashboard shows both facts. The model is a historical baseline, not a live weather forecast or crop-stage model. Incomplete accumulation is labelled, and partial chill days are excluded from forecast training/test windows. Dynamic chill holds state during missing hours.

## Verification

26 Python tests pass. Browser-engine checks verify 16 forecast/evaluation results against Python, plus station selection and exclusion of future target observations.
Browser QA verified metric switching, horizon switching, evaluation tables, unsupported-location errors, and reset behavior.

```powershell
python -m unittest discover -s . -p "test_*.py"
node dashboard/test-engine.mjs
```

## Hosted status

A private Sites project was registered, and the site manifest is retained in the working dashboard/.openai/hosting.json.
Publication did not complete: automatic approval review rejected the publishing helper's stdin write containing its source-repository credentials because the required sandbox approval category is disabled. A noninteractive fallback then failed because its execution environment could not resolve the source-repository host. There is no verified production URL. No source was pushed to the original GitHub repository.

The static dashboard/dist directory is deployable once the publishing permission issue is resolved. The source credentials are not included in this package.
