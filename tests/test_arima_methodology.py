from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.methods.arima.data import build_and_write_group_dataset, extract_brand_and_form, select_single_group_from_metadata
from src.methods.arima.forecasting import calculate_fixed_seasonal_scale, calculate_history_sha256, forecast_weekly_naive, run_monthly_expanding_arima, slice_available_history


def synthetic_metadata():
    master = [{'일자': '2020-01-01', '품목코드': code, '품목명': '시험캡슐100mg', '단위': '캡슐', '규격': ''} for code in ['A', 'B']]
    catalog = [{'제품명': '시험캡슐100밀리그램(시험성분)', '허가일': '2019-01-01', '함량': '100MG', 'atc_code': 'N03AX12', '주성분': '시험성분', 'item_seq': 'synthetic', '업체명': 'synthetic'}]
    return master, catalog


def test_grouping_ignores_consumption_and_product_switches():
    master, catalog = synthetic_metadata()
    initial = select_single_group_from_metadata(master, catalog)
    for row in master:
        row['consumption'] = '100000' if row['품목코드'] == 'A' else '0'
    changed = select_single_group_from_metadata(master, catalog)
    assert [asdict(member) for member in initial.members] == [asdict(member) for member in changed.members]
    a = np.array([12, 12, 0, 0, 12, 12])
    b = np.array([0, 0, 12, 12, 0, 0])
    assert np.all(a + b == 12)
    assert np.corrcoef(a, b)[0, 1] < 0


@pytest.mark.parametrize('invalid', ['missing_unit', 'ambiguous_product', 'missing_form'])
def test_ambiguous_or_incomplete_metadata_cannot_form_group(invalid):
    master, catalog = synthetic_metadata()
    if invalid == 'missing_unit':
        for row in master:
            row['단위'] = ''
    elif invalid == 'ambiguous_product':
        catalog.append({**catalog[0], 'item_seq': 'different-product'})
    else:
        for row in master:
            row['품목명'] = '미상100mg'
        catalog[0]['제품명'] = '미상100mg'
        assert extract_brand_and_form('미상100mg') is None
    with pytest.raises(ValueError, match='No comparable'):
        select_single_group_from_metadata(master, catalog)


def test_repeated_rows_preserved_and_absence_not_imputed(tmp_path: Path):
    master, catalog = synthetic_metadata()
    original = tmp_path / 'original'
    original.mkdir()
    pd.DataFrame(master).to_parquet(original / '약품.parquet')
    pd.DataFrame(catalog).to_parquet(tmp_path / 'atc.parquet')
    pd.DataFrame({'품목코드': ['A', 'A', 'B'], '의약품사용일시': ['2020-01-01 09:00:00'] * 3}).to_parquet(original / '20200101~20200630.parquet')
    series, metadata = build_and_write_group_dataset(original, tmp_path / 'atc.parquet', tmp_path / 'output')
    assert series.loc['2020-01-01'] == 3
    assert pd.isna(series.loc['2020-01-02'])
    assert metadata['selected_rows'] == 3
    assert metadata['imputation_applied'] is False


def test_future_mutation_does_not_change_fit_or_forecasts():
    random = np.random.default_rng(123)
    series = pd.Series(10 + random.normal(size=180), index=pd.date_range('2020-01-01', periods=180, freq='D'))
    series.iloc[10:15] = np.nan
    origin = pd.Timestamp('2020-05-04')
    changed = series.copy()
    changed.loc[origin:] += 10000
    history = slice_available_history(series, origin, 1)
    assert history.isna().sum() == 5
    assert calculate_history_sha256(history) == calculate_history_sha256(slice_available_history(changed, origin, 1))
    first = run_monthly_expanding_arima(series, pd.DatetimeIndex([origin]), (1, 0, 0), 14, 1, 200)
    second = run_monthly_expanding_arima(changed, pd.DatetimeIndex([origin]), (1, 0, 0), 14, 1, 200)
    assert not first.failures and not second.failures
    np.testing.assert_allclose(first.predictions['prediction_raw'], second.predictions['prediction_raw'])
    assert np.isfinite(first.predictions['prediction_raw']).all()
    assert first.fit_events.iloc[0]['missing_rows'] == 5


def test_second_week_naive_uses_available_week_not_future_actuals():
    history = pd.Series(np.arange(70), index=pd.date_range('2020-01-01', periods=70, freq='D'))
    dates = pd.date_range(history.index[-1] + pd.Timedelta(days=1), periods=14)
    forecast = forecast_weekly_naive(history, dates)
    np.testing.assert_array_equal(forecast[:7], history.iloc[-7:])
    np.testing.assert_array_equal(forecast[7:], history.iloc[-7:])


def test_delayed_cutoff_and_undefined_mase_are_explicit():
    series = pd.Series(10.0, index=pd.date_range('2020-01-01', periods=100, freq='D'))
    history = slice_available_history(series, pd.Timestamp('2020-04-01'), 7)
    assert history.index.max() == pd.Timestamp('2020-03-25')
    assert calculate_fixed_seasonal_scale(history)['scale'] is None
    with pytest.raises(ValueError):
        slice_available_history(series, pd.Timestamp('2020-04-01'), 0)
