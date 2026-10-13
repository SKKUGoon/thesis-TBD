from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

from src.methods.xgboost.known_calendar import calculate_known_calendar_features, read_known_calendar
from src.methods.xgboost.patient_data import map_kcd_chapter, prepare_record_patient_categories


REFERENCE=Path('data/processed/external/korean_calendar/calendar_20261007_01')


def test_true_kcd_boundaries_not_only_first_letter():
    mapped=map_kcd_chapter(pd.Series(['A01','B20','D10','D63','H40','H90','S10','T20','U07','MISSING','INVALID']))
    assert mapped.tolist()==['I','I','II','III','VII','VIII','XIX','XIX','XXII','MISSING','INVALID']


def test_ten_year_age_groups():
    age=['0','9','10','19','20','89','90','99','100','120','']
    frame=pd.DataFrame({'병록번호':['synthetic']*len(age),'의약품사용일시':['2020-01-01']*len(age),'주상병코드':['I10']*len(age),'부상병코드':['']*len(age),'성별코드':['M']*len(age),'외래입원여부':['외래']*len(age),'나이':age})
    result=prepare_record_patient_categories(frame,'standard')
    assert result.age.tolist()==['0_9','0_9','10_19','10_19','20_29','80_89','90_99','90_99','100_109','120_129','MISSING_INVALID']


def test_calendar_and_distancing_known_at_forecast_origin():
    calendar=read_known_calendar(REFERENCE)
    origins=pd.DatetimeIndex(['2022-04-11','2022-04-18','2023-09-01','2023-09-06'])
    targets=pd.DatetimeIndex(['2022-04-18','2022-04-18','2023-10-02','2023-10-02'])
    result=calculate_known_calendar_features(origins,targets,calendar)
    assert result['is_national_epidemic_distancing_response'][:2].tolist()==[1,0]
    assert result['target_named_holiday'][2:].tolist()==[0,1]
    assert targets.dayofweek.tolist()==[0,0,0,0]


def test_future_announcement_change_cannot_change_past_features():
    calendar=read_known_calendar(REFERENCE)
    changed=replace(calendar,policy_values=np.array([1,1]))
    origins=pd.DatetimeIndex(['2022-04-11'])
    targets=pd.DatetimeIndex(['2022-04-24'])
    before=calculate_known_calendar_features(origins,targets,calendar)
    after=calculate_known_calendar_features(origins,targets,changed)
    for key in before:
        np.testing.assert_array_equal(before[key],after[key])
