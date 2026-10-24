import os, sys, warnings
sys.path.insert(0, "member1")  # use Member 1's flat module, not the station_selection/ package
import pandas as pd
from station_selection import select_stations
from weather_processing import fetch_weather_data, calculate_chill, calculate_gdd
from prediction import run_prediction_pipeline

# ---- edit these ----
LAT, LON, ELEV_M = 38.54, -121.74, 20
START, END = "2021-11-01", "2026-02-28"
CURRENT, TARGET = "2026-01-15", "2026-02-28"   # must fall inside a chill season
BASE_TEMP_F = 50
# export CIMIS_APP_KEY=your_key   (set this in the terminal first)

stations = select_stations(LAT, LON, ELEV_M, pd.read_csv("data/cimis_stations.csv"))
print(stations[["station_id", "station_name", "distance_km", "elevation_diff_m"]])

hourly = fetch_weather_data(stations, START, END, "hourly")
daily = fetch_weather_data(stations, START, END, "daily")

chill_df = calculate_chill(hourly)
gdd_df = calculate_gdd(daily, BASE_TEMP_F)

result = run_prediction_pipeline(chill_df, gdd_df, CURRENT, TARGET)
print(result)