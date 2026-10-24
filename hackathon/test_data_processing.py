import io
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import pandas as pd
from data_processing import (station_input, date_window, validate_response,
                             download_weather, process_weather)

class ProcessingTests(unittest.TestCase):
    def payload(self, scope='hourly', day='2025-11-01', station=6):
        r=dict(Station=str(station),Date=day,Scope=scope,Standard='english')
        if scope=='hourly':
            r.update(Hour='0100',HlyAirTmp={'Value':'40','Qc':' ','Unit':'(F)'})
        else:
            r.update(DayAirTmpMin={'Value':'40','Qc':' ','Unit':'(F)'},
                     DayAirTmpMax={'Value':'70','Qc':' ','Unit':'(F)'})
        return {'Data':{'Providers':[{'Records':[r]}]}}

    def temp_dir(self):
        # Place fixtures within this project. Verify cleanup target before use.
        temporary=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.assertTrue(Path(temporary.name).resolve().is_relative_to(Path(__file__).resolve().parent))
        return temporary

    def test_station_contract(self):
        self.assertEqual(list(station_input([6,139,226]).index),[6,139,226])
        for ids in [[6,6,139],[6,139],[6,139,0],[6,139,2.5]]:
            with self.assertRaises(ValueError):station_input(ids)

    def test_dates(self):
        for a,b in [('2025-01-01',None),('2025-02-01','2025-01-01')]:
            with self.assertRaises(ValueError):date_window(a,b)
        self.assertIsNone(date_window(None,None))

    def test_response_validation(self):
        day=date(2025,11,1)
        with self.assertRaises(ValueError):validate_response({},[6],'hourly',day,day)
        with self.assertRaises(ValueError):validate_response(self.payload(station=71),[6],'hourly',day,day)
        payload=self.payload();payload['Data']['Providers'][0]['Records']*=2
        with self.assertRaises(ValueError):validate_response(payload,[6],'hourly',day,day)

    def test_download_header_cache_and_schema(self):
        calls=[]
        def opener(request,timeout):
            self.assertNotIn('private-test-key',request.full_url)
            self.assertEqual(request.get_header('Ocp-apim-subscription-key'),'private-test-key')
            params=parse_qs(urlparse(request.full_url).query)
            scope='hourly' if params['isHourly']==['true'] else 'daily'
            calls.append(scope)
            return io.BytesIO(json.dumps(self.payload(scope)).encode())
        window=(date(2025,11,1),date(2025,11,1))
        with self.temp_dir() as directory:
            batches=download_weather([6,139,226],directory,window,window,
                                     api_key='private-test-key',opener=opener)
            self.assertEqual(calls,['hourly','daily'])
            self.assertFalse(any(b['cached'] for b in batches))
            def no_network(*args,**kwargs):raise AssertionError('Cache should prevent network use')
            cached=download_weather([6,139,226],directory,window,window,opener=no_network)
            self.assertTrue(all(b['cached'] for b in cached))
            for p in Path(directory).rglob('*.json'):
                self.assertNotIn('private-test-key',p.read_text())

    def test_failed_api_retries_without_saving_rejection_page(self):
        calls=[]
        def opener(*args,**kwargs):
            calls.append(1)
            return io.BytesIO(b'<html>Request Rejected</html>')
        window=(date(2025,11,1),date(2025,11,1))
        with self.temp_dir() as directory:
            with self.assertRaisesRegex(ValueError,'Credential details suppressed'):
                download_weather([6,139,226],directory,window,window,api_key='secret',
                                 opener=opener,pause=lambda _:None)
            self.assertEqual(len(calls),3)
            self.assertEqual(list(Path(directory).rglob('*.json')),[])

    def test_explicit_date_range_preserves_absent_days(self):
        selected=station_input([6,139,226])
        window=(date(2025,11,1),date(2025,11,2))
        with self.temp_dir() as directory:
            for scope in ['hourly','daily']:
                folder=Path(directory)/scope;folder.mkdir()
                records=[self.payload(scope,station=s)['Data']['Providers'][0]['Records'][0]
                         for s in selected.index]
                (folder/'test.json').write_text(json.dumps({'Data':{'Providers':[{'Records':records}]}}))
            tables,_=process_weather(selected,directory,hourly_window=window,daily_window=window)
            self.assertEqual(len(tables['clean_hourly']),3*48)
            self.assertEqual(tables['clean_hourly'].valid.sum(),3)
            self.assertEqual(len(tables['clean_daily']),6)
            self.assertEqual(tables['clean_daily'].valid.sum(),3)
            self.assertEqual(tables['clean_daily'].gdd.dropna().tolist(),[5.,5.,5.])

    def test_missing_stations_error(self):
        with patch('data_processing.load_temperatures') as loader:
            idx=pd.date_range('2025-11-01',periods=2)
            table=pd.DataFrame({6:[40.,40.]},index=idx)
            loader.side_effect=[{'HlyAirTmp':table},
                                {'DayAirTmpMin':table,'DayAirTmpMax':table+30}]
            with self.assertRaisesRegex(ValueError,'Missing hourly/daily sources'):
                process_weather(station_input([6,139,226]))

    def test_demo_integration(self):
        tables,provenance=process_weather(station_input([6,139,226]))
        self.assertGreaterEqual(len(tables['clean_hourly']),5112)
        self.assertEqual(len(tables['clean_daily']),3288)
        self.assertEqual(set(tables['season_summary'].entity),{'6','139','226'})
        self.assertEqual(len(tables['quality_report']),6)
        self.assertTrue(all(len(p['sha256'])==64 for p in provenance))
        self.assertTrue(tables['clean_hourly'].temp_f.isna().any())
        self.assertTrue(tables['clean_daily'].gdd.isna().any())

if __name__=='__main__':
    unittest.main()
