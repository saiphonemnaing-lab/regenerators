param(
 [string]$HourlyStart='2025-11-01', [string]$HourlyEnd='2026-01-10',
 [string]$DailyStart='2023-01-01', [string]$DailyEnd='2025-12-31',
 [string]$Stations='6,139,226'
)
$ErrorActionPreference='Stop'
if(-not $env:CIMIS_APP_KEY){throw 'Set CIMIS_APP_KEY before running.'}
$root=Join-Path $PSScriptRoot 'data'
$endpoint='https://et.water.ca.gov/StationWeb/GetDataByStationNumber'
$manifest=@()
foreach($kind in @('hourly','daily')) {
 $folder=Join-Path $root $kind
 New-Item -ItemType Directory -Force -Path $folder | Out-Null
 $start=if($kind -eq 'hourly'){$HourlyStart}else{$DailyStart}
 $end=if($kind -eq 'hourly'){$HourlyEnd}else{$DailyEnd}
 $cursor=[datetime]::ParseExact($start,'yyyy-MM-dd',$null)
 $last=[datetime]::ParseExact($end,'yyyy-MM-dd',$null)
 if($cursor -gt $last){throw 'Start date must precede end date.'}
 $stationCount=$Stations.Split(',').Count
 # Use small batches to avoid volume errors and slow server responses.
 $batchDays=if($kind -eq 'hourly'){[math]::Floor(1700/(24*$stationCount))}else{31}
 if($batchDays -lt 1){throw 'Too many stations for a single request.'}
 $items=if($kind -eq 'hourly'){'hly-air-tmp'}else{'day-air-tmp-max,day-air-tmp-min,day-air-tmp-avg'}
 $isHourly=($kind -eq 'hourly').ToString().ToLowerInvariant()
 while($cursor -le $last){
  $stop=$cursor.AddDays($batchDays-1);if($stop -gt $last){$stop=$last}
  $a=$cursor.ToString('yyyy-MM-dd');$b=$stop.ToString('yyyy-MM-dd')
  $name="$($Stations.Replace(',','-'))_${a}_to_${b}.json"
  $uri=$endpoint+"?stationNbrs=$Stations&startDate=$a&endDate=$b&isHourly=$isHourly&unitOfMeasure=E&dataItems=$items"
  $cached=Join-Path $folder $name
  if(Test-Path -LiteralPath $cached){$response=Get-Content -Raw -LiteralPath $cached|ConvertFrom-Json}else{
  for($attempt=1;$attempt -le 3;$attempt++){
   try{
    $response=Invoke-RestMethod -Uri $uri -Headers @{'Ocp-Apim-Subscription-Key'=$env:CIMIS_APP_KEY;Accept='application/json'} -TimeoutSec 45
    if($response -is [string] -or -not $response.Data.Providers){throw 'Non-data response'}
    break
   }catch{if($attempt -eq 3){throw "CIMIS failed: $kind $a to $b. Credential details suppressed."};Start-Sleep -Seconds 2}
  }
  }
  $records=@($response.Data.Providers|ForEach-Object{$_.Records})
  if($records.Count -eq 0){throw "Empty response: $kind $a to $b"}
  if(@($records|Where-Object{$_.Scope -ne $kind}).Count){throw 'Unexpected record scope'}
  $response|ConvertTo-Json -Depth 20 -Compress|Set-Content -Encoding utf8NoBOM -LiteralPath (Join-Path $folder $name)
  $manifest+=@{scope=$kind;start=$a;end=$b;records=$records.Count;file="$kind/$name"}
  Write-Output "$kind $a to $b : $($records.Count) records"
  $cursor=$stop.AddDays(1)
 }
}
@{source=$endpoint;units='English (Fahrenheit)';stations=$Stations;retrieved_utc=[datetime]::UtcNow.ToString('o');batches=$manifest}|ConvertTo-Json -Depth 10|Set-Content -Encoding utf8NoBOM -LiteralPath (Join-Path $root 'download_manifest.json')


