import unittest
import numpy as np
import pandas as pd
from prediction import forecast,backtest,evaluate

class PredictionTests(unittest.TestCase):
    def series(self):
        index=pd.date_range('2023-01-01','2025-12-31')
        return pd.Series([10. if t.year==2023 else 20. if t.year==2024 else 30. for t in index],index=index)

    def test_prior_year_mean_and_spread(self):
        f=forecast(self.series(),'2025-06-01',14)
        self.assertEqual(f['historical_years'],[2023,2024])
        self.assertEqual(f['mean'],210)
        self.assertEqual(f['p10'],154)
        self.assertEqual(f['p90'],266)

    def test_future_target_cannot_change_forecast(self):
        source=self.series()
        baseline=forecast(source,'2025-06-01',14)
        source.loc['2025-06-02':]=99999.
        self.assertEqual(baseline,forecast(source,'2025-06-01',14))

    def test_incomplete_windows_are_excluded(self):
        source=self.series();source.loc['2024-06-03']=np.nan
        f=forecast(source,'2025-06-01',14)
        self.assertEqual(f['status'],'insufficient_history')
        self.assertEqual(f['sample_count'],1)
        self.assertIsNone(f['mean'])

    def test_no_fabricated_evaluation(self):
        empty=evaluate(pd.DataFrame())
        self.assertEqual(empty['n'],0)
        self.assertIsNone(empty['mae'])

    def test_error_metrics(self):
        rows=pd.DataFrame(dict(error=[2.,-4.],predicted=[12.,6.],actual=[10.,10.],persistence=[11.,8.]))
        scores=evaluate(rows)
        self.assertEqual(scores['mae'],3.)
        self.assertAlmostEqual(scores['rmse'],np.sqrt(10))
        self.assertEqual(scores['bias'],-1.)
        self.assertEqual(scores['persistence_mae'],1.5)
        self.assertEqual(scores['skill_vs_persistence'],-1.)

    def test_backtest_training_chronology(self):
        rows=backtest(self.series())
        self.assertGreater(len(rows),0)
        for _,r in rows.iterrows():
            self.assertTrue(all(int(y)<int(r.as_of[:4]) for y in r.historical_years.split(',')))

    def test_leap_day_does_not_alias_march_first(self):
        index=pd.date_range('2021-01-01','2024-12-31')
        result=forecast(pd.Series(1.,index=index),'2024-02-29',7)
        self.assertEqual(result['status'],'insufficient_history')

if __name__=='__main__':
    unittest.main()
