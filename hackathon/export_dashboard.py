import json
import math
from orchard_analysis import BASE, load_metadata, load_temperatures

metadata=load_metadata(BASE/'data')
hourly=load_temperatures(BASE/'data','hourly')['HlyAirTmp']
daily=load_temperatures(BASE/'data','daily')
hourly=hourly.loc[(hourly.index - __import__('pandas').Timedelta(hours=1)).month.isin([11,12,1,2])]
clean=lambda x:float(x) if math.isfinite(float(x)) else None
stations=[]
for sid,r in metadata.iterrows():
    if not all(math.isfinite(r[k]) for k in ['lat','lon','elev_ft']):continue
    stations.append(dict(id=int(sid),name=r['name'],lat=r.lat,lon=r.lon,elevation=r.elev_ft,active=bool(r.active)))
weather={}
for sid in hourly.columns:
    if sid not in daily['DayAirTmpMin']:continue
    pair=daily['DayAirTmpMin'][sid].notna() & daily['DayAirTmpMax'][sid].notna()
    weather[str(sid)]=dict(
        hourly=[[t.strftime('%Y-%m-%dT%H:%M:%S'),clean(v)] for t,v in hourly[sid].items()],
        daily=[[t.strftime('%Y-%m-%d'),clean(daily['DayAirTmpMin'].loc[t,sid]),
                clean(daily['DayAirTmpMax'].loc[t,sid])] for t in daily['DayAirTmpMin'].index],
        hourlyCoverage=float(hourly[sid].notna().mean()),dailyCoverage=float(pair.mean()))
payload=dict(stations=stations,weather=weather,source='California DWR CIMIS',
             hourlyWindow=['2022-11-01','2026-01-10'],dailyWindow=['2023-01-01','2025-12-31'],
             availableThrough='2025-12-31',retrieved='2026-10-24',
             note='Earlier winters Nov 1-Feb 28, plus Nov 2025-Jan 2026. No summer hourly data. QC-filtered; missing values retained.')
out=BASE/'dashboard'/'dist'/'weather.json'
out.write_text(json.dumps(payload,separators=(',',':'),allow_nan=False),encoding='utf-8')
print(out, out.stat().st_size)
