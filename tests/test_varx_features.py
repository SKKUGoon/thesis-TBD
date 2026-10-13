import numpy as np
import pandas as pd
import pytest

from src.methods.varx.feature_screening import build_origin_available_features, fit_full_rank_linear_model


def test_patient_and_drug_features_exclude_origin_and_future_observations():
    index = pd.date_range('2020-01-01', periods=100)
    group = pd.Series(np.arange(100.0), index=index)
    patients = pd.DataFrame({'male_patients': np.arange(100), 'female_patients': np.arange(100) + 10, 'total_patients': np.arange(100) * 2 + 10}, index=index)
    original, blocks = build_origin_available_features(group, patients, 14)
    cutoff = index[60]
    group.loc[cutoff:] = 9999
    patients.loc[cutoff:] = 9999
    changed, _ = build_origin_available_features(group, patients, 14)
    pd.testing.assert_frame_equal(original.loc[:cutoff], changed.loc[:cutoff])
    assert len(blocks['drug_ar']) == 7
    assert len(blocks['total_patients']) == 28
    assert original.loc[cutoff, 'total_patients_lag_1'] == 59 * 2 + 10
    assert original.loc[cutoff, 'total_patients_lag_28'] == 32 * 2 + 10


def test_future_calendar_is_known_and_horizon_specific():
    index = pd.date_range('2020-01-01', periods=100)
    group = pd.Series(1.0, index=index)
    patients = pd.DataFrame({name: 1.0 for name in ['male_patients', 'female_patients', 'total_patients']}, index=index)
    one, _ = build_origin_available_features(group, patients, 1)
    fourteen, _ = build_origin_available_features(group, patients, 14)
    assert one.loc['2020-03-02', 'target_weekday_6'] == 0
    assert fourteen.loc['2020-03-02', 'target_weekday_6'] == 1


def test_patient_total_and_exact_sex_partition_cannot_be_included_together():
    features = pd.DataFrame({'male': np.arange(80), 'female': np.arange(80) ** 2})
    features['total'] = features['male'] + features['female']
    with pytest.raises(ValueError, match='not full rank'):
        fit_full_rank_linear_model(features, pd.Series(np.arange(80)))
