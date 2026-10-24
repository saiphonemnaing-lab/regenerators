"""
weather_processing.py  --  Member 2: Data Collection & Processing

    fetch_weather_data()  -> raw hourly or daily CIMIS weather (standardized columns)
    calculate_chill()     -> daily + cumulative chill hours (32-45 F model)
    calculate_gdd()       -> daily + cumulative growing degree days

API key: set the environment variable CIMIS_APP_KEY, or pass api_key=... to
fetch_weather_data(). Never hard-code the key in a file you commit.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Optional

import numpy as np
import pandas as pd
import requests

log = logging.getLogger(__name__)

CIMIS_URL = "https://et.water.ca.gov/api/data"

# CIMIS caps each request at ~1,740 (hourly) / ~1,750 (daily) records.
# We request ONE station at a time and stay safely under the cap.
MAX_RECORDS_PER_REQUEST = 1700

# CIMIS QC flags we treat as "no usable observation".
# (R = rejected/out of range, M = missing, Q = missing, S = severely out of range)
BAD_QC_FLAGS = {"R", "M", "Q", "S"}


# ----------------------------------------------------------------------------
# Function 1: fetch_weather_data
# ----------------------------------------------------------------------------
def fetch_weather_data(
    selected_stations: pd.DataFrame,
    start_date: str,
    end_date: str,
    frequency: str = "hourly",
    api_key: Optional[str] = None,
    station_col: Optional[str] = None,
    bad_qc_flags: set = BAD_QC_FLAGS,
    max_retries: int = 2,
    pause: float = 0.5,
) -> pd.DataFrame:
    """
    Fetch CIMIS weather for the selected stations.

    Returns
    -------
    hourly -> station_id, timestamp, temperature (F)   [+ qc]
    daily  -> station_id, date, tmax (F), tmin (F)     [+ qc_tmax, qc_tmin]

    Notes
    -----
    * Hourly timestamps are HOUR-BEGINNING. CIMIS reports hour-ending
      ("0100" = 00:00-01:00, "2400" = 23:00-24:00), so we shift back one hour.
      This keeps hour "2400" on the correct calendar date.
    * Observations flagged as bad by CIMIS (or with no value) come back as NaN
      in daily mode and are dropped in hourly mode. Missing is never imputed.
    * Chunks that fail after retries are skipped with a warning, so one bad
      request doesn't kill the whole pull.
    """
    if frequency not in ("hourly", "daily"):
        raise ValueError("frequency must be 'hourly' or 'daily'")

    api_key = api_key or os.environ.get("CIMIS_APP_KEY")
    if not api_key:
        raise ValueError("No CIMIS API key: set CIMIS_APP_KEY or pass api_key=")

    station_ids = _extract_station_ids(selected_stations, station_col)

    start, end = pd.Timestamp(start_date), pd.Timestamp(end_date)
    if end < start:
        raise ValueError("end_date is before start_date")

    records_per_day = 24 if frequency == "hourly" else 1
    days_per_chunk = max(1, MAX_RECORDS_PER_REQUEST // records_per_day)
    data_items = "hly-air-tmp" if frequency == "hourly" else "day-air-tmp-max,day-air-tmp-min"

    rows, failed = [], []
    for sid in station_ids:
        chunk_start = start
        while chunk_start <= end:
            chunk_end = min(chunk_start + pd.Timedelta(days=days_per_chunk - 1), end)
            params = {
                "appKey": api_key,
                "targets": sid,
                "startDate": chunk_start.strftime("%Y-%m-%d"),
                "endDate": chunk_end.strftime("%Y-%m-%d"),
                "dataItems": data_items,
                "unitOfMeasure": "E",  # English units -> degrees F
            }
            payload = _get_json(params, max_retries)
            if payload is None:
                failed.append((sid, params["startDate"], params["endDate"]))
            else:
                rows.extend(_parse_records(payload, frequency))
            chunk_start = chunk_end + pd.Timedelta(days=1)
            time.sleep(pause)

    if failed:
        log.warning("%d request chunk(s) failed and were skipped: %s", len(failed), failed)

    return _build_frame(rows, frequency, bad_qc_flags)


def _extract_station_ids(stations: pd.DataFrame, station_col: Optional[str]) -> list:
    candidates = [station_col] if station_col else ["station_id", "StationNbr", "station", "Station"]
    for c in candidates:
        if c and c in stations.columns:
            return [str(s) for s in stations[c].dropna().unique()]
    raise KeyError(
        f"Couldn't find a station id column in selected_stations "
        f"(looked for {candidates}). Pass station_col=..."
    )


def _get_json(params: dict, max_retries: int) -> Optional[dict]:
    """GET with a minimal retry on transient errors. Returns None on failure."""
    for attempt in range(max_retries + 1):
        try:
            r = requests.get(CIMIS_URL, params=params, headers={"Accept": "application/json"}, timeout=60)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(2 ** attempt)
                continue
            log.warning("CIMIS HTTP %s for station %s: %s", r.status_code, params["targets"], r.text[:200])
            return None
        except (requests.RequestException, ValueError) as e:
            log.warning("CIMIS request error (attempt %d): %s", attempt + 1, e)
            time.sleep(2 ** attempt)
    return None


def _parse_records(payload: dict, frequency: str) -> list:
    out = []
    providers = (payload.get("Data") or {}).get("Providers") or []
    for prov in providers:
        for rec in prov.get("Records") or []:
            sid = str(rec.get("Station"))
            date = rec.get("Date")
            if frequency == "hourly":
                t = rec.get("HlyAirTmp") or {}
                out.append((sid, date, rec.get("Hour"), t.get("Value"), t.get("Qc")))
            else:
                tx = rec.get("DayAirTmpMax") or {}
                tn = rec.get("DayAirTmpMin") or {}
                out.append((sid, date, tx.get("Value"), tx.get("Qc"), tn.get("Value"), tn.get("Qc")))
    return out


def _build_frame(rows: list, frequency: str, bad_flags: set) -> pd.DataFrame:
    if frequency == "hourly":
        cols = ["station_id", "timestamp", "temperature", "qc"]
        if not rows:
            return pd.DataFrame(columns=cols)
        df = pd.DataFrame(rows, columns=["station_id", "date", "hour", "temperature", "qc"])
        hour_ending = pd.to_numeric(df["hour"], errors="coerce") // 100
        df["timestamp"] = pd.to_datetime(df["date"]) + pd.to_timedelta(hour_ending - 1, unit="h")
        df["temperature"] = pd.to_numeric(df["temperature"], errors="coerce")
        df["qc"] = df["qc"].fillna("").astype(str).str.strip()
        df.loc[df["qc"].isin(bad_flags), "temperature"] = np.nan
        df = df.dropna(subset=["timestamp", "temperature"])
        df = df.drop_duplicates(subset=["station_id", "timestamp"], keep="last")
        return df[cols].sort_values(["station_id", "timestamp"]).reset_index(drop=True)

    cols = ["station_id", "date", "tmax", "tmin", "qc_tmax", "qc_tmin"]
    if not rows:
        return pd.DataFrame(columns=cols)
    df = pd.DataFrame(rows, columns=["station_id", "date", "tmax", "qc_tmax", "tmin", "qc_tmin"])
    df["date"] = pd.to_datetime(df["date"])
    for v, q in (("tmax", "qc_tmax"), ("tmin", "qc_tmin")):
        df[v] = pd.to_numeric(df[v], errors="coerce")
        df[q] = df[q].fillna("").astype(str).str.strip()
        df.loc[df[q].isin(bad_flags), v] = np.nan
    df = df.drop_duplicates(subset=["station_id", "date"], keep="last")
    return df[cols].sort_values(["station_id", "date"]).reset_index(drop=True)


# ----------------------------------------------------------------------------
# Season helper (shared by chill and GDD)
# ----------------------------------------------------------------------------
def _season_year(dates: pd.Series, season_start: str) -> pd.Series:
    """
    Label each date with the calendar year in which its season STARTED.
    season_start is "MM-DD". With "11-01", 2023-12-15 and 2024-01-20 both
    belong to season 2023; 2024-11-02 belongs to season 2024.
    """
    month, day = (int(x) for x in season_start.split("-"))
    after_start = (dates.dt.month > month) | ((dates.dt.month == month) & (dates.dt.day >= day))
    return dates.dt.year.where(after_start, dates.dt.year - 1)


# ----------------------------------------------------------------------------
# Function 2: calculate_chill
# ----------------------------------------------------------------------------
def calculate_chill(
    hourly_df: pd.DataFrame,
    model: str = "chill_hours_32_45",
    season_start: str = "11-01",
) -> pd.DataFrame:
    """
    Daily and cumulative chill from standardized hourly data.

    Output columns: station_id, date, daily_chill, cumulative_chill,
                    n_valid_hours, season

    * Chill-hours model: an hour counts if 32 F <= temperature <= 45 F.
    * Only VALID hourly observations are evaluated. Missing hours are NOT
      counted as non-chilling hours; they just contribute nothing.
    * A day with zero valid hours gets daily_chill = NaN (unknown, not zero).
      Cumulative totals skip NaN days, so they are a lower bound where data
      is missing; check n_valid_hours to see how complete each day was.
    * Cumulative chill resets at season_start ("MM-DD", default Nov 1).
    """
    if model != "chill_hours_32_45":
        raise NotImplementedError(f"Unknown chill model '{model}'")

    cols = ["station_id", "date", "daily_chill", "cumulative_chill", "n_valid_hours", "season"]
    if hourly_df.empty:
        return pd.DataFrame(columns=cols)

    df = hourly_df.dropna(subset=["temperature"]).copy()
    df["date"] = pd.to_datetime(df["timestamp"]).dt.normalize()
    df["is_chill"] = df["temperature"].between(32, 45)  # inclusive on both ends

    daily = (
        df.groupby(["station_id", "date"])
        .agg(daily_chill=("is_chill", "sum"), n_valid_hours=("is_chill", "size"))
        .reset_index()
    )

    # Fill in days with no valid data so gaps are visible as NaN, not silently absent.
    full = []
    for sid, g in daily.groupby("station_id"):
        idx = pd.date_range(g["date"].min(), g["date"].max(), freq="D", name="date")
        g = g.set_index("date").reindex(idx)
        g["station_id"] = sid
        g["n_valid_hours"] = g["n_valid_hours"].fillna(0).astype(int)
        g["daily_chill"] = g["daily_chill"].astype(float).where(g["n_valid_hours"] > 0)
        full.append(g.reset_index())
    daily = pd.concat(full, ignore_index=True)

    daily["season"] = _season_year(daily["date"], season_start)
    daily = daily.sort_values(["station_id", "date"])
    daily["cumulative_chill"] = daily.groupby(["station_id", "season"])["daily_chill"].cumsum()
    # cumsum leaves NaN on NaN days; carry the running total forward through gaps.
    daily["cumulative_chill"] = daily.groupby(["station_id", "season"])["cumulative_chill"].ffill().fillna(0)

    return daily[cols].reset_index(drop=True)


# ----------------------------------------------------------------------------
# Function 3: calculate_gdd
# ----------------------------------------------------------------------------
def calculate_gdd(
    daily_df: pd.DataFrame,
    base_temp: float,
    season_start: str = "01-01",
) -> pd.DataFrame:
    """
    Daily and cumulative growing degree days from standardized daily data.

        GDD_d = max(0, (Tmax_d + Tmin_d) / 2 - base_temp)     (all in F)

    Output columns: station_id, date, gdd, cumulative_gdd, season

    * Days missing tmax or tmin get gdd = NaN (not 0); the cumulative total
      carries forward through them.
    * Cumulative GDD resets at season_start ("MM-DD", default Jan 1).
    * base_temp is crop-specific, in F (e.g. 50 F is a common default).
    """
    cols = ["station_id", "date", "gdd", "cumulative_gdd", "season"]
    if daily_df.empty:
        return pd.DataFrame(columns=cols)

    df = daily_df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values(["station_id", "date"])

    mean_temp = (df["tmax"] + df["tmin"]) / 2.0  # NaN if either is missing
    df["gdd"] = (mean_temp - base_temp).clip(lower=0)

    df["season"] = _season_year(df["date"], season_start)
    grp = df.groupby(["station_id", "season"])["gdd"]
    df["cumulative_gdd"] = grp.cumsum()
    df["cumulative_gdd"] = df.groupby(["station_id", "season"])["cumulative_gdd"].ffill().fillna(0)

    return df[cols].reset_index(drop=True)


# ----------------------------------------------------------------------------
# Example usage
# ----------------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Replace with Member 1's output
    selected_stations = pd.DataFrame({"station_id": ["2", "80"]})

    hourly_df = fetch_weather_data(selected_stations, "2023-11-01", "2024-02-29", "hourly")
    daily_df = fetch_weather_data(selected_stations, "2023-11-01", "2024-02-29", "daily")

    chill_df = calculate_chill(hourly_df)
    gdd_df = calculate_gdd(daily_df, base_temp=50)

    print(chill_df.tail())
    print(gdd_df.tail())