"""Member 2: collection, cleaning, and historical chill/GDD exports."""
import argparse
import hashlib
import json
import math
import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import numpy as np
import pandas as pd
from orchard_analysis import BASE, load_temperatures, analyze_series, blend_temperatures, growing_degree_days

ENDPOINT = 'https://et.water.ca.gov/StationWeb/GetDataByStationNumber'


def station_input(stations=None, selected_csv=None):
    selected = pd.read_csv(selected_csv) if selected_csv else pd.DataFrame({'station_id':stations or []})
    if 'station_id' not in selected:
        raise ValueError('CSV needs a station_id column.')
    ids = pd.to_numeric(selected.station_id, errors='coerce')
    if ids.isna().any() or (ids <= 0).any() or (ids % 1 != 0).any():
        raise ValueError('Station IDs must be positive integers.')
    selected['station_id'] = ids.astype(int)
    if len(selected) != 3 or selected.station_id.duplicated().any():
        raise ValueError('Provide three distinct selected stations.')
    if 'weight' in selected:
        w = pd.to_numeric(selected.weight, errors='coerce')
        if not np.isfinite(w).all() or (w <= 0).any():
            raise ValueError('Weights must be positive finite numbers.')
        selected['weight'] = w / w.sum()
    return selected.set_index('station_id')


def date_window(start, end):
    if (start is None) != (end is None):
        raise ValueError('Provide both start and end dates.')
    if start is None:
        return None
    a, b = date.fromisoformat(start), date.fromisoformat(end)
    if a > b:
        raise ValueError('Start date must precede end date.')
    return a, b


def validate_response(payload, stations, scope, start, end):
    try:
        records = [r for p in payload['Data']['Providers'] for r in p['Records']]
    except (KeyError, TypeError):
        raise ValueError('Invalid CIMIS data envelope.') from None
    if not records:
        raise ValueError('CIMIS returned no records.')
    seen = set()
    for r in records:
        sid, day = int(r['Station']), date.fromisoformat(r['Date'])
        if sid not in stations or r.get('Scope') != scope or not start <= day <= end:
            raise ValueError('Unexpected station, date, or scope.')
        if str(r.get('Standard', '')).lower() != 'english':
            raise ValueError('English/Fahrenheit data required.')
        key = sid, day, r.get('Hour')
        if key in seen:
            raise ValueError('Duplicate observations in batch.')
        seen.add(key)
    return records


