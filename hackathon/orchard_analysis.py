"""Orchard input, station selection, and observed historical chill/GDD analysis."""
import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
from hackathon import BAD_QC, chill_hours, utah_units, dynamic_portions, miles_between, _last_float

BASE = Path(__file__).resolve().parent


def validate_site(lat, lon, elevation_ft):
    values = [float(lat), float(lon), float(elevation_ft)]
    if not all(math.isfinite(v) for v in values):
        raise ValueError('Coordinates and elevation must be finite numbers.')
    if not -90 <= values[0] <= 90 or not -180 <= values[1] <= 180:
        raise ValueError('Latitude must be -90..90 and longitude -180..180.')
    return dict(lat=values[0], lon=values[1], elev_ft=values[2])


def load_metadata(data_dir):
    raw = json.loads((Path(data_dir) / 'stations.json').read_text(encoding='utf-8-sig'))
    return pd.DataFrame([dict(
        station_id=int(s['StationNbr']), name=s.get('Name', ''),
        lat=_last_float(s.get('HmsLatitude')), lon=_last_float(s.get('HmsLongitude')),
        elev_ft=pd.to_numeric(s.get('Elevation'), errors='coerce'),
        active=str(s.get('IsActive', '')).lower() == 'true',
    ) for s in raw['Stations']]).set_index('station_id')


