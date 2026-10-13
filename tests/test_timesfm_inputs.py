from pathlib import Path

import numpy as np
import pandas as pd

from src.methods.timesfm.run import build_origin_inputs
from src.methods.xgboost.known_calendar import read_known_calendar


def test_future_observations_do_not_change_origin_inputs() -> None:
    dates=pd.date_range('2022-01-01',periods=70)
    origin=dates[40]
    target=pd.DataFrame({'one':np.arange(70,dtype=float),'two':np.arange(70,dtype=float)*2},index=dates)
    patients=pd.DataFrame({'total':np.arange(70,dtype=float)},index=dates)
    calendar=read_known_calendar(Path('data/processed/external/korean_calendar/calendar_20261007_01'))
    before=build_origin_inputs(target,patients,['two','one'],origin,28,calendar)
    target.loc[origin:]=99999;patients.loc[origin:]=-99999
    after=build_origin_inputs(target,patients,['two','one'],origin,28,calendar)
    for a,b in zip(before,after): np.testing.assert_array_equal(a,b)
    assert before[0][0,0,-1]==78
    assert before[0][0,1,-1]==39


def test_missing_target_dates_remain_nan() -> None:
    dates=pd.date_range('2022-01-01',periods=50)
    target=pd.DataFrame({'one':np.arange(50,dtype=float)},index=dates)
    target.loc[dates[25],'one']=np.nan
    patients=pd.DataFrame({'total':np.arange(50,dtype=float)},index=dates)
    calendar=read_known_calendar(Path('data/processed/external/korean_calendar/calendar_20261007_01'))
    y,p,z=build_origin_inputs(target,patients,['one'],dates[40],28,calendar)
    assert np.isnan(y[0,0,13])
    assert y.shape==(1,1,28) and z.shape[-1]==42


def test_joint_and_independent_metrics_are_not_collapsed() -> None:
    from src.methods.timesfm.run import evaluate_predictions
    dates=pd.date_range('2022-01-01',periods=21)
    targets=pd.DataFrame({'one':np.arange(21,dtype=float)},index=dates)
    cohorts=pd.DataFrame([{'channel':'one','name':'synthetic product','cohort':'drug'}])
    rows=[]
    for mode,prediction in [('joint',12.),('independent',15.)]:
        rows.append({'channel':'one','mode':mode,'context':28,'observed':True,'origin':pd.Timestamp('2024-01-01'),'prediction':prediction,'actual':10.,'q1':5.,'q9':20.})
    metrics=evaluate_predictions(pd.DataFrame(rows),targets,cohorts)
    assert len(metrics)==2
    assert metrics.set_index('mode').loc['joint','mae']==2.
    assert metrics.set_index('mode').loc['independent','mae']==5.
