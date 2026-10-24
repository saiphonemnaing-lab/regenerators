import assert from 'node:assert/strict';
import fs from 'node:fs';
import {selectStations,buildMetrics,forecast,backtest,stamp,iso} from './dist/engine.mjs';
const data=JSON.parse(fs.readFileSync(new URL('./dist/weather.json',import.meta.url)));
const reference=JSON.parse(fs.readFileSync(new URL('./reference.json',import.meta.url)));
const site={lat:38.55,lon:-121.8,elevation:60,base:50,radius:75,elevationLimit:500};
const selected=selectStations(data,site);
assert.deepEqual(selected.map(s=>s.id),[6,226,139]);
assert.throws(()=>selectStations(data,{...site,lat:0,lon:0}),/Fewer than three/);
const metrics=buildMetrics(selected);
let checked=0;
for(const expected of reference.forecasts){
 const actual=forecast(metrics.series[expected.metric],expected.as_of,expected.horizon);
 assert.equal(actual.status,expected.status);
 assert.deepEqual(actual.years,expected.historical_years);
 for(const key of ['mean','p10','p90'])assert.ok(Math.abs(actual[key]-expected[key])<1e-8,key+' mismatch '+expected.metric);
 checked++;
}
for(const expected of reference.scores){
 const actual=backtest(metrics.series[expected.metric],'2025-12-31',expected.horizon);
 assert.equal(actual.n,expected.n);
 for(const key of ['mae','rmse','bias'])assert.ok(Math.abs(actual[key]-expected[key])<1e-8,key+' mismatch '+expected.metric);
 checked++;
}
const before=forecast(metrics.series.gdd,'2025-06-01');
for(const [day]of metrics.series.gdd)if(day>'2025-06-01')metrics.series.gdd.set(day,999999);
assert.deepEqual(forecast(metrics.series.gdd,'2025-06-01'),before);
assert.equal(iso(stamp('2025-01-01')),'2025-01-01');
console.log(checked+' forecast/evaluation parity checks passed; station selection and future-data exclusion passed.');