def load_temperatures(data_dir, scope):
    """Return cleaned Fahrenheit tables, retaining missing periods as NaN."""
    fields = ['HlyAirTmp'] if scope == 'hourly' else ['DayAirTmpMin', 'DayAirTmpMax']
    rows = []
    for path in sorted((Path(data_dir) / scope).glob('*.json')):
        doc = json.loads(path.read_text(encoding='utf-8-sig'))
        for provider in doc['Data']['Providers']:
            for rec in provider['Records']:
                if rec.get('Scope') != scope:
                    continue
                if str(rec.get('Standard', '')).lower() != 'english':
                    raise ValueError(f'{path.name}: Fahrenheit/English records required.')
                time = pd.Timestamp(rec['Date'])
                if scope == 'hourly':
                    hour = int(rec['Hour'])
                    if hour % 100 or not 100 <= hour <= 2400:
                        raise ValueError(f'Invalid CIMIS end-of-hour label: {hour}')
                    time += pd.Timedelta(hours=hour // 100)
                row = dict(station_id=int(rec['Station']), time=time)
                for field in fields:
                    item = rec.get(field) or {}
                    value = pd.to_numeric(item.get('Value'), errors='coerce')
                    # Broad physical sanity check; source QC flags provide local screening.
                    if (item.get('Qc') or '').strip() in BAD_QC or not -100 <= value <= 140:
                        value = np.nan
                    row[field] = value
                if scope == 'daily' and row[fields[0]] > row[fields[1]]:
                    row[fields[0]] = row[fields[1]] = np.nan
                rows.append(row)
    if not rows:
        raise ValueError(f'No {scope} temperature records in {data_dir}. Download data first.')
    frame = pd.DataFrame(rows).drop_duplicates(['station_id', 'time'], keep='last')
    index = pd.date_range(frame.time.min(), frame.time.max(), freq='h' if scope == 'hourly' else 'D')
    result = {f: frame.pivot(index='time', columns='station_id', values=f).reindex(index) for f in fields}
    if scope == 'hourly':
        t = result['HlyAirTmp']
        before, after = t.diff(), t.diff(-1)
        result['HlyAirTmp'] = t.mask((before.abs() > 15) & (after.abs() > 15) & (before * after > 0))
    return result


def select_stations(site, metadata, hourly, daily, max_distance_mi=75,
                    max_elevation_diff_ft=500, min_coverage=0.8):
    """Select exactly three suitable stations with BOTH local data sources.

    Coverage is measured over each source's entire loaded time range, before
    blending or imputation. Score = distance/radius + elevation difference/tolerance.
    This is an explicit heuristic, not a calibrated microclimate model.
    """
    validate_site(site['lat'], site['lon'], site['elev_ft'])
    if not all(math.isfinite(x) and x > 0 for x in [max_distance_mi, max_elevation_diff_ft]):
        raise ValueError('Distance and elevation limits must be positive finite numbers.')
    if not 0 < min_coverage <= 1:
        raise ValueError('Minimum coverage must be in (0, 1].')
    candidates = metadata.dropna(subset=['lat', 'lon', 'elev_ft']).copy()
    candidates['dist_mi'] = miles_between(site['lat'], site['lon'], candidates.lat, candidates.lon)
    candidates['elev_diff_ft'] = (candidates.elev_ft - site['elev_ft']).abs()
    # Chill-source availability is evaluated in winter, not missing summer hours.
    winter = (hourly.index - pd.Timedelta(hours=1)).month.isin([11, 12, 1, 2])
    coverage_source = hourly.loc[winter] if winter.any() else hourly
    candidates['hourly_coverage'] = coverage_source.notna().mean().reindex(candidates.index).fillna(0)
    valid_daily = daily['DayAirTmpMin'].notna() & daily['DayAirTmpMax'].notna()
    candidates['daily_coverage'] = valid_daily.mean().reindex(candidates.index).fillna(0)
    eligible = candidates[
        (candidates.dist_mi <= max_distance_mi) &
        (candidates.elev_diff_ft <= max_elevation_diff_ft) &
        (candidates.hourly_coverage >= min_coverage) &
        (candidates.daily_coverage >= min_coverage)
    ].copy()
    eligible['score'] = eligible.dist_mi / max_distance_mi + eligible.elev_diff_ft / max_elevation_diff_ft
    eligible = eligible.reset_index().sort_values(['score', 'dist_mi', 'station_id']).set_index('station_id')
    if len(eligible) < 3:
        nearby = candidates[(candidates.dist_mi <= max_distance_mi) & (candidates.elev_diff_ft <= max_elevation_diff_ft)]
        ids = ', '.join(str(i) for i in nearby.sort_values('dist_mi').head(10).index)
        raise ValueError(f'Only {len(eligible)} suitable stations have sufficient hourly AND daily data; need 3. '
                         f'Download temperatures for nearby station IDs [{ids}], or explicitly adjust limits. '
                         'No distant fallback stations were substituted.')
    selected = eligible.head(3).copy()
    weights = 1 / selected.dist_mi.clip(lower=0.5).pow(2)
    selected['weight'] = weights / weights.sum()
    selected['reason'] = [f'{r.dist_mi:.1f} mi away; elevation difference {r.elev_diff_ft:.0f} ft; '
                          f'hourly coverage {r.hourly_coverage:.1%}; daily coverage {r.daily_coverage:.1%}'
                          for _, r in selected.iterrows()]
    return selected


def blend_temperatures(table, selected):
    """Inverse-distance blend of available stations; no temperature imputation."""
    data = table.reindex(columns=selected.index)
    weights = selected.weight
    denominator = data.notna().mul(weights).sum(axis=1)
    return data.mul(weights).sum(axis=1).div(denominator.where(denominator > 0))


def growing_degree_days(tmin, tmax, base_f):
    """Simple average method, no upper cutoff; missing temperatures stay missing."""
    if not math.isfinite(base_f):
        raise ValueError('GDD base temperature must be finite.')
    return ((tmin + tmax) / 2 - base_f).clip(lower=0).where(tmin <= tmax)


def analyze_series(hourly, daily_min, daily_max, base_f, entity, min_coverage=0.8):
    """Daily increments/cumulative observed sums and season coverage summaries.

    Chill seasons: Nov 1-Feb 28, matching the original model. GDD: calendar
    years. No missing values are filled and incomplete periods are labelled.
    """
    curves, summaries = [], []
    hour_dates = hourly.index - pd.Timedelta(hours=1)
    chill_years = sorted(set(hour_dates[hour_dates.month.isin([11, 12, 1, 2])].map(
        lambda t: t.year if t.month >= 11 else t.year - 1)))
    for year in chill_years:
        start, end = pd.Timestamp(year, 11, 1), pd.Timestamp(year + 1, 2, 28)
        idx = pd.date_range(start + pd.Timedelta(hours=1), end + pd.Timedelta(days=1), freq='h')
        t = hourly.reindex(idx)
        increments = pd.DataFrame(dict(chill_hours=chill_hours(t), utah_units=utah_units(t),
                                       chill_portions=dynamic_portions(t))).where(t.notna(), np.nan)
        increments.index -= pd.Timedelta(hours=1)
        daily = increments.resample('D').sum(min_count=1)
        valid_hours = t.notna().set_axis(idx - pd.Timedelta(hours=1)).resample('D').sum()
        daily['valid_hours'] = valid_hours
        daily['coverage'] = valid_hours / 24
        for metric in ['chill_hours', 'utah_units', 'chill_portions']:
            daily[f'cumulative_{metric}'] = daily[metric].cumsum()
        daily['entity'], daily['season'], daily['scope'] = entity, f'{year}-{year+1}', 'chill'
        curves.append(daily.rename_axis('date').reset_index())
        coverage = float(t.notna().mean())
        summaries.append(dict(entity=entity, scope='chill', season=f'{year}-{year+1}',
                              expected_samples=len(idx), valid_samples=int(t.notna().sum()),
                              coverage=coverage, eligible_for_comparison=coverage >= min_coverage,
                              incomplete=coverage < 1, **{m: float(daily[m].sum(min_count=1))
                              for m in ['chill_hours', 'utah_units', 'chill_portions']}))
    for year in sorted(set(daily_min.index.year)):
        idx = pd.date_range(f'{year}-01-01', f'{year}-12-31')
        lo, hi = daily_min.reindex(idx), daily_max.reindex(idx)
        gdd = growing_degree_days(lo, hi, base_f)
        daily = pd.DataFrame(dict(tmin_f=lo, tmax_f=hi, gdd=gdd, cumulative_gdd=gdd.cumsum(),
                                  coverage=gdd.notna().astype(float)))
        daily['entity'], daily['season'], daily['scope'] = entity, str(year), 'gdd'
        curves.append(daily.rename_axis('date').reset_index())
        coverage = float(gdd.notna().mean())
        summaries.append(dict(entity=entity, scope='gdd', season=str(year),
                              expected_samples=len(idx), valid_samples=int(gdd.notna().sum()),
                              coverage=coverage, eligible_for_comparison=coverage >= min_coverage,
                              incomplete=coverage < 1, gdd=float(gdd.sum(min_count=1))))
    return pd.concat(curves, ignore_index=True), pd.DataFrame(summaries)


def run_analysis(site, data_dir=BASE/'data', base_f=50, max_distance_mi=75,
                 max_elevation_diff_ft=500, min_coverage=0.8):
    """Reusable entry point for a future dashboard; returns pandas tables."""
    site = validate_site(site['lat'], site['lon'], site['elev_ft'])
    if not math.isfinite(base_f):
        raise ValueError('GDD base temperature must be finite.')
    metadata = load_metadata(data_dir)
    hourly = load_temperatures(data_dir, 'hourly')['HlyAirTmp']
    daily = load_temperatures(data_dir, 'daily')
    selected = select_stations(site, metadata, hourly, daily, max_distance_mi,
                               max_elevation_diff_ft, min_coverage)
    # Use identical contributing stations for daily min and max temperatures.
    common = daily['DayAirTmpMin'].notna() & daily['DayAirTmpMax'].notna()
    lo, hi = daily['DayAirTmpMin'].where(common), daily['DayAirTmpMax'].where(common)
    all_curves, all_summaries = [], []
    series = [('orchard', blend_temperatures(hourly, selected),
               blend_temperatures(lo, selected), blend_temperatures(hi, selected))]
    series += [(str(sid), hourly[sid], lo[sid], hi[sid]) for sid in selected.index]
    for entity, temp, tmin, tmax in series:
        curves, summary = analyze_series(temp, tmin, tmax, base_f, entity, min_coverage)
        all_curves.append(curves); all_summaries.append(summary)
    return dict(selected_stations=selected.reset_index(),
                daily_analysis=pd.concat(all_curves, ignore_index=True),
                season_summary=pd.concat(all_summaries, ignore_index=True))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['lat', 'lon', 'elevation-ft']:
        parser.add_argument('--'+name, type=float)
    parser.add_argument('--base-f', type=float, default=50, help='GDD base F; demo default 50, set for your crop')
    parser.add_argument('--max-distance-mi', type=float, default=75)
    parser.add_argument('--max-elevation-diff-ft', type=float, default=500)
    parser.add_argument('--min-coverage', type=float, default=0.8)
    parser.add_argument('--data-dir', type=Path, default=BASE/'data')
    parser.add_argument('--output-dir', type=Path, default=BASE/'results')
    args = parser.parse_args(argv)
    try:
        lat = args.lat if args.lat is not None else float(input('Orchard latitude: '))
        lon = args.lon if args.lon is not None else float(input('Orchard longitude (west is negative): '))
        elev = args.elevation_ft if args.elevation_ft is not None else float(input('Elevation in feet: '))
        site = validate_site(lat, lon, elev)
        results = run_analysis(site, args.data_dir, args.base_f, args.max_distance_mi,
                               args.max_elevation_diff_ft, args.min_coverage)
    except (ValueError, OSError, KeyError, EOFError) as error:
        parser.error(str(error))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, table in results.items():
        table.to_csv(args.output_dir/f'{name}.csv', index=False)
    config = dict(site=site, base_f=args.base_f, gdd_method='simple_average_no_upper_cutoff',
                  gdd_season='January 1-December 31', chill_season='November 1-February 28',
                  max_distance_mi=args.max_distance_mi, max_elevation_diff_ft=args.max_elevation_diff_ft,
                  min_coverage=args.min_coverage, temperature_imputation=False,
                  selection_score='distance/max_distance + elevation_difference/max_elevation_difference',
                  blend='inverse squared distance, floor 0.5 mile; renormalized over available stations',
                  notes=['Totals sum observed increments; missing values are not zero-temperature days.',
                         'Partial seasons and missing data can bias totals. Check coverage before comparison.',
                         '50 F is a demo base, not a crop recommendation.',
                         'Chill portions reuse the existing model; missing hours hold its state.'])
    (args.output_dir/'analysis_config.json').write_text(json.dumps(config, indent=2), encoding='utf-8')
    print(results['selected_stations'][['station_id', 'name', 'dist_mi', 'elev_diff_ft', 'weight', 'reason']].to_string(index=False))
    print('\nOrchard observed totals (incomplete periods are marked):')
    print(results['season_summary'].query("entity == 'orchard'").round(3).to_string(index=False))
    print(f'\nSaved results to {args.output_dir.resolve()}')
    return results


if __name__ == '__main__':
    main()
