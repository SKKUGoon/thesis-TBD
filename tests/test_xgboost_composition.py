import numpy as np
import pandas as pd

from src.methods.xgboost.forecasting import build_lagged_origin_features, build_direct_horizon_matrix, run_monthly_compositional_forecasts
from src.methods.xgboost.patient_data import prepare_record_patient_categories, count_distinct_record_patients


def test_diagnosis_patient_counts_preserve_unique_patient_and_missing_category():
    records = pd.DataFrame({'병록번호':['synthetic_A']*3 + ['synthetic_B'], '의약품사용일시':['2020-01-01 12:00:00']*4, '주상병코드':['I10','I10','E11',''], '부상병코드':['']*4, '성별코드':['M']*4, '나이':['70']*4, '외래입원여부':['외래']*4})
    prepared = prepare_record_patient_categories(records)
    counts = count_distinct_record_patients(prepared, 'main_dx').set_index('category')['count']
    assert counts['I10']==1 and counts['E11']==1 and counts['MISSING']==1
    assert len(prepared[['date','patient']].drop_duplicates())==2
    assert len(records)==4


def test_forecast_origin_excludes_same_day_and_future_patient_diagnoses():
    index=pd.date_range('2020-01-01',periods=100)
    items=pd.DataFrame({'A':np.arange(100)},index=index)
    patients=pd.DataFrame({'main_dx__I10':np.arange(100)},index=index)
    first=build_lagged_origin_features(items,patients,list(patients),7,28)
    cutoff=index[70]
    items.loc[cutoff:]=99999
    patients.loc[cutoff:]=99999
    second=build_lagged_origin_features(items,patients,list(patients),7,28)
    pd.testing.assert_frame_equal(first.loc[:cutoff],second.loc[:cutoff])
    y=pd.Series(3,index=index)
    matrix,labels,origins,dates=build_direct_horizon_matrix(first,y,14)
    assert ((dates-origins).days==matrix.horizon.to_numpy()-1).all()
    assert matrix[origins==cutoff]['patient_main_dx__I10_lag_1'].eq(69).all()


def test_xgboost_future_labels_do_not_change_past_predictions():
    index=pd.date_range('2020-01-01',periods=430)
    group=pd.Series(10+np.sin(np.arange(430)),index=index)
    items=pd.DataFrame({'A':group},index=index)
    patients=pd.DataFrame(index=index)
    base=build_lagged_origin_features(items,patients,[],7,7)
    config={'xgboost_parameters':{'objective':'reg:squarederror','tree_method':'hist','max_depth':2,'nthread':1},'seed':1,'boost_rounds':5}
    origin=pd.Timestamp('2021-02-01')
    first=run_monthly_compositional_forecasts(group,base,pd.DatetimeIndex([origin]),'xgboost',config,14)
    changed=group.copy()
    changed.loc[origin:]+=10000
    second=run_monthly_compositional_forecasts(changed,base,pd.DatetimeIndex([origin]),'xgboost',config,14)
    np.testing.assert_array_equal(first.predictions.prediction_raw,second.predictions.prediction_raw)
    assert first.predictions.parameter_training_end.lt(origin).all()
