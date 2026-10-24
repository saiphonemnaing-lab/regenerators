"""
Orchard chill model, v2: reads CIMIS JSON files from disk (no API key needed)
Reboot the Earth Hackathon: FNRIC "Counting the Cold" challenge

Folder layout (next to this script):
    data/
        stations.json     <- the "all stations" file
        hourly/           <- any number of hourly JSON files, split however you
                             like (per station, per month, mixed). Files may
                             overlap; duplicates are dropped.

What it does:
  1. Loads hourly air temps, throws out bad readings (QC flags, impossible
     values, one-hour spikes)
  2. Blends the nearest stations into one orchard-level temperature series
  3. Chill three ways: chill hours, Utah units, Dynamic Model chill portions
  4. This season vs past seasons, plus a 2-week outlook from past years
  5. Optional: "hide one station" test to measure how accurate the blending is

Requirements: pip install pandas numpy matplotlib
"""

import json
import re
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

# =====================================================================
# CONFIG
# =====================================================================
BASE = Path(__file__).resolve().parent
STATIONS_FILE = BASE / "data" / "stations.json"
HOURLY_DIR = BASE / "data" / "hourly"

ORCHARD = {"name": "Example orchard near Davis", "lat": 38.55, "lon": -121.80, "elev_ft": 60}

N_STATIONS = 3             # how many nearby stations to blend
STATION_IDS = None         # e.g. [6, 139, 226] to choose stations yourself
MAX_DIST_MI = 75           # ignore stations farther than this

SEASON_START = (11, 1)     # chill counted from Nov 1 (say this in your pitch)
SEASON_END = (2, 28)
AS_OF = None               # None = latest date in the data, or e.g. date(2026, 1, 15)
MIN_COVERAGE = 0.80        # past seasons need data for 80% of hours to count
OUTLOOK_DAYS = 14

LAPSE_F_PER_FT = 0.0       # elevation adjustment; 0 = off (night inversions make it unreliable)
MAX_INTERP_GAP_H = 3       # fill gaps up to this many hours

# Readings flagged with these CIMIS QC codes are treated as bad. "Y" is
# included because broken-sensor stretches sometimes carry it.
BAD_QC = {"M", "Q", "R", "S", "N", "H", "Y"}
TEMP_RANGE_F = (10, 115)   # outside this = sensor error for Central Valley
MAX_SPIKE_F = 15           # one-hour jump away and back by more than this = spike

RUN_VALIDATION = True      # run the hide-one-station test on the latest full season


# =====================================================================
# 1. LOAD DATA
# =====================================================================
def _last_float(s):
    """CIMIS gives lat as e.g. "38º32'8N / 38.535694"; grab the decimal part."""
    nums = re.findall(r"-?\d+\.\d+", str(s))
    return float(nums[-1]) if nums else np.nan


def load_stations():
    with open(STATIONS_FILE, encoding="utf-8") as f:
        raw = json.load(f)
    rows = [{
        "id": int(s["StationNbr"]),
        "name": s.get("Name"),
        "lat": _last_float(s.get("HmsLatitude")),
        "lon": _last_float(s.get("HmsLongitude")),
        "elev_ft": pd.to_numeric(s.get("Elevation"), errors="coerce"),
    } for s in raw["Stations"]]
    return pd.DataFrame(rows).set_index("id")


