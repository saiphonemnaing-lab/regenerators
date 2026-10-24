"""Module 3: historical analogue forecasts with chronological backtesting."""
import argparse
import json
from pathlib import Path
import numpy as np
import pandas as pd
from data_processing import BASE, station_input, process_weather

METRICS = {'gdd':'gdd', 'chill_hours':'chill', 'utah_units':'chill', 'chill_portions':'chill'}


def daily_series(metrics, entity, metric):
    subset = metrics[(metrics.entity.astype(str) == str(entity)) & (metrics.scope == METRICS[metric])]
    result = subset.set_index('date')[metric].copy()
    result.index = pd.to_datetime(result.index)
    # Partial chill days do not count as complete historical analogue days.
    if metric != 'gdd':
        coverage = subset.set_index('date').coverage
        coverage.index = pd.to_datetime(coverage.index)
        result = result.where(coverage >= 1)
    return result.sort_index()


def forecast(series, as_of, horizon=14, min_years=2):
    """Use full same-calendar windows from strictly earlier anchor years.

    Issued after as_of's last hour/day. Returns future accumulation, not crop
    development milestones. p10/p90 describe historical spread, not calibrated
    prediction intervals. The current year's later observations are never read.
    """
    origin = pd.Timestamp(as_of).normalize()
    if horizon not in (7, 14):
        raise ValueError('Forecast horizon must be 7 or 14 days.')
    if min_years < 2:
        raise ValueError('At least two historical years are required.')
    samples = []
    if len(series):
        for year in range(series.index.min().year - 1, origin.year):
            try:
                anchor = origin.replace(year=year)
            except ValueError:
                continue
            index = pd.date_range(anchor + pd.Timedelta(days=1), periods=horizon)
            if index[-1] > origin:
                continue
            values = series.reindex(index)
            if values.notna().all():
                samples.append(dict(anchor_year=year, increments=values.tolist(), total=float(values.sum())))
    totals = [s['total'] for s in samples]
    result = dict(as_of=str(origin.date()), horizon=horizon, historical_years=[s['anchor_year'] for s in samples],
                  sample_count=len(samples), status='ok' if len(samples) >= min_years else 'insufficient_history',
                  method='same-calendar-window mean from prior years; complete windows only',
                  mean=None, p10=None, p90=None, daily_increments=[])
    if len(samples) >= min_years:
        result.update(mean=float(np.mean(totals)), p10=float(np.quantile(totals,.1)),
                      p90=float(np.quantile(totals,.9)),
                      daily_increments=np.mean([s['increments'] for s in samples],axis=0).tolist())
    return result


def backtest(series, horizon=14, step_days=14, min_years=2):
    """Rolling origins; forecast training always precedes the issue date."""
    rows = []
    if not len(series):
        return pd.DataFrame(rows)
    start, stop = series.index.min(), series.index.max() - pd.Timedelta(days=horizon)
    for origin in pd.date_range(start,stop,freq=f'{step_days}D'):
        actual = series.reindex(pd.date_range(origin+pd.Timedelta(days=1),periods=horizon))
        if actual.isna().any():
            continue
        predicted = forecast(series,origin,horizon,min_years)
        if predicted['status'] != 'ok':
            continue
        recent = series.reindex(pd.date_range(origin-pd.Timedelta(days=6),origin))
        persistence = float(recent.mean()*horizon) if recent.notna().all() else np.nan
        total = float(actual.sum())
        rows.append(dict(as_of=str(origin.date()),horizon=horizon,actual=total,predicted=predicted['mean'],
                         error=predicted['mean']-total,persistence=persistence,
                         historical_years=','.join(map(str,predicted['historical_years']))))
    return pd.DataFrame(rows)


def evaluate(rows):
    if rows.empty:
        return dict(status='insufficient_backtests',n=0,mae=None,rmse=None,bias=None,
                    persistence_mae=None,paired_model_mae=None,skill_vs_persistence=None)
    error = rows.error
    paired = rows.dropna(subset=['persistence'])
    baseline = float((paired.persistence-paired.actual).abs().mean()) if len(paired) else None
    paired_mae = float(paired.error.abs().mean()) if len(paired) else None
    return dict(status='ok',n=len(rows),mae=float(error.abs().mean()),
                rmse=float(np.sqrt((error**2).mean())),bias=float(error.mean()),
                persistence_mae=baseline,paired_model_mae=paired_mae,
                skill_vs_persistence=(1-paired_mae/baseline) if baseline and paired_mae is not None else None)


def predict_all(metrics,as_of,entity='orchard'):
    forecasts, evaluations, trials = [], [], []
    for metric in METRICS:
        series = daily_series(metrics,entity,metric)
        for horizon in (7,14):
            prediction = forecast(series,as_of,horizon)
            prediction.update(metric=metric,entity=entity)
            forecasts.append(prediction)
            # Evaluation only uses actuals available by this issue date.
            tested = backtest(series.loc[:pd.Timestamp(as_of)],horizon)
            evaluations.append(dict(metric=metric,horizon=horizon,entity=entity,**evaluate(tested)))
            if not tested.empty:
                tested['metric'],tested['entity'] = metric,entity
                trials.append(tested)
    return forecasts,pd.DataFrame(evaluations),pd.concat(trials,ignore_index=True) if trials else pd.DataFrame()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--selected-stations',type=Path,required=True)
    parser.add_argument('--as-of',default='2025-12-31')
    parser.add_argument('--base-f',type=float,default=50)
    parser.add_argument('--output-dir',type=Path,default=BASE/'predictions')
    args=parser.parse_args()
    selected=station_input(selected_csv=args.selected_stations)
    tables,_=process_weather(selected,base_f=args.base_f)
    entity='orchard' if 'weight' in selected else str(selected.index[0])
    forecasts,metrics,trials=predict_all(tables['daily_metrics'],args.as_of,entity)
    args.output_dir.mkdir(parents=True,exist_ok=True)
    (args.output_dir/'forecasts.json').write_text(json.dumps(forecasts,indent=2),encoding='utf-8')
    metrics.to_csv(args.output_dir/'evaluation.csv',index=False)
    trials.to_csv(args.output_dir/'backtests.csv',index=False)
    print(metrics.to_string(index=False))
    print('Saved predictions and held-out evaluation to',args.output_dir.resolve())

if __name__=='__main__':
    main()
