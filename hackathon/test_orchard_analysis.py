import json
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from orchard_analysis import (validate_site, select_stations, growing_degree_days,
                              blend_temperatures, analyze_series, load_temperatures, run_analysis)


class AnalysisTests(unittest.TestCase):
    def fixtures(self):
        meta = pd.DataFrame(dict(station_id=[1,2,3,4],name=['A','B','C','D'],
                                 lat=[38,38.01,38.02,38.03],lon=[-121]*4,
                                 elev_ft=[100,1000,110,120],active=[True]*4)).set_index('station_id')
        index = pd.date_range('2025-11-01 01:00',periods=48,freq='h')
        hourly = pd.DataFrame(40.,index=index,columns=meta.index)
        idx = pd.date_range('2025-01-01',periods=3)
        daily = {'DayAirTmpMin':pd.DataFrame(40.,index=idx,columns=meta.index),
                 'DayAirTmpMax':pd.DataFrame(70.,index=idx,columns=meta.index)}
        return meta,hourly,daily

    def test_invalid_inputs(self):
        for args in [(91,0,0),(0,-181,0),(float('nan'),0,0),(0,0,float('inf'))]:
            with self.assertRaises(ValueError): validate_site(*args)

    def test_elevation_excludes_nearer_station(self):
        meta,h,d=self.fixtures()
        selected=select_stations(validate_site(38,-121,100),meta,h,d)
        self.assertEqual(list(selected.index),[1,3,4])
        self.assertAlmostEqual(selected.weight.sum(),1)

    def test_coverage_required_for_both_sources(self):
        meta,h,d=self.fixtures();d['DayAirTmpMin'][3]=np.nan
        with self.assertRaisesRegex(ValueError,'Only 2 suitable'):
            select_stations(validate_site(38,-121,100),meta,h,d)

    def test_no_silent_distant_fallback(self):
        meta,h,d=self.fixtures()
        with self.assertRaisesRegex(ValueError,'need 3'):
            select_stations(validate_site(0,0,100),meta,h,d)

    def test_score_uses_elevation_not_just_distance(self):
        meta,h,d=self.fixtures();meta.loc[2,'elev_ft']=490
        selected=select_stations(validate_site(38,-121,100),meta,h,d)
        self.assertNotIn(2,selected.index)

    def test_blend_renormalizes_and_preserves_missing(self):
        selected=pd.DataFrame({'weight':[.75,.25]},index=[1,2])
        table=pd.DataFrame({1:[40,np.nan,np.nan],2:[60,60,np.nan]})
        result=blend_temperatures(table,selected)
        self.assertEqual(result.iloc[0],45)
        self.assertEqual(result.iloc[1],60)
        self.assertTrue(pd.isna(result.iloc[2]))

    def test_gdd_formula_and_missing(self):
        result=growing_degree_days(pd.Series([40.,20.,np.nan,80.]),pd.Series([80.,40.,90.,60.]),50)
        self.assertEqual(list(result.iloc[:2]),[10.,0.])
        self.assertTrue(result.iloc[2:].isna().all())

    def test_midnight_belongs_to_previous_chill_day(self):
        hourly=pd.Series([40.],index=pd.DatetimeIndex(['2025-11-02 00:00']))
        daily=pd.Series([40.],index=pd.DatetimeIndex(['2025-01-01']))
        curves,summary=analyze_series(hourly,daily,daily+30,50,'test')
        chill=curves.query("scope == 'chill'").set_index('date')
        self.assertEqual(chill.loc['2025-11-01','chill_hours'],1)
        self.assertTrue(pd.isna(chill.loc['2025-11-02','chill_hours']))
        self.assertFalse(summary.query("scope == 'chill'").iloc[0].eligible_for_comparison)

    def test_leap_year_and_cumulative_reset(self):
        idx=pd.date_range('2023-01-01','2024-12-31')
        low=pd.Series(50.,index=idx);high=pd.Series(70.,index=idx)
        hourly=pd.Series([40.],index=pd.DatetimeIndex(['2025-11-01 01:00']))
        curves,summary=analyze_series(hourly,low,high,50,'test')
        gdd=summary.query("scope == 'gdd'").set_index('season')
        self.assertEqual(gdd.loc['2023','gdd'],3650)
        self.assertEqual(gdd.loc['2024','gdd'],3660)
        self.assertEqual(curves.query("scope == 'gdd' and season == '2024'").iloc[0].cumulative_gdd,10)

    def test_loader_filters_qc_and_rejects_celsius(self):
        from unittest.mock import patch, Mock
        record=dict(Date='2025-01-01',Station='6',Scope='daily',Standard='english',
                    DayAirTmpMin={'Value':'40','Qc':'M'},DayAirTmpMax={'Value':'70','Qc':' '})
        path=Mock(); path.name='test.json'
        path.read_text.return_value=json.dumps({'Data':{'Providers':[{'Records':[record]}]}})
        with patch.object(Path,'glob',return_value=[path]):
            loaded=load_temperatures('unused','daily')
            self.assertTrue(pd.isna(loaded['DayAirTmpMin'].iloc[0,0]))
            record['Standard']='metric'
            path.read_text.return_value=json.dumps({'Data':{'Providers':[{'Records':[record]}]}})
            with self.assertRaisesRegex(ValueError,'Fahrenheit'):load_temperatures('unused','daily')

    def test_real_data_integration(self):
        results=run_analysis(validate_site(38.55,-121.8,60))
        self.assertEqual(set(results['selected_stations'].station_id),{6,139,226})
        summary=results['season_summary'].query("entity == 'orchard'")
        self.assertEqual(set(summary.query("scope == 'gdd'").season),{'2023','2024','2025'})
        chill=summary.query("scope == 'chill' and season == '2025-2026'").iloc[0]
        self.assertLess(chill.coverage,.8)
        self.assertFalse(chill.eligible_for_comparison)


if __name__ == '__main__':
    unittest.main()

