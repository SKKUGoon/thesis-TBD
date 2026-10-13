import numpy as np
import pandas as pd
import pytest

from src.methods.boosting.run import forecast_last_observed_same_weekday
from src.methods.boosting.utility import calculate_provisioning_utility


def test_asymmetric_cost_and_coverage_have_known_values() -> None:
    result = calculate_provisioning_utility(np.array([10.,10.]), np.array([8.,13.]), np.array([10.,5.]), 4)
    assert result['scenario_cost'] == 11
    assert result['baseline_scenario_cost'] == 20
    assert result['relative_cost_reduction_percent'] == pytest.approx(45)
    assert result['coverage_proxy'] == .9


def test_missing_demand_is_rejected_instead_of_imputed() -> None:
    with pytest.raises(ValueError):
        calculate_provisioning_utility(np.array([np.nan]), np.array([5.]), np.array([5.]), 4)


def test_future_movements_do_not_change_as_of_baseline() -> None:
    target = pd.Series([2.,3.,999.], index=pd.to_datetime(['2024-01-01','2024-01-08','2024-01-22']))
    origin = pd.Timestamp('2024-01-15')
    dates = pd.DatetimeIndex([origin])
    baseline = forecast_last_observed_same_weekday(target, origin, dates)
    target.loc['2024-01-22'] = 1
    np.testing.assert_array_equal(baseline, forecast_last_observed_same_weekday(target, origin, dates))
    assert baseline[0] == 3
