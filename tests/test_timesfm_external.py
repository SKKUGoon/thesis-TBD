from pathlib import Path

import numpy as np
import pandas as pd

from src.methods.timesfm.external_data import build_patient_features, normalize_external_records
from src.methods.timesfm.external_run import add_wape
from src.methods.timesfm.seasonal_comparison import compare_saved_drug_forecasts


def test_missing_patient_date_is_not_zero_and_insurance_is_not_encounter() -> None:
    frame = pd.DataFrame({'date':['20220101','20220101'], '차트번호':['synthetic','synthetic'],
                          '청구코드':['111111111','111111111'], '한글명칭':['synthetic drug']*2,
                          'male':['1','1'],'age':['30','30'],'kcd1':['D50','D50'],'kcd2':['','']})
    records = normalize_external_records(frame,'epurun')
    features = build_patient_features(records,pd.date_range('2022-01-01',periods=2))
    assert len(records)==2
    assert features.loc['2022-01-01','total__ALL']==1
    assert features.loc['2022-01-01','encounter__OTHER']==1
    assert features.loc['2022-01-01','main_kcd__III']==1
    assert features.loc['2022-01-02'].isna().all()


def test_unobserved_drug_stays_in_metrics_without_substitution() -> None:
    dates = pd.date_range('2023-12-25',periods=21)
    targets = pd.DataFrame({'drug_01':np.arange(1,22,dtype=float),'drug_02':np.nan},index=dates)
    origin = pd.Timestamp('2024-01-01')
    rows = []
    for channel in targets:
        for date in dates[7:]:
            rows.append({'channel':channel,'origin':origin,'target_date':date,
                         'input_end':origin-pd.Timedelta(days=1),'actual':targets.at[date,channel],
                         'prediction':8.,'observed':pd.notna(targets.at[date,channel])})
    forecasts = pd.DataFrame(rows)
    cohort = pd.DataFrame({'channel':['drug_01','drug_02'],'name':['one','two'],'cohort':['drug','drug']})
    audit, comparison = compare_saved_drug_forecasts(forecasts,targets,cohort)
    missing = comparison.set_index('channel').loc['drug_02']
    assert missing.common_rows==0 and pd.isna(missing.baseline_wape_percent)
    assert missing.possible_rows==14
    assert (audit.baseline_source_date<audit.origin).all()
    base_metrics = pd.DataFrame({'channel':['drug_01','drug_02']})
    enriched = add_wape(base_metrics,forecasts)
    assert len(enriched)==2 and pd.isna(enriched.set_index('channel').loc['drug_02','wape_percent'])