def load_hourly():
    """Returns a table: one row per hour, one column per station (temps in F)."""
    files = sorted(HOURLY_DIR.glob("*.json")) if HOURLY_DIR.is_dir() else [HOURLY_DIR]
    if not files or not files[0].exists():
        raise FileNotFoundError(f"No hourly JSON files found in {HOURLY_DIR}")

    rows = []
    for path in files:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for provider in data["Data"]["Providers"]:
            for rec in provider["Records"]:
                v = rec.get("HlyAirTmp") or {}
                rows.append((int(rec["Station"]), rec["Date"], rec["Hour"],
                             v.get("Value"), (v.get("Qc") or "").strip()))

    df = pd.DataFrame(rows, columns=["station", "date", "hour", "value", "qc"])
    # CIMIS hours run 0100..2400 (Pacific Standard Time), stamped at the END
    # of the hour; 2400 becomes 00:00 the next day.
    df["time"] = pd.to_datetime(df["date"]) + pd.to_timedelta(df["hour"].astype(int) // 100, unit="h")
    df["temp_f"] = pd.to_numeric(df["value"], errors="coerce")

    flagged = df["qc"].isin(BAD_QC)
    out_of_range = df["temp_f"].notna() & ~df["temp_f"].between(*TEMP_RANGE_F)
    df.loc[flagged | out_of_range, "temp_f"] = np.nan
    df = df.drop_duplicates(["station", "time"], keep="last")

    wide = df.pivot(index="time", columns="station", values="temp_f").sort_index()
    wide = wide.reindex(pd.date_range(wide.index.min(), wide.index.max(), freq="h"))

    # one-hour spikes: jumps far from both the hour before and the hour after
    spikes = (wide.diff().abs() > MAX_SPIKE_F) & (wide.diff(-1).abs() > MAX_SPIKE_F)
    wide = wide.mask(spikes)

    print(f"Loaded {len(df):,} hourly readings from {len(files)} file(s), "
          f"{wide.shape[1]} station(s), {wide.index.min():%Y-%m-%d} to {wide.index.max():%Y-%m-%d}")
    print(f"  Removed: {int(flagged.sum()):,} QC-flagged, {int(out_of_range.sum()):,} out of range, "
          f"{int(spikes.sum().sum()):,} spikes\n")
    return wide


# =====================================================================
# 2. PICK + BLEND STATIONS
# =====================================================================
def miles_between(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, [lat1, lon1, lat2, lon2])
    a = (np.sin((lat2 - lat1) / 2) ** 2
         + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2)
    return 3958.8 * 2 * np.arcsin(np.sqrt(a))


def pick_stations(site, stations, available, n=N_STATIONS, ids=None, exclude=()):
    """Nearest stations to a site, among those that actually have data."""
    st = stations.loc[[s for s in available if s in stations.index and s not in exclude]]
    st = st.dropna(subset=["lat", "lon"]).copy()
    st["dist_mi"] = miles_between(site["lat"], site["lon"], st["lat"], st["lon"])
    if ids:
        return st.loc[[i for i in ids if i in st.index]]
    return st[st["dist_mi"] <= MAX_DIST_MI].nsmallest(n, "dist_mi")


def season_dates(start_year):
    return date(start_year, *SEASON_START), date(start_year + 1, *SEASON_END)


def hours_between(start, end):
    return pd.date_range(pd.Timestamp(start) + pd.Timedelta(hours=1),
                         pd.Timestamp(end) + pd.Timedelta(days=1), freq="h")


def site_series(wide, site, chosen, start, end):
    """Inverse-distance-weighted blend of the chosen stations at one site.
    Returns (hourly temps, fraction of hours with data)."""
    df = wide.reindex(hours_between(start, end))[list(chosen.index)]
    df = df.interpolate(limit=MAX_INTERP_GAP_H, limit_area="inside")
    for sid in df.columns:
        df[sid] += LAPSE_F_PER_FT * (chosen.loc[sid, "elev_ft"] - site["elev_ft"])
    w = 1 / chosen["dist_mi"].clip(lower=0.5) ** 2
    wsum = df.notna().mul(w, axis=1).sum(axis=1)
    blended = df.fillna(0).mul(w, axis=1).sum(axis=1) / wsum.replace(0, np.nan)
    blended = blended.interpolate(limit=6, limit_area="inside")
    return blended, float(blended.notna().mean())


# =====================================================================
# 3. CHILL MODELS (hourly temps in F -> hourly increments; missing hours add 0)
# =====================================================================
def chill_hours(tf):
    """Simplest model: 1 for every hour between 32 and 45 F."""
    return ((tf >= 32) & (tf <= 45)).astype(float)


def utah_units(tf):
    """Utah model (Richardson 1974). Warm hours subtract chill."""
    tc = (tf - 32) * 5 / 9
    units = np.select([tc <= 1.4, tc <= 2.4, tc <= 9.1, tc <= 12.4, tc <= 15.9, tc <= 18.0],
                      [0, 0.5, 1, 0.5, 0, -0.5], default=-1)
    return pd.Series(np.where(tf.isna(), 0, units), index=tf.index)


def dynamic_portions(tf):
    """Dynamic Model (Fishman/Erez), same equations as chillR::Dynamic_Model.
    Cold builds an intermediate product; warm hours can destroy it before
    it's locked in as a permanent chill portion. California standard."""
    tk = (tf.values - 32) * 5 / 9 + 273
    e0, e1, a0, a1, slp, tetmlt = 4153.5, 12888.8, 139500, 2.567e18, 1.6, 277
    aa, ee = a0 / a1, e1 - e0
    with np.errstate(invalid="ignore"):
        ftmprt = slp * tetmlt * (tk - tetmlt) / tk
        sr = np.exp(ftmprt)
        xi = sr / (1 + sr)
        xs = aa * np.exp(ee / tk)
        ak1 = a1 * np.exp(-e1 / tk)

    inc = np.zeros(len(tk))
    inter_e, prev_xi = 0.0, 0.0
    for i in range(len(tk)):
        if np.isnan(tk[i]):            # missing hour: hold state, add nothing
            continue
        inter_s = inter_e if inter_e < 1 else inter_e * (1 - prev_xi)
        inter_e = xs[i] - (xs[i] - inter_s) * np.exp(-ak1[i])
        inc[i] = inter_e * xi[i] if inter_e >= 1 else 0.0
        prev_xi = xi[i]
    return pd.Series(inc, index=tf.index)


def daily_cumulative(tf):
    hourly = pd.DataFrame({"chill_hours": chill_hours(tf),
                           "utah_units": utah_units(tf),
                           "chill_portions": dynamic_portions(tf)})
    hourly.index = hourly.index - pd.Timedelta(hours=1)  # put each hour on its own date
    return hourly.resample("D").sum().cumsum()


# =====================================================================
# 4. SEASONS, HISTORY, OUTLOOK
# =====================================================================
def season_of(d):
    """Season start year for a date, clamping off-season dates to the last season end."""
    if d.month >= SEASON_START[0]:
        return d.year, d
    if (d.month, d.day) <= SEASON_END:
        return d.year - 1, d
    return d.year - 1, date(d.year, *SEASON_END)   # Mar-Oct: last finished season


def history(wide, site, chosen, current_season, metric):
    cols = {}
    for y in sorted({t.year if t.month >= SEASON_START[0] else t.year - 1
                     for t in wide.index[wide[list(chosen.index)].notna().any(axis=1)]}):
        if y >= current_season:
            continue
        start, end = season_dates(y)
        tf, cov = site_series(wide, site, chosen, start, end)
        if cov < MIN_COVERAGE:
            print(f"  skipping season {y}-{(y + 1) % 100:02d}: only {cov:.0%} of hours have data")
            continue
        cols[y] = daily_cumulative(tf)[metric].reset_index(drop=True)
    return pd.DataFrame(cols)


def validate(wide, stations, season, n=N_STATIONS):
    """Hide each station, estimate its temps from its neighbors, compare chill.
    This measures how well the tool works for an orchard with no station."""
    start, end = season_dates(season)
    idx = hours_between(start, end)
    have = [s for s in wide.columns
            if s in stations.index and wide.reindex(idx)[s].notna().mean() >= MIN_COVERAGE]
    rows = []
    for s in have:
        target = stations.loc[s].to_dict()
        actual = wide.reindex(idx)[s].interpolate(limit=MAX_INTERP_GAP_H, limit_area="inside")
        near = pick_stations(target, stations, have, n=n, exclude=(s,))
        if len(near) < 2:
            continue
        blend, _ = site_series(wide, target, near, start, end)
        single, _ = site_series(wide, target, near.iloc[[0]], start, end)
        cp = lambda tf: daily_cumulative(tf)["chill_portions"].iloc[-1]
        rows.append({"station": f"{s} {target['name']}", "actual_CP": cp(actual),
                     "blend_CP": cp(blend), "nearest_only_CP": cp(single),
                     "nearest": f"{near.index[0]} ({near['dist_mi'].iloc[0]:.0f} mi)"})
    if not rows:
        print("Validation: not enough stations with full data for this season.")
        return None
    res = pd.DataFrame(rows)
    res["blend_err"] = (res["blend_CP"] - res["actual_CP"]).abs()
    res["nearest_err"] = (res["nearest_only_CP"] - res["actual_CP"]).abs()
    print(f"\nHide-one-station test, season {season}-{(season + 1) % 100:02d} (chill portions):")
    print(res.round(1).to_string(index=False))
    print(f"Average error: blend {res['blend_err'].mean():.1f} CP vs "
          f"nearest station only {res['nearest_err'].mean():.1f} CP")
    return res


# =====================================================================
# 5. RUN
# =====================================================================
def main(metric="chill_portions"):
    stations = load_stations()
    wide = load_hourly()
    chosen = pick_stations(ORCHARD, stations, wide.columns, ids=STATION_IDS)
    if chosen.empty:
        raise SystemExit(f"No stations with data within {MAX_DIST_MI} miles of the orchard.")
    print("Stations used:")
    print(chosen[["name", "dist_mi", "elev_ft"]].round(1).to_string(), "\n")

    last_data = wide[list(chosen.index)].dropna(how="all").index.max()
    current_season, as_of = season_of(AS_OF or (last_data - pd.Timedelta(hours=1)).date())
    start, _ = season_dates(current_season)
    tf, cov = site_series(wide, ORCHARD, chosen, start, as_of)
    cum = daily_cumulative(tf)
    now, day_n = cum.iloc[-1], len(cum) - 1

    unit = {"chill_portions": "chill portions", "chill_hours": "chill hours",
            "utah_units": "Utah chill units"}[metric]
    print(f"{ORCHARD['name']}: season {current_season}-{(current_season + 1) % 100:02d}, "
          f"counted from {start} to {as_of}")
    if cov < MIN_COVERAGE:
        print(f"  WARNING: only {cov:.0%} of hours have data, so totals below are undercounts.")
    print(f"  Chill portions (Dynamic): {now['chill_portions']:.1f}")
    print(f"  Chill hours (32-45 F):    {now['chill_hours']:.0f}")
    print(f"  Utah chill units:         {now['utah_units']:.0f}\n")

    hist = history(wide, ORCHARD, chosen, current_season, metric)
    outlook = None
    if hist.shape[1] >= 3:
        past = hist.iloc[min(day_n, len(hist) - 1)].dropna()
        pct = (past < now[metric]).mean() * 100
        print(f"Compared with {len(past)} past seasons on the same date: higher than {pct:.0f}% "
              f"of them (median {past.median():.1f} {unit}).")
        end_day = min(day_n + OUTLOOK_DAYS, len(hist) - 1)
        if end_day > day_n:
            gains = (hist.iloc[end_day] - hist.iloc[day_n]).dropna()
            lo, med, hi = np.percentile(gains, [10, 50, 90])
            outlook = (end_day, lo, med, hi)
            print(f"Next {OUTLOOK_DAYS} days, based on past years: +{med:.1f} {unit} "
                  f"(likely range +{lo:.1f} to +{hi:.1f}).")
        else:
            print("Season is over, so no outlook. Set AS_OF to an earlier date to demo it.")
    else:
        print(f"Only {hist.shape[1]} complete past season(s) in the data; need 3+ to compare "
              "with history and give an outlook.")

    plot(cum[metric], hist, now[metric], outlook, unit, start)

    if RUN_VALIDATION:
        full = [y for y in hist.columns] + ([current_season] if cov >= MIN_COVERAGE else [])
        if full:
            validate(wide, stations, max(full))
    return cum, hist


def plot(current, hist, now_val, outlook, unit, start):
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(10, 5))
    if hist.shape[1] >= 3:
        days = pd.date_range(start, periods=len(hist))
        ax.fill_between(days, hist.quantile(0.1, axis=1), hist.quantile(0.9, axis=1),
                        alpha=0.2, label="Past seasons (10th-90th percentile)")
        ax.plot(days, hist.median(axis=1), "--", label="Past seasons (median)")
        if outlook:
            end_day, lo, med, hi = outlook
            ax.errorbar(days[end_day], now_val + med, yerr=[[med - lo], [hi - med]],
                        fmt="o", capsize=5, label=f"{OUTLOOK_DAYS}-day outlook")
    ax.plot(current.index, current.values, lw=2.5, label="This season")
    ax.set_ylabel(unit.capitalize())
    ax.set_title(f"Chill accumulation: {ORCHARD['name']}")
    ax.legend(loc="upper left")
    fig.autofmt_xdate()
    fig.tight_layout()
    out = BASE / "chill_outlook.png"
    fig.savefig(out, dpi=150)
    print(f"\nSaved chart to {out}")


if __name__ == "__main__":
    main()