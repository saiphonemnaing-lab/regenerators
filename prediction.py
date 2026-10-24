"""
prediction.py -- Member 3: Prediction & Evaluation

Predicts end-of-period chill / GDD accumulation with a historical-average
baseline and evaluates it with a leakage-free backtest.

Functions
---------
predict_accumulation()     -> dict   (single prediction)
evaluate_predictions()     -> dict   (MAE / RMSE on actual vs predicted)
run_prediction_pipeline()  -> dict   (chill + GDD predictions + evaluation)

Assumed input schema (from Member 2's cleaned dataset)
------------------------------------------------------
One row per day, with:
    date            : datetime-like column
    chill / gdd     : daily value for the metric (the columns "chill_daily"
                      and "gdd_daily" are also accepted)
If your column names differ, pass date_col= / value_col= or edit
DATE_COL / VALUE_COL_CANDIDATES below.

Method
------
Predicted total = observed accumulation so far this season
                + average accumulation over the SAME calendar window
                  (current_date+1 -> target_date) in completed prior seasons.
Only data on or before current_date is ever used, so there is no leakage.
"""

from __future__ import annotations

from typing import Optional, Union

import numpy as np
import pandas as pd

DATE_COL = "date"
VALID_METRICS = ("chill", "gdd")
VALUE_COL_CANDIDATES = {
    "chill": ["chill", "chill_daily", "chill_portions", "chill_hours"],
    "gdd": ["gdd", "gdd_daily"],
}
# Season starts chosen for this project (month-day).
DEFAULT_SEASON_START = {"chill": "11-01", "gdd": "01-01"}


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _check_metric(metric: str) -> str:
    metric = str(metric).lower()
    if metric not in VALID_METRICS:
        raise ValueError(f"metric must be one of {VALID_METRICS}, got {metric!r}")
    return metric


def _resolve_value_col(df: pd.DataFrame, metric: str, value_col: Optional[str]) -> str:
    if value_col is not None:
        if value_col not in df.columns:
            raise KeyError(f"value_col {value_col!r} not in columns {list(df.columns)}")
        return value_col
    for cand in VALUE_COL_CANDIDATES[metric]:
        if cand in df.columns:
            return cand
    raise KeyError(
        f"No column for metric {metric!r}. Looked for "
        f"{VALUE_COL_CANDIDATES[metric]} in {list(df.columns)}. Pass value_col=."
    )


def _daily_series(df: pd.DataFrame, metric: str, date_col: str,
                  value_col: Optional[str]) -> pd.Series:
    """Return a clean daily Series indexed by normalized date."""
    if date_col not in df.columns:
        raise KeyError(f"date_col {date_col!r} not in columns {list(df.columns)}")
    col = _resolve_value_col(df, metric, value_col)
    s = df[[date_col, col]].copy()
    s[date_col] = pd.to_datetime(s[date_col]).dt.normalize()
    s = s.dropna(subset=[date_col]).drop_duplicates(subset=date_col, keep="last")
    return s.set_index(date_col)[col].astype(float).sort_index()


def _season_start_date(season_start: str, ref_date: pd.Timestamp) -> pd.Timestamp:
    """
    Most recent occurrence of the season start on or before ref_date.
    season_start may be 'MM-DD' or a full date ('2026-11-01'); only the
    month and day are used.
    """
    s = str(season_start).strip()
    parts = s.split("-")
    if len(parts) == 2:
        month, day = int(parts[0]), int(parts[1])
    else:
        d = pd.Timestamp(s)
        month, day = d.month, d.day
    start = pd.Timestamp(year=ref_date.year, month=month, day=day)
    if start > ref_date:
        start = pd.Timestamp(year=ref_date.year - 1, month=month, day=day)
    return start


def _window_sum(series: pd.Series, start: pd.Timestamp, end: pd.Timestamp) -> Optional[float]:
    """Sum of series over [start, end]; None if any day is missing."""
    if end < start:
        return 0.0
    expected = (end - start).days + 1
    window = series.loc[start:end].dropna()
    if len(window) != expected:
        return None
    return float(window.sum())