def download_weather(ids, data_dir, hourly_window, daily_window, api_key=None,
                     opener=urlopen, pause=time.sleep):
    """Bounded batches, validated cache, header authentication, three retries."""
    if hourly_window is None or daily_window is None:
        raise ValueError('Download requires explicit hourly and daily date ranges.')
    batches = []
    for scope, window in [('hourly', hourly_window), ('daily', daily_window)]:
        first, last = window
        folder = Path(data_dir) / scope
        folder.mkdir(parents=True, exist_ok=True)
        days = min(31, max(1, 1500 // (len(ids) * (24 if scope == 'hourly' else 3))))
        cursor = first
        while cursor <= last:
            stop = min(last, cursor + timedelta(days=days-1))
            path = folder / f"{'-'.join(map(str, ids))}_{cursor}_to_{stop}.json"
            cached = False
            if path.exists():
                try:
                    payload = json.loads(path.read_text(encoding='utf-8-sig'))
                    records = validate_response(payload, ids, scope, cursor, stop)
                    cached = True
                except (ValueError, KeyError, TypeError):
                    pass
            if not cached:
                key = api_key or os.environ.get('CIMIS_APP_KEY')
                if not key:
                    raise ValueError('Set CIMIS_APP_KEY to download uncached data.')
                params = dict(stationNbrs=','.join(map(str, ids)), startDate=str(cursor),
                              endDate=str(stop), isHourly=str(scope == 'hourly').lower(),
                              unitOfMeasure='E', dataItems='hly-air-tmp' if scope == 'hourly'
                              else 'day-air-tmp-min,day-air-tmp-max,day-air-tmp-avg')
                request = Request(ENDPOINT + '?' + urlencode(params),
                                  headers={'Ocp-Apim-Subscription-Key':key, 'Accept':'application/json'})
                for attempt in range(3):
                    try:
                        with opener(request, timeout=45) as response:
                            payload = json.loads(response.read().decode('utf-8-sig'))
                        records = validate_response(payload, ids, scope, cursor, stop)
                        break
                    except Exception:
                        if attempt == 2:
                            raise ValueError(f'CIMIS failed for {scope} {cursor} to {stop}; '
                                             'no valid batch saved. Credential details suppressed.') from None
                        pause(2**attempt)
                temporary = path.with_suffix('.json.tmp')
                temporary.write_text(json.dumps(payload), encoding='utf-8')
                temporary.replace(path)
            batches.append(dict(scope=scope, start=str(cursor), end=str(stop), records=len(records),
                                cached=cached, file=str(path.resolve())))
            cursor = stop + timedelta(days=1)
    return batches


def _window_table(table, window, hourly=False):
    if window is None:
        return table
    start, end = map(pd.Timestamp, window)
    index = pd.date_range(start + pd.Timedelta(hours=1) if hourly else start,
                          end + pd.Timedelta(days=1) if hourly else end,
                          freq='h' if hourly else 'D')
    return table.reindex(index)


def source_audit(data_dir, ids, hourly_window, daily_window):
    rows, files = [], []
    fields = {'hourly':['HlyAirTmp'], 'daily':['DayAirTmpMin','DayAirTmpMax']}
    for scope, window in [('hourly',hourly_window), ('daily',daily_window)]:
        for path in sorted((Path(data_dir) / scope).glob('*.json')):
            raw = path.read_bytes()
            files.append(dict(file=str(path.resolve()), sha256=hashlib.sha256(raw).hexdigest()))
            doc = json.loads(raw.decode('utf-8-sig'))
            for provider in doc['Data']['Providers']:
                for rec in provider['Records']:
                    sid, day = int(rec['Station']), date.fromisoformat(rec['Date'])
                    if rec.get('Scope') != scope or sid not in ids:
                        continue
                    if window and not window[0] <= day <= window[1]:
                        continue
                    for field in fields[scope]:
                        value = rec.get(field) or {}
                        rows.append(dict(scope=scope, station_id=sid, source_date=str(day),
                                         hour=rec.get('Hour',''), field=field,
                                         raw_value=value.get('Value'), qc=value.get('Qc'),
                                         unit=value.get('Unit'), source_file=path.name))
    return pd.DataFrame(rows), files


def process_weather(selected, data_dir=BASE/'data', base_f=50, min_coverage=.8,
                    hourly_window=None, daily_window=None):
    """Consume selected stations without rerunning module 1."""
    if not math.isfinite(base_f) or not 0 < min_coverage <= 1:
        raise ValueError('Base temperature must be finite and coverage in (0, 1].')
    hourly = _window_table(load_temperatures(data_dir,'hourly')['HlyAirTmp'], hourly_window, True)
    daily = {k:_window_table(v,daily_window) for k,v in load_temperatures(data_dir,'daily').items()}
    ids = list(selected.index)
    missing = [sid for sid in ids if sid not in hourly or sid not in daily['DayAirTmpMin']]
    if missing:
        raise ValueError(f'Missing hourly/daily sources for stations: {missing}. Download first.')
    hourly = hourly[ids]
    lo, hi = daily['DayAirTmpMin'][ids], daily['DayAirTmpMax'][ids]
    clean_hourly, clean_daily, curves, summaries, quality = [], [], [], [], []
    for sid in ids:
        clean_hourly.append(pd.DataFrame(dict(timestamp_end_pst=hourly.index,
                            source_date=(hourly.index-pd.Timedelta(hours=1)).date,
                            station_id=sid, temp_f=hourly[sid].values,
                            valid=hourly[sid].notna().values)))
        gdd = growing_degree_days(lo[sid],hi[sid],base_f)
        clean_daily.append(pd.DataFrame(dict(date=lo.index, station_id=sid, tmin_f=lo[sid].values,
                           tmax_f=hi[sid].values, gdd=gdd.values, valid=gdd.notna().values)))
        for scope, valid in [('hourly',hourly[sid].notna()), ('daily',gdd.notna())]:
            quality.append(dict(station_id=sid, scope=scope, expected_samples=len(valid),
                                valid_samples=int(valid.sum()), missing_or_rejected=int((~valid).sum()),
                                coverage=float(valid.mean())))
        c, s = analyze_series(hourly[sid],lo[sid],hi[sid],base_f,str(sid),min_coverage)
        curves.append(c)
        summaries.append(s)
    if 'weight' in selected:
        common = lo.notna() & hi.notna()
        c, s = analyze_series(blend_temperatures(hourly,selected),
                              blend_temperatures(lo.where(common),selected),
                              blend_temperatures(hi.where(common),selected),
                              base_f,'orchard',min_coverage)
        curves.append(c)
        summaries.append(s)
    audit, provenance = source_audit(data_dir,ids,hourly_window,daily_window)
    return dict(clean_hourly=pd.concat(clean_hourly,ignore_index=True),
                clean_daily=pd.concat(clean_daily,ignore_index=True),
                daily_metrics=pd.concat(curves,ignore_index=True),
                season_summary=pd.concat(summaries,ignore_index=True),
                quality_report=pd.DataFrame(quality), source_audit=audit), provenance


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--selected-stations',type=Path,help='Module 1 selected_stations.csv')
    group.add_argument('--stations',type=int,nargs=3,help='Three station IDs')
    parser.add_argument('--data-dir',type=Path,default=BASE/'data')
    parser.add_argument('--output-dir',type=Path,default=BASE/'processed')
    parser.add_argument('--base-f',type=float,default=50)
    parser.add_argument('--min-coverage',type=float,default=.8)
    parser.add_argument('--download',action='store_true')
    for scope in ['hourly','daily']:
        for edge in ['start','end']:
            parser.add_argument(f'--{scope}-{edge}')
    args = parser.parse_args(argv)
    try:
        selected = station_input(args.stations,args.selected_stations)
        hourly = date_window(args.hourly_start,args.hourly_end)
        daily = date_window(args.daily_start,args.daily_end)
        batches = download_weather(list(selected.index),args.data_dir,hourly,daily) if args.download else []
        tables, provenance = process_weather(selected,args.data_dir,args.base_f,args.min_coverage,hourly,daily)
        args.output_dir.mkdir(parents=True,exist_ok=True)
        for name,table in tables.items():
            table.to_csv(args.output_dir/f'{name}.csv',index=False)
        selected.reset_index().to_csv(args.output_dir/'selected_stations.csv',index=False)
        manifest = dict(schema_version=1, created_utc=datetime.now(timezone.utc).isoformat(),
                        api_endpoint=ENDPOINT, station_ids=list(selected.index), base_f=args.base_f,
                        min_coverage=args.min_coverage, units='Fahrenheit; GDD in Fahrenheit-days',
                        time_basis='CIMIS fixed Pacific Standard Time, end-of-hour; no DST conversion',
                        hourly_window=[str(x) for x in hourly] if hourly else 'loaded source range',
                        daily_window=[str(x) for x in daily] if daily else 'loaded source range',
                        imputation=False, duplicate_policy='last record in sorted filename order wins',
                        chill_season='Nov 1-Feb 28; Feb 29 excluded', gdd_season='Jan 1-Dec 31',
                        gdd_method='simple average; no upper cutoff; base_f is a configurable demo parameter',
                        qc_policy='reject M,Q,R,S,N,H,Y; reject outside -100..140F; inverted extrema; isolated >15F spikes',
                        limitations=['Missing values stay missing; totals sum observed increments.',
                                     'Dynamic Model holds state across missing hours; incomplete estimates may be biased.',
                                     'Coverage eligibility is not an accuracy validation.'],
                        downloads=batches, input_files=provenance,
                        output_rows={name:len(table) for name,table in tables.items()})
        (args.output_dir/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    except (ValueError,OSError,KeyError,TypeError) as error:
        parser.error(str(error))
    print(tables['quality_report'].round(4).to_string(index=False))
    print(f'Wrote cleaned weather, chill/GDD, QC audit, and manifest to {args.output_dir.resolve()}')
    return tables


if __name__ == '__main__':
    main()
