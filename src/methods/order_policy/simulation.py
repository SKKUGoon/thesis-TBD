from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class InventoryScenario:
    lead_days: int
    review_days: int
    holding_cost: float
    shortage_cost: float
    order_cost: float
    initial_stock: int


def simulate_order_policy(demand: pd.Series, target_stock: pd.Series, scenario: InventoryScenario) -> pd.DataFrame:
    """Receive, review/order, fulfill, charge end-day stock; reset only at missing-date gaps."""
    if not demand.index.equals(target_stock.index) or demand.isna().any() or target_stock.isna().any():
        raise ValueError('Simulation requires aligned observed demand and finite policy targets.')
    if (demand<0).any() or (target_stock<0).any() or scenario.lead_days<1 or scenario.review_days<1:
        raise ValueError('Invalid inventory scenario.')
    rows=[]
    previous_date=None
    segment=-1
    for date, quantity in demand.items():
        if previous_date is None or (date-previous_date).days!=1:
            segment+=1
            on_hand=float(scenario.initial_stock)
            pipeline={}
            segment_position=0
        received=pipeline.pop(date,0.)
        on_hand+=received
        inventory_position=on_hand+sum(pipeline.values())
        order=0.
        if segment_position%scenario.review_days==0:
            order=float(np.ceil(max(0.,target_stock.loc[date]-inventory_position)))
            if order:
                arrival=date+pd.Timedelta(days=scenario.lead_days)
                pipeline[arrival]=pipeline.get(arrival,0.)+order
        fulfilled=min(on_hand,float(quantity))
        shortage=float(quantity)-fulfilled
        on_hand-=fulfilled
        holding=on_hand*scenario.holding_cost
        shortage_cost=shortage*scenario.shortage_cost
        fixed=scenario.order_cost if order>0 else 0.
        rows.append({'date':date,'segment':segment,'demand':float(quantity),'target_stock':float(target_stock.loc[date]),'initial_stock':scenario.initial_stock,'received':received,'ordered':order,'served':fulfilled,'shortage':shortage,'end_inventory':on_hand,'pipeline':sum(pipeline.values()),'holding_cost':holding,'shortage_cost':shortage_cost,'order_cost':fixed,'total_cost':holding+shortage_cost+fixed})
        previous_date=date
        segment_position+=1
    ledger=pd.DataFrame(rows)
    for _,frame in ledger.groupby('segment'):
        assert np.isclose(scenario.initial_stock+frame.received.sum(),frame.served.sum()+frame.end_inventory.iloc[-1])
        assert np.isclose(frame.ordered.sum(),frame.received.sum()+frame.pipeline.iloc[-1])
    return ledger


def summarize_inventory_policy(ledger: pd.DataFrame) -> dict[str,float]:
    return {'days':len(ledger),'segments':ledger.segment.nunique(),'demand':float(ledger.demand.sum()),'cost':float(ledger.total_cost.sum()),'holding_cost':float(ledger.holding_cost.sum()),'shortage_cost':float(ledger.shortage_cost.sum()),'order_cost':float(ledger.order_cost.sum()),'fill_rate':float(ledger.served.sum()/ledger.demand.sum()),'shortage_units':float(ledger.shortage.sum()),'shortage_days':int((ledger.shortage>0).sum()),'average_inventory':float(ledger.end_inventory.mean()),'maximum_inventory':float(ledger.end_inventory.max()),'orders':int((ledger.ordered>0).sum()),'ordered_units':float(ledger.ordered.sum()),'terminal_pipeline_units':float(ledger.groupby('segment').pipeline.last().sum())}


def calculate_as_of_protection_forecast(predictions: pd.DataFrame, dates: pd.DatetimeIndex, protection_days: int) -> pd.Series:
    origins=np.array(sorted(predictions.origin.unique()),dtype='datetime64[ns]')
    values=predictions.set_index(['origin','target_date'])['prediction']
    result={}
    for date in dates:
        position=origins.searchsorted(date.to_datetime64(),side='right')-1
        if position<0:
            continue
        origin=pd.Timestamp(origins[position])
        future_dates=pd.date_range(date,periods=protection_days)
        keys=pd.MultiIndex.from_product([[origin],future_dates])
        selected=values.reindex(keys)
        if selected.isna().any():
            continue
        result[date]=float(selected.sum())
    return pd.Series(result,dtype=float).rename('forecast_protected_demand')