# --------------------------------------------------------------------------
# Function 1: predict_accumulation
# --------------------------------------------------------------------------
def predict_accumulation(
    historical_df: pd.DataFrame,
    current_date: str,
    target_date: str,
    metric: str,
    season_start: str,
    date_col: str = DATE_COL,
    value_col: Optional[str] = None,
) -> dict:
    """
    Predict total accumulation at target_date using a historical-average baseline.

    Returns
    -------
    {
      "metric", "current_accumulation", "predicted_remaining",
      "predicted_total", "target_date", "method",
      "n_seasons_used", "remaining_std"
    }
    """
    metric = _check_metric(metric)
    series = _daily_series(historical_df, metric, date_col, value_col)

    current = pd.Timestamp(current_date).normalize()
    target = pd.Timestamp(target_date).normalize()
    if target < current:
        raise ValueError("target_date must be on or after current_date")

    # Never look past current_date (prevents leakage).
    series = series.loc[:current]
    season_start_dt = _season_start_date(season_start, current)
    if target < season_start_dt:
        raise ValueError("target_date is before the start of the current season")

    # 1. Current season accumulation from observed data.
    current_acc = _window_sum(series, season_start_dt, current)
    if current_acc is None:
        raise ValueError(
            f"Missing daily observations between {season_start_dt.date()} "
            f"and {current.date()}; clean/fill the data first."
        )

    # 2-3. Same remaining window in completed prior seasons, then average.
    rem_start = current + pd.Timedelta(days=1)
    remaining_totals = []
    if target >= rem_start:
        max_years = current.year - series.index.min().year + 1
        for k in range(1, max_years + 1):
            shift = pd.DateOffset(years=k)
            hs, he = rem_start - shift, target - shift
            if hs < series.index.min():
                break
            val = _window_sum(series, hs, he)
            if val is not None:
                remaining_totals.append(val)
        if not remaining_totals:
            raise ValueError(
                "No complete historical seasons cover the remaining window "
                f"{rem_start.date()} -> {target.date()}."
            )
        predicted_remaining = float(np.mean(remaining_totals))
        remaining_std = float(np.std(remaining_totals, ddof=1)) if len(remaining_totals) > 1 else 0.0
    else:
        predicted_remaining, remaining_std = 0.0, 0.0

    # 4-5. Combine and return.
    return {
        "metric": metric,
        "current_accumulation": round(current_acc, 2),
        "predicted_remaining": round(predicted_remaining, 2),
        "predicted_total": round(current_acc + predicted_remaining, 2),
        "target_date": target.strftime("%Y-%m-%d"),
        "method": "historical_average",
        "n_seasons_used": len(remaining_totals),
        "remaining_std": round(remaining_std, 2),
    }


# --------------------------------------------------------------------------
# Function 2: evaluate_predictions
# --------------------------------------------------------------------------
def _pick_col(df: pd.DataFrame, explicit: Optional[str], candidates: list, label: str) -> str:
    if explicit is not None:
        if explicit not in df.columns:
            raise KeyError(f"{label} column {explicit!r} not in {list(df.columns)}")
        return explicit
    for c in candidates:
        if c in df.columns:
            return c
    raise KeyError(f"Could not find {label} column; tried {candidates} in {list(df.columns)}")


def evaluate_predictions(
    actual_df: pd.DataFrame,
    predicted_df: pd.DataFrame,
    metric: str,
    date_col: str = DATE_COL,
    actual_col: Optional[str] = None,
    predicted_col: Optional[str] = None,
) -> dict:
    """
    Compare predictions with held-out actuals (joined on date_col).

    Returns {"metric", "mae", "rmse", "bias", "n_test_samples"}.
    bias = mean(predicted - actual): positive means over-prediction.
    """
    metric = _check_metric(metric)
    a_col = _pick_col(actual_df, actual_col,
                      [f"actual_{metric}", "actual", metric, f"{metric}_actual"], "actual")
    p_col = _pick_col(predicted_df, predicted_col,
                      [f"predicted_{metric}", "predicted", f"{metric}_predicted"], "predicted")

    a = actual_df[[date_col, a_col]].rename(columns={a_col: "actual"})
    p = predicted_df[[date_col, p_col]].rename(columns={p_col: "predicted"})
    for d in (a, p):
        d[date_col] = pd.to_datetime(d[date_col]).dt.normalize()
    merged = a.merge(p, on=date_col, how="inner").dropna(subset=["actual", "predicted"])
    if merged.empty:
        raise ValueError("actual_df and predicted_df share no dates with valid values")

    err = merged["predicted"].astype(float) - merged["actual"].astype(float)
    return {
        "metric": metric,
        "mae": round(float(err.abs().mean()), 2),
        "rmse": round(float(np.sqrt((err ** 2).mean())), 2),
        "bias": round(float(err.mean()), 2),
        "n_test_samples": int(len(merged)),
    }


