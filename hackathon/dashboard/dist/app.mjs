import {DAY,iso,stamp,selectStations,buildMetrics,forecast,backtest,accumulation} from './engine.mjs';
const $=id=>document.getElementById(id);
const definitions={
  chill_hours:{name:'Chill hours',unit:'chill hours',description:'Hours between 32°F and 45°F'},
  gdd:{name:'Growing degree days',unit:'°F-days',description:'Simple-average GDD · configurable base'},
  chill_portions:{name:'Dynamic chill portions',unit:'chill portions',description:'Dynamic Model · Fishman/Erez'},
  utah_units:{name:'Utah chill units',unit:'Utah units',description:'Warm hours can subtract accumulated chill'}
};
const fmt=(n,d=1)=>Number.isFinite(n)?n.toLocaleString('en-US',{maximumFractionDigits:d}):'—';
const signed=n=>Number.isFinite(n)?(n>=0?'+':'')+fmt(n):'—';
const esc=x=>String(x).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let data,selected,metrics,metric='chill_hours',horizon=14,current,site;
function values(){return{lat:Number($('lat').value),lon:Number($('lon').value),elevation:Number($('elev').value),
  base:Number($('base').value),radius:Number($('radius').value),elevationLimit:Number($('elev-limit').value)};}
function analyze(){
  $('error').hidden=true;
  try{
    site=values();selected=selectStations(data,site);metrics=buildMetrics(selected,site.base);
    $('location-label').textContent=site.lat.toFixed(3)+'°, '+site.lon.toFixed(3)+'° · '+fmt(site.elevation,0)+' ft';
    $('workspace').hidden=false;render();
  }catch(error){$('error').textContent=error.message;$('error').hidden=false;$('workspace').hidden=true;}
}
function render(){
  try{
    const asOf=$('asof').value;
    if(!asOf||asOf<'2023-01-01'||asOf>data.availableThrough)throw Error('Choose an analysis date between January 1, 2023 and December 31, 2025.');
    $('error').hidden=true;
    const def=definitions[metric],observed=accumulation(metrics,asOf,metric);
    const predicted=forecast(metrics.series[metric],asOf,horizon),evaluation=backtest(metrics.series[metric],asOf,horizon);
    current={asOf,observed,predicted,evaluation};
    $('total').textContent=fmt(observed.total,metric==='chill_hours'?0:1);
    $('total-unit').textContent=def.unit+' · observed sum';
    $('season-label').textContent=observed.start+' → '+observed.end;
    $('outlook-title').textContent='NEXT '+horizon+' DAYS · ESTIMATED GAIN';
    $('gain').textContent=predicted.status==='ok'?signed(predicted.mean):'Not enough data';
    $('gain').style.fontSize=predicted.status==='ok'?'':'20px';
    $('gain-unit').textContent=def.unit+' · historical baseline';
    $('spread').textContent=predicted.status==='ok'?'Historical spread '+fmt(predicted.p10)+'–'+fmt(predicted.p90)+' · '+predicted.sampleCount+' windows':'Need 2 complete matching historical windows';
    $('mae').textContent=evaluation.n?fmt(evaluation.mae):'—';
    $('trials').textContent=evaluation.n?evaluation.n+' held-out forecasts · '+def.unit:'No eligible historical holdouts';
    $('coverage').textContent=fmt(observed.coverage*100)+'%';
    $('coverage-note').textContent=observed.coverage===1?'Complete through analysis date':'Incomplete · totals use available observations';
    $('chart-title').textContent=def.name+' accumulation';
    $('chart-subtitle').textContent=def.description+(metric==='gdd'?' · base '+fmt(site.base)+'°F':'');
    $('forecast-note').textContent=predicted.status==='ok'?
      'Prior anchor years: '+predicted.years.join(', ')+'. Whisker: historical 10th–90th percentile at the forecast end.':
      'No forecast shown: insufficient complete historical windows for this date.';
    drawChart(observed,predicted,asOf);
    $('stations').innerHTML=selected.map(s=>'<tr><td><span class="station-icon">'+s.id+'</span><strong>'+esc(s.name)+'</strong><small>Station '+s.id+'</small></td><td>'+fmt(s.mi)+' mi</td><td>'+fmt(s.elevDiff,0)+' ft</td><td>'+fmt(s.weight*100)+'%</td><td>'+fmt(s.weather.hourlyCoverage*100)+'% / '+fmt(s.weather.dailyCoverage*100)+'%</td></tr>').join('');
    drawEvaluation(evaluation);
    $('quality').innerHTML=selected.map(s=>'<div class="quality-row"><strong>'+esc(s.name)+'</strong><span>Valid winter hours: '+fmt(s.weather.hourlyCoverage*100)+'% · paired daily temperatures: '+fmt(s.weather.dailyCoverage*100)+'%</span></div>').join('');
  }catch(error){$('error').textContent=error.message;$('error').hidden=false;}
}
function drawChart(observed,predicted,asOf){
  const W=900,H=280,L=45,R=28,T=20,B=37;
  const start=stamp(observed.start),end=stamp(asOf)+horizon*DAY;
  const totals=observed.points.filter(p=>p.value!==null).map(p=>p.value);
  let projection=[],sum=observed.total??0;
  if(predicted.status==='ok'&&observed.total!==null){
    projection=[{date:asOf,value:sum}];
    predicted.daily.forEach((v,i)=>{sum+=v;projection.push({date:iso(stamp(asOf)+(i+1)*DAY),value:sum});});
  }
  const values=[0,...totals,...projection.map(p=>p.value)];
  if(projection.length)values.push(observed.total+predicted.p10,observed.total+predicted.p90);
  const lower=Math.min(0,...values),upper=Math.max(1,...values),pad=(upper-lower)*.12;
  const min=lower<0?lower-pad:0,max=upper+pad;
  const x=date=>L+(stamp(date)-start)/(end-start)*(W-L-R);
  const y=v=>H-B-(v-min)/(max-min)*(H-T-B);
  const path=points=>{let open=false;return points.map(p=>{if(p.value===null){open=false;return '';}const d=(open?'L':'M')+x(p.date).toFixed(2)+','+y(p.value).toFixed(2);open=true;return d;}).join(' ');};
  let svg='<svg viewBox="0 0 '+W+' '+H+'" aria-hidden="true"><defs><linearGradient id="area" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#72965a" stop-opacity=".16"/><stop offset="100%" stop-color="#72965a" stop-opacity="0"/></linearGradient></defs>';
  const issueX=x(asOf);
  svg+='<rect x="'+issueX+'" y="'+T+'" width="'+(W-R-issueX)+'" height="'+(H-B-T)+'" fill="#f1f5e6"/>';
  for(let i=0;i<=4;i++){
    const value=min+(max-min)*i/4,yy=y(value);
    svg+='<line x1="'+L+'" x2="'+(W-R)+'" y1="'+yy+'" y2="'+yy+'" stroke="#e8edde" stroke-dasharray="3 5"/><text x="'+(L-9)+'" y="'+(yy+3)+'" text-anchor="end">'+fmt(value,0)+'</text>';
  }
  for(let i=0;i<=4;i++){const date=iso(start+(end-start)*i/4);svg+='<text x="'+x(date)+'" y="'+(H-10)+'" text-anchor="middle">'+new Date(stamp(date)).toLocaleDateString('en-US',{month:'short',day:'numeric',timeZone:'UTC'})+'</text>';}
  const observedPath=path(observed.points);
  svg+='<path d="'+observedPath+'" fill="none" stroke="#426f43" stroke-width="2.8" stroke-linejoin="round" stroke-linecap="round"/>';
  svg+='<line x1="'+issueX+'" x2="'+issueX+'" y1="'+T+'" y2="'+(H-B)+'" stroke="#becbab" stroke-dasharray="4 4"/><text x="'+(issueX-5)+'" y="'+(T+9)+'" text-anchor="end">Analysis date</text>';
  if(projection.length){
    const last=projection.at(-1),xx=x(last.date),lo=y(observed.total+predicted.p10),hi=y(observed.total+predicted.p90);
    svg+='<path d="'+path(projection)+'" fill="none" stroke="#8da35d" stroke-width="2.5" stroke-dasharray="6 5"/>';
    svg+='<line x1="'+xx+'" x2="'+xx+'" y1="'+lo+'" y2="'+hi+'" stroke="#a5b879" stroke-width="1.5"/><path d="M'+(xx-5)+','+lo+'H'+(xx+5)+'M'+(xx-5)+','+hi+'H'+(xx+5)+'" stroke="#a5b879"/><circle cx="'+xx+'" cy="'+y(last.value)+'" r="4" fill="#819b50" stroke="white" stroke-width="2"/>';
  }
  svg+='</svg>';$('chart').innerHTML=svg;
  $('chart').setAttribute('aria-label',definitions[metric].name+': observed total '+fmt(observed.total)+', coverage '+fmt(observed.coverage*100)+' percent. '+(predicted.status==='ok'?'Estimated '+horizon+' day gain '+fmt(predicted.mean)+'.':'Insufficient history for a forecast.'));
}
function drawEvaluation(e){
  if(!e.n){
    $('evaluation-summary').innerHTML='<div class="eval-message">There are no eligible held-out forecasts for this metric and analysis date. Earlier historical data and complete observation windows are required. No accuracy score is invented.</div>';
    $('backtests').innerHTML='<tr><td colspan="5">No eligible backtests.</td></tr>';return;
  }
  let message=e.n<10?'Only '+e.n+' eligible holdouts: treat these errors as preliminary. ':'';
  if(e.baseline!==null){
    message+='On matched test dates, this model’s MAE is '+fmt(e.pairedMae)+' versus '+fmt(e.baseline)+' for the recent-seven-day persistence baseline. ';
    message+=e.skill>0?'The historical baseline performs better on those dates.':'The recent-temperature baseline performs better on those dates.';
  }
  $('evaluation-summary').innerHTML='<div><strong>'+fmt(e.mae)+'</strong><span>MAE · average absolute error</span></div><div><strong>'+fmt(e.rmse)+'</strong><span>RMSE · larger errors carry more weight</span></div><div><strong>'+signed(e.bias)+'</strong><span>Bias · predicted minus observed</span></div><div class="eval-message">'+esc(message)+' Historical spread is not an accuracy guarantee.</div>';
  $('backtests').innerHTML=[...e.rows].reverse().map(r=>'<tr><td>'+r.date+'</td><td>'+fmt(r.actual)+'</td><td>'+fmt(r.predicted)+'</td><td>'+fmt(Math.abs(r.error))+'</td><td>'+r.years.join(', ')+'</td></tr>').join('');
}
$('orchard-form').addEventListener('submit',event=>{event.preventDefault();analyze();});
$('demo').addEventListener('click',()=>{for(const [id,v]of Object.entries({lat:38.55,lon:-121.8,elev:60,base:50,radius:75,'elev-limit':500}))$(id).value=v;analyze();});
$('asof').addEventListener('change',()=>{if(metrics)render();});
document.querySelectorAll('[data-metric]').forEach(button=>button.addEventListener('click',()=>{
  metric=button.dataset.metric;document.querySelectorAll('[data-metric]').forEach(b=>b.classList.toggle('active',b===button));if(metrics)render();
}));
document.querySelectorAll('[data-horizon]').forEach(button=>button.addEventListener('click',()=>{
  horizon=Number(button.dataset.horizon);document.querySelectorAll('[data-horizon]').forEach(b=>b.classList.toggle('active',b===button));if(metrics)render();
}));
document.querySelectorAll('[data-tab]').forEach(button=>button.addEventListener('click',()=>{
  document.querySelectorAll('[data-tab]').forEach(b=>{b.classList.toggle('active',b===button);b.setAttribute('aria-current',b===button?'page':'false');});
  document.querySelectorAll('[data-panel]').forEach(p=>p.hidden=p.dataset.panel!==button.dataset.tab);
}));
$('export').addEventListener('click',()=>{
  if(!current)return;
  const rows=[['type','date','metric','value','coverage','historical_window_count'],...current.observed.points.map(p=>['observed',p.date,metric,p.daily??'',p.coverage,''])];
  if(current.predicted.status==='ok')current.predicted.daily.forEach((v,i)=>rows.push(['forecast',iso(stamp(current.asOf)+(i+1)*DAY),metric,v,'',current.predicted.sampleCount]));
  const blob=new Blob([rows.map(r=>r.join(',')).join('\n')],{type:'text/csv;charset=utf-8'});
  const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='orchard-'+metric+'-'+current.asOf+'.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
});
try{
  const response=await fetch('./weather.json');if(!response.ok)throw Error('Weather data could not be loaded. Please reload.');
  data=await response.json();$('loading').hidden=true;analyze();
}catch(error){$('loading').hidden=true;$('error').textContent=error.message;$('error').hidden=false;}
