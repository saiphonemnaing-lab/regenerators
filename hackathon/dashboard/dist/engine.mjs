export const DAY=86400000;
export const iso=t=>new Date(t).toISOString().slice(0,10);
export const stamp=s=>Date.parse(s.length===10?s+'T00:00:00Z':s+'Z');
const finite=v=>typeof v==='number'&&Number.isFinite(v);
export function distance(a,b){
  const rad=x=>x*Math.PI/180,lat1=rad(a.lat),lat2=rad(b.lat),dlat=lat2-lat1,dlon=rad(b.lon-a.lon);
  return 3958.8*2*Math.asin(Math.sqrt(Math.sin(dlat/2)**2+Math.cos(lat1)*Math.cos(lat2)*Math.sin(dlon/2)**2));
}
export function selectStations(data,site){
  if(![site.lat,site.lon,site.elevation,site.radius,site.elevationLimit,site.base].every(Number.isFinite)||Math.abs(site.lat)>90||Math.abs(site.lon)>180||site.radius<=0||site.elevationLimit<=0)
    throw Error('Enter valid coordinates, elevation, base temperature, and positive station limits.');
  const candidates=data.stations.map(s=>{
    const w=data.weather[s.id],mi=distance(site,s),elevDiff=Math.abs(site.elevation-s.elevation);
    return {...s,mi,elevDiff,score:mi/site.radius+elevDiff/site.elevationLimit,weather:w};
  }).filter(s=>s.mi<=site.radius&&s.elevDiff<=site.elevationLimit&&s.weather&&s.weather.hourlyCoverage>=.8&&s.weather.dailyCoverage>=.8)
    .sort((a,b)=>a.score-b.score||a.mi-b.mi||a.id-b.id);
  if(candidates.length<3)throw Error('Fewer than three suitable stations have downloaded weather in this area. This demo contains Davis, Winters, and Woodland. Try the Davis example, or add station data with the Python downloader.');
  const picked=candidates.slice(0,3),total=picked.reduce((s,x)=>s+1/Math.max(.5,x.mi)**2,0);
  return picked.map(s=>({...s,weight:(1/Math.max(.5,s.mi)**2)/total}));
}
export function buildMetrics(selected,base=50){
  const hourly=new Map(),daily=new Map();
  for(const station of selected){
    for(const [time,v]of station.weather.hourly){
      if(!hourly.has(time))hourly.set(time,[]);
      if(finite(v))hourly.get(time).push([v,station.weight]);
    }
    for(const [day,lo,hi]of station.weather.daily){
      if(!daily.has(day))daily.set(day,[]);
      if(finite(lo)&&finite(hi))daily.get(day).push([lo,hi,station.weight]);
    }
  }
  const series=Object.fromEntries(['gdd','chill_hours','utah_units','chill_portions'].map(k=>[k,new Map()]));
  const coverage=new Map(),chillValues=new Map(),gddTemps=new Map(),groups=new Map();
  for(const [time,parts]of [...hourly].sort()){
    const end=stamp(time),day=iso(end-3600000),month=Number(day.slice(5,7));
    if(![11,12,1,2].includes(month)||day.slice(5)==='02-29')continue;
    const year=Number(day.slice(0,4))-(month<11?1:0);
    if(!groups.has(year))groups.set(year,[]);
    const den=parts.reduce((s,p)=>s+p[1],0),temp=den?parts.reduce((s,p)=>s+p[0]*p[1],0)/den:null;
    groups.get(year).push({day,temp});
  }
  for(const [year,hours]of groups){
    let inter=0,prevXi=0; const days=new Map();
    for(let t=stamp(year+'-11-01');t<=stamp((year+1)+'-02-28');t+=DAY)
      days.set(iso(t),{valid:0,chill_hours:0,utah_units:0,chill_portions:0});
    for(const {day,temp}of hours){
      if(!finite(temp)||!days.has(day))continue;
      const row=days.get(day);row.valid++;
      row.chill_hours+=temp>=32&&temp<=45?1:0;
      const tc=(temp-32)*5/9;
      row.utah_units+=tc<=1.4?0:tc<=2.4?.5:tc<=9.1?1:tc<=12.4?.5:tc<=15.9?0:tc<=18?-.5:-1;
      const tk=tc+273,sr=Math.exp(1.6*277*(tk-277)/tk),xi=sr/(1+sr);
      const xs=(139500/2.567e18)*Math.exp((12888.8-4153.5)/tk),ak=2.567e18*Math.exp(-12888.8/tk);
      const begin=inter<1?inter:inter*(1-prevXi);
      inter=xs-(xs-begin)*Math.exp(-ak);
      row.chill_portions+=inter>=1?inter*xi:0;prevXi=xi;
    }
    for(const [day,row]of days){
      coverage.set(day,row.valid/24);chillValues.set(day,row);
      for(const metric of ['chill_hours','utah_units','chill_portions'])
        series[metric].set(day,row.valid===24?row[metric]:null);
    }
  }
  for(const [day,parts]of [...daily].sort()){
    const den=parts.reduce((s,p)=>s+p[2],0);
    const lo=den?parts.reduce((s,p)=>s+p[0]*p[2],0)/den:null;
    const hi=den?parts.reduce((s,p)=>s+p[1]*p[2],0)/den:null;
    series.gdd.set(day,den?Math.max(0,(lo+hi)/2-base):null);gddTemps.set(day,{lo,hi});
  }
  return {series,coverage,chillValues,gddTemps};
}
export const quantile=(arr,q)=>{const s=[...arr].sort((a,b)=>a-b),p=(s.length-1)*q,i=Math.floor(p);return s[i]+(s[Math.min(i+1,s.length-1)]-s[i])*(p-i);};
const mean=arr=>arr.reduce((a,b)=>a+b,0)/arr.length;
function windowValues(series,origin,horizon){
  return Array.from({length:horizon},(_,i)=>series.get(iso(origin+(i+1)*DAY)));
}
export function forecast(series,asOf,horizon=14){
  const origin=stamp(asOf),first=[...series.keys()].sort()[0],samples=[];
  if(first)for(let year=Number(first.slice(0,4))-1;year<Number(asOf.slice(0,4));year++){
    const anchor=year+asOf.slice(4),at=stamp(anchor);
    if(iso(at)!==anchor||at+horizon*DAY>origin)continue;
    const values=windowValues(series,at,horizon);
    if(values.every(finite))samples.push({year,total:values.reduce((a,b)=>a+b,0),values});
  }
  if(samples.length<2)return{status:'insufficient_history',sampleCount:samples.length,years:samples.map(s=>s.year)};
  const totals=samples.map(s=>s.total);
  return {status:'ok',sampleCount:samples.length,years:samples.map(s=>s.year),
    mean:mean(totals),p10:quantile(totals,.1),p90:quantile(totals,.9),
    daily:Array.from({length:horizon},(_,i)=>mean(samples.map(s=>s.values[i])))};
}
export function backtest(series,asOf,horizon=14){
  const keys=[...series.keys()].sort(),rows=[];
  if(!keys.length)return {n:0,rows};
  const first=stamp(keys[0]),stop=Math.min(stamp(keys.at(-1)),stamp(asOf))-horizon*DAY;
  for(let t=first;t<=stop;t+=14*DAY){
    const values=windowValues(series,t,horizon);
    if(!values.every(finite))continue;
    const pred=forecast(series,iso(t),horizon);if(pred.status!=='ok')continue;
    const recent=Array.from({length:7},(_,i)=>series.get(iso(t-(6-i)*DAY)));
    const actual=values.reduce((a,b)=>a+b,0),persistence=recent.every(finite)?mean(recent)*horizon:null;
    rows.push({date:iso(t),actual,predicted:pred.mean,error:pred.mean-actual,persistence,years:pred.years});
  }
  if(!rows.length)return{n:0,rows};
  const paired=rows.filter(r=>finite(r.persistence));
  const baseline=paired.length?mean(paired.map(r=>Math.abs(r.persistence-r.actual))):null;
  const pairedMae=paired.length?mean(paired.map(r=>Math.abs(r.error))):null;
  return {n:rows.length,mae:mean(rows.map(r=>Math.abs(r.error))),
    rmse:Math.sqrt(mean(rows.map(r=>r.error**2))),bias:mean(rows.map(r=>r.error)),
    baseline,pairedMae,skill:baseline?1-pairedMae/baseline:null,rows};
}
export function seasonStart(asOf,metric){
  const year=Number(asOf.slice(0,4)),month=Number(asOf.slice(5,7));
  return metric==='gdd'?year+'-01-01':(year-(month<11?1:0))+'-11-01';
}
export function accumulation(metrics,asOf,metric){
  const start=seasonStart(asOf,metric),end=metric==='gdd'?asOf:[asOf,(Number(start.slice(0,4))+1)+'-02-28'].sort()[0];
  const points=[];let total=0,valid=0,expected=0;
  for(let t=stamp(start);t<=stamp(end);t+=DAY){
    const day=iso(t);if(metric!=='gdd'&&day.slice(5)==='02-29')continue;
    const row=metrics.chillValues.get(day),v=metric==='gdd'?metrics.series.gdd.get(day):row&&row.valid?row[metric]:null;
    const cov=metric==='gdd'?(finite(v)?1:0):(metrics.coverage.get(day)||0);
    expected++;valid+=cov;if(finite(v))total+=v;
    points.push({date:day,value:finite(v)?total:null,daily:v,coverage:cov});
  }
  return {total:valid?total:null,coverage:expected?valid/expected:0,points,start,end};
}