# --------------------------------------------------------------------------
# Backtest helper (builds the actual/predicted frames for evaluation)
# --------------------------------------------------------------------------
def _backtest_frames(historical_df, current_date, target_date, metric, season_start,
                     date_col, value_col):
    """
    Re-run the forecast as if it were made in earlier years (same calendar
    origin and target) and pair each with what actually happened.
    Each forecast only uses data up to its own origin date.
    """
    series = _daily_series(historical_df, metric, date_col, value_col)
    current = pd.Timestamp(current_date).normalize()
    target = pd.Timestamp(target_date).normalize()
    actual_rows, pred_rows = [], []

    for k in range(1, current.year - series.index.min().year + 2):
        shift = pd.DateOffset(years=k)
        origin, tgt = current - shift, target - shift
        if origin < series.index.min():
            break
        ss = _season_start_date(season_start, origin)
        actual_total = _window_sum(series, ss, tgt)  # what really happened
        if actual_total is None:
            continue
        try:
            pred = predict_accumulation(
                historical_df, origin.strftime("%Y-%m-%d"), tgt.strftime("%Y-%m-%d"),
                metric, season_start, date_col=date_col, value_col=value_col)
        except ValueError:
            continue  # not enough prior seasons yet
        actual_rows.append({date_col: tgt, f"actual_{metric}": actual_total})
        pred_rows.append({date_col: tgt, f"predicted_{metric}": pred["predicted_total"]})

    return pd.DataFrame(actual_rows), pd.DataFrame(pred_rows)


# --------------------------------------------------------------------------
# Function 3: run_prediction_pipeline
# --------------------------------------------------------------------------
def run_prediction_pipeline(
    chill_df: pd.DataFrame,
    gdd_df: pd.DataFrame,
    current_date: str,
    target_date: str,
    season_start: Optional[Union[str, dict]] = None,
    date_col: str = DATE_COL,
) -> dict:
    """
    Predict chill and GDD, then backtest each on prior seasons.

    season_start: None (use DEFAULT_SEASON_START: chill Nov 1, GDD Jan 1),
                  a dict like {"chill": "11-01", "gdd": "01-01"},
                  or one string applied to both metrics.

    Returns
    -------
    {
      "chill_prediction": {...},
      "gdd_prediction": {...},
      "evaluation": {"chill": {...} | None, "gdd": {...} | None}
    }
    """
    if season_start is None:
        starts = dict(DEFAULT_SEASON_START)
    elif isinstance(season_start, dict):
        starts = {**DEFAULT_SEASON_START, **season_start}
    else:
        starts = {m: season_start for m in VALID_METRICS}

    result = {"evaluation": {}}
    for metric, df in (("chill", chill_df), ("gdd", gdd_df)):
        result[f"{metric}_prediction"] = predict_accumulation(
            df, current_date, target_date, metric, starts[metric], date_col=date_col)

        actual_df, pred_df = _backtest_frames(
            df, current_date, target_date, metric, starts[metric], date_col, None)
        result["evaluation"][metric] = (
            evaluate_predictions(actual_df, pred_df, metric, date_col=date_col)
            if len(actual_df) else None  # not enough history to backtest
        )
    # keep key order stable for Member 4
    return {
        "chill_prediction": result["chill_prediction"],
        "gdd_prediction": result["gdd_prediction"],
        "evaluation": result["evaluation"],
    }


if __name__ == "__main__":
    # Quick demo with synthetic data (replace with Member 2's output).
    rng = np.random.default_rng(0)
    days = pd.date_range("2016-01-01", "2026-12-31", freq="D")
    doy = days.dayofyear.values
    chill = np.clip(8 * np.cos((doy - 15) / 365 * 2 * np.pi) + rng.normal(0, 2, len(days)), 0, None)
    gdd = np.clip(12 - 12 * np.cos((doy - 15) / 365 * 2 * np.pi) + rng.normal(0, 2, len(days)), 0, None)
    chill_df = pd.DataFrame({"date": days, "chill": chill})
    gdd_df = pd.DataFrame({"date": days, "gdd": gdd})
    out = run_prediction_pipeline(chill_df, gdd_df, "2026-12-01", "2026-12-31")
    import json
    print(json.dumps(out, indent=2))