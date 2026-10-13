import pandas as pd
import pytest

from src.methods.order_policy.simulation import InventoryScenario, calculate_as_of_protection_forecast, simulate_order_policy


def test_lead_time_and_lost_shortage_are_accounted_once() -> None:
    dates=pd.date_range('2024-01-01',periods=3)
    ledger=simulate_order_policy(pd.Series([5.,5.,5.],index=dates),pd.Series(10.,index=dates),InventoryScenario(1,1,1,10,5,0))
    assert ledger.received.tolist()==[0,10,0]
    assert ledger.shortage.tolist()==[5,0,0]
    assert ledger.shortage_cost.sum()==50
    assert ledger.end_inventory.tolist()==[0,5,0]


def test_missing_calendar_gap_resets_instead_of_filling_zero() -> None:
    dates=pd.to_datetime(['2024-01-01','2024-01-03'])
    ledger=simulate_order_policy(pd.Series(3.,index=dates),pd.Series(5.,index=dates),InventoryScenario(1,1,1,10,5,5))
    assert ledger.segment.tolist()==[0,1]
    assert ledger.end_inventory.tolist()==[2,2]
    with pytest.raises(ValueError):
        simulate_order_policy(pd.Series([float('nan')],index=dates[:1]),pd.Series(5.,index=dates[:1]),InventoryScenario(1,1,1,10,5,5))


def test_future_forecast_origin_is_not_used() -> None:
    predictions=pd.DataFrame({'origin':pd.to_datetime(['2024-01-01','2024-01-01','2024-01-08']), 'target_date':pd.to_datetime(['2024-01-03','2024-01-04','2024-01-08']), 'prediction':[2.,3.,999.]})
    result=calculate_as_of_protection_forecast(predictions,pd.DatetimeIndex(['2024-01-03']),2)
    assert result.iloc[0]==5
