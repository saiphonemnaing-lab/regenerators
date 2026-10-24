# Regenerators — Krish's implementation

Agriculture project for the Reboot the Earth Hackathon. This version extends the team's orchard chill prototype with station selection, weather processing, historical outlooks, evaluation, and a browser dashboard.

## Try the dashboard

On Windows, open hackathon/start_dashboard.cmd, then visit http://127.0.0.1:8765.

Or serve the static dashboard from the repository root:

```sh
python -m http.server 8765 --bind 127.0.0.1 --directory hackathon/dashboard/dist
```

Start with the Davis example. The app supports orchard coordinates/elevation, chill hours, Utah units, Dynamic chill portions, GDD, 7/14-day historical outlooks, backtest error tables, data coverage, and CSV export.

## Python modules

```sh
cd hackathon
python -m pip install -r requirements.txt
python orchard_analysis.py --lat 38.55 --lon -121.8 --elevation-ft 60
python data_processing.py --selected-stations results/selected_stations.csv
python prediction.py --selected-stations results/selected_stations.csv --as-of 2025-12-31
```

1. orchard_analysis.py: select three stations using distance, elevation difference, and usable winter-hourly/daily coverage; calculate observed accumulation.
2. data_processing.py: fetch CIMIS hourly/daily observations, clean sources, preserve gaps/flags, export datasets and quality reports.
3. prediction.py: prior-year calendar-window baseline, chronological backtests, MAE/RMSE/bias, persistence comparison.
4. dashboard/dist: static browser dashboard with equivalent calculations; no API key required to use included data.

The download functions use CIMIS_APP_KEY from the environment. Never commit API keys.

## Data and limitations

Bundled temperatures cover Davis (6), Winters (139), and Woodland (226). Hourly data covers three previous Nov–Feb winters and Nov 2025–Jan 2026. Daily data covers 2023–2025.

This is a historical decision-support prototype, not live weather forecasting. Some chill evaluations have only a few valid holdouts. Incomplete observations remain missing; totals can be biased. GDD uses a configurable base temperature (demo default 50 F), simple-average method and no upper cutoff. Chill seasons use Nov 1–Feb 28; Feb 29 is excluded to match the original model.

## Validation

```sh
python -m unittest discover -s hackathon -p "test_*.py"
node hackathon/dashboard/test-engine.mjs
```

26 Python tests and 16 forecast/evaluation parity checks pass. Original chill formulas are retained in hackathon/hackathon.py.

See hackathon/MODULE2_README.md and hackathon/MODULES_3_4_README.md for detailed handoff contracts and assumptions.
