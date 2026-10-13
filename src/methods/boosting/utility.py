from __future__ import annotations

import numpy as np
import pandas as pd


def calculate_provisioning_utility(demand: np.ndarray, provision: np.ndarray, baseline_provision: np.ndarray, underage_ratio: float) -> dict[str, float]:
    """Normalized single-period cost proxy with overage cost=1; no inventory trajectory."""
    demand = np.asarray(demand, dtype=float)
    provision = np.asarray(provision, dtype=float)
    baseline_provision = np.asarray(baseline_provision, dtype=float)
    if demand.shape != provision.shape or demand.shape != baseline_provision.shape:
        raise ValueError('Demand and both provisioning arrays must align.')
    if not len(demand) or not all(np.isfinite(values).all() for values in (demand, provision, baseline_provision)):
        raise ValueError('Utility requires observed, finite demand; no missing-value fill.')
    if any((values < 0).any() for values in (demand, provision, baseline_provision)) or underage_ratio <= 0:
        raise ValueError('Nonnegative quantities and positive cost ratio required.')
    shortage = np.maximum(demand-provision, 0)
    excess = np.maximum(provision-demand, 0)
    cost = float((underage_ratio*shortage+excess).sum())
    baseline_cost = float((underage_ratio*np.maximum(demand-baseline_provision,0)+np.maximum(baseline_provision-demand,0)).sum())
    return {'periods':len(demand), 'observed_demand':float(demand.sum()), 'shortage_proxy':float(shortage.sum()), 'excess_proxy':float(excess.sum()), 'scenario_cost':cost, 'baseline_scenario_cost':baseline_cost, 'relative_cost_reduction_percent':100*(1-cost/baseline_cost) if baseline_cost else np.nan, 'coverage_proxy':float(np.minimum(demand,provision).sum()/demand.sum()) if demand.sum() else np.nan, 'cycle_coverage_proxy':float((provision>=demand).mean())}


def build_nonoverlapping_provisioning_periods(predictions: pd.DataFrame, item_kind: str) -> pd.DataFrame:
    if item_kind == 'drug':
        origins = sorted(predictions.origin.unique())[::2]
        selected = predictions[predictions.origin.isin(origins)]
        grouped = selected.groupby('origin')
        if not (grouped.actual.count() == 14).all():
            raise ValueError('Drug utility requires fully observed 14-day windows.')
        periods = grouped.agg(demand=('actual','sum'), forecast=('prediction','sum'), baseline=('naive_prediction','sum')).reset_index()
        dates = selected.target_date
        if dates.duplicated().any():
            raise ValueError('Utility periods overlap.')
    elif item_kind == 'material':
        selected = predictions[(predictions.horizon_day <= 7) & predictions.actual.notna()]
        if selected.target_date.duplicated().any():
            raise ValueError('Observed daily movement utility periods overlap.')
        periods = selected[['target_date','actual','prediction','naive_prediction']].rename(columns={'actual':'demand','prediction':'forecast','naive_prediction':'baseline'}).copy()
    else:
        raise ValueError(item_kind)
    periods['provision'] = np.ceil(periods.forecast.clip(lower=0))
    periods['baseline_provision'] = np.ceil(periods.baseline.clip(lower=0))
    return periods
