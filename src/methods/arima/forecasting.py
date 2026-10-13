from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
import warnings

import numpy as np
import pandas as pd
from statsmodels.tsa.arima.model import ARIMA


class NonconvergedArimaError(RuntimeError):
    pass


@dataclass(frozen=True)
class ArimaForecastRun:
    predictions: pd.DataFrame
    fit_events: pd.DataFrame
    failures: tuple[dict[str, object], ...]


def slice_available_history(series: pd.Series, origin: pd.Timestamp, delay_days: int) -> pd.Series:
    if delay_days < 1:
        raise ValueError('Availability delay must be at least one day.')
    history = series.loc[:origin - pd.Timedelta(days=delay_days)].copy()
    if history.empty or history.index.has_duplicates or not history.index.equals(pd.date_range(history.index.min(), history.index.max(), freq='D')):
        raise ValueError('History must retain a continuous daily index, including explicit NaN observations.')
    if int(history.notna().sum()) < 60:
        raise ValueError('Insufficient observed training values.')
    return history


def calculate_history_sha256(history: pd.Series) -> str:
    values = [{'date': date.date().isoformat(), 'value': None if pd.isna(value) else float(value)} for date, value in history.items()]
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def calculate_fixed_seasonal_scale(initial_history: pd.Series) -> dict[str, object]:
    errors = (initial_history - initial_history.shift(7)).abs().dropna()
    scale = float(errors.mean()) if len(errors) else float('nan')
    return {'seasonal_period': 7, 'initial_period_end': str(initial_history.index.max().date()), 'complete_pair_count': len(errors), 'scale': scale if scale > 0 else None}


def forecast_weekly_naive(history: pd.Series, target_dates: pd.DatetimeIndex) -> np.ndarray:
    forecasts = []
    for target_date in target_dates:
        source_date = target_date - pd.Timedelta(days=7)
        while source_date > history.index.max():
            source_date -= pd.Timedelta(days=7)
        value = history.get(source_date, float('nan'))
        forecasts.append(float(value))
    return np.asarray(forecasts)


def fit_converged_arima(history: pd.Series, order: tuple[int, int, int], maximum_iterations: int) -> tuple[object, list[str]]:
    with warnings.catch_warnings(record=True) as recorded_warnings:
        warnings.simplefilter('always')
        model = ARIMA(history, order=order, trend='c' if order[1] == 0 else 'n', missing='none')
        fitted_model = model.fit(method='statespace', method_kwargs={'maxiter': maximum_iterations}, cov_type='none')
    if not fitted_model.mle_retvals.get('converged', False):
        raise NonconvergedArimaError(f'ARIMA{order} did not converge: {fitted_model.mle_retvals}')
    if not np.isfinite(fitted_model.params).all():
        raise NonconvergedArimaError('Non-finite fitted ARIMA parameters.')
    return fitted_model, [str(warning.message) for warning in recorded_warnings]


def run_monthly_expanding_arima(series: pd.Series, origins: pd.DatetimeIndex, order: tuple[int, int, int], horizon_days: int, delay_days: int, maximum_iterations: int, checkpoint_directory: Path | None = None) -> ArimaForecastRun:
    prediction_rows: list[dict[str, object]] = []
    fit_rows: list[dict[str, object]] = []
    failures: list[dict[str, object]] = []
    fitted_model = None
    fitted_month = None
    monthly_failure = None
    for origin in origins:
        history = slice_available_history(series, origin, delay_days)
        month = origin.strftime('%Y-%m')
        if fitted_month != month:
            fitted_month = month
            fitted_model = None
            monthly_failure = None
            started = time.perf_counter()
            try:
                fitted_model, warning_messages = fit_converged_arima(history, order, maximum_iterations)
            except (NonconvergedArimaError, np.linalg.LinAlgError) as error:
                monthly_failure = str(error)
                failures.append({'origin': str(origin.date()), 'month': month, 'order': list(order), 'stage': 'monthly_fit', 'error': monthly_failure})
                continue
            fit_event = {'origin': str(origin.date()), 'month': month, 'training_end': str(history.index.max().date()), 'history_sha256': calculate_history_sha256(history), 'calendar_rows': len(history), 'observed_rows': int(history.notna().sum()), 'missing_rows': int(history.isna().sum()), 'fit_seconds': time.perf_counter() - started, 'actual_fit_at_utc': datetime.now(timezone.utc).isoformat(), 'order': list(order), 'parameter_names': fitted_model.param_names, 'parameters': fitted_model.params.tolist(), 'warnings': warning_messages, 'converged': True}
            fit_rows.append(fit_event)
            if checkpoint_directory is not None:
                checkpoint_directory.mkdir(parents=True, exist_ok=True)
                (checkpoint_directory / f'{month}.json').write_text(json.dumps(fit_event, ensure_ascii=False, indent=2))
        if fitted_model is None:
            failures.append({'origin': str(origin.date()), 'month': month, 'order': list(order), 'stage': 'forecast_unavailable_after_failed_monthly_fit', 'error': monthly_failure})
            continue
        # Refilter only available history with fixed monthly parameters; do not refit between months.
        with warnings.catch_warnings(record=True) as state_warnings:
            warnings.simplefilter('always')
            state = ARIMA(history, order=order, trend='c' if order[1] == 0 else 'n', missing='none').filter(fitted_model.params, cov_type='none')
        forecast_values = np.asarray(state.forecast(horizon_days + delay_days - 1))[delay_days - 1:]
        if not np.isfinite(forecast_values).all():
            failures.append({'origin': str(origin.date()), 'order': list(order), 'stage': 'forecast', 'error': 'nonfinite_forecast'})
            continue
        target_dates = pd.date_range(origin, periods=horizon_days, freq='D')
        naive_values = forecast_weekly_naive(history, target_dates)
        for horizon, date, raw_prediction, naive_prediction in zip(range(1, horizon_days + 1), target_dates, forecast_values, naive_values):
            prediction_rows.append({'origin': origin, 'target_date': date, 'horizon_day': horizon, 'available_data_end': history.index.max(), 'parameter_training_end': pd.Timestamp(fit_rows[-1]['training_end']), 'history_sha256': calculate_history_sha256(history) if horizon == 1 else None, 'state_update_warnings': [str(warning.message) for warning in state_warnings] if horizon == 1 else [], 'prediction_raw': float(raw_prediction), 'prediction_nonnegative': max(0.0, float(raw_prediction)), 'naive_prediction': float(naive_prediction), 'actual': float(series.get(date, float('nan')))})
    return ArimaForecastRun(pd.DataFrame(prediction_rows), pd.DataFrame(fit_rows), tuple(failures))


def calculate_group_forecast_metrics(predictions: pd.DataFrame, seasonal_scale: float | None) -> dict[str, object]:
    if predictions.empty:
        return {'status': 'no_predictions'}
    complete_origins = []
    for origin, frame in predictions.groupby('origin'):
        if len(frame) == 14 and frame[['actual', 'prediction_nonnegative', 'naive_prediction']].notna().all().all():
            complete_origins.append(origin)
    evaluation = predictions[predictions['origin'].isin(complete_origins)]
    if evaluation.empty:
        return {'status': 'no_complete_common_vectors'}
    metrics: dict[str, object] = {'status': 'evaluated', 'common_origins': len(complete_origins), 'target_rows': len(evaluation), 'excluded_prediction_origins': int(predictions['origin'].nunique() - len(complete_origins)), 'negative_raw_predictions': int((evaluation['prediction_raw'] < 0).sum())}
    for span in [1, 7, 14]:
        selected = evaluation[evaluation['horizon_day'] <= span]
        error = selected['prediction_nonnegative'] - selected['actual']
        naive_error = selected['naive_prediction'] - selected['actual']
        mae = float(error.abs().mean())
        naive_mae = float(naive_error.abs().mean())
        totals = selected.groupby('origin')[['prediction_nonnegative', 'naive_prediction', 'actual']].sum()
        metrics[f'first_{span}_days'] = {'mean_daily_mae': mae, 'raw_mean_daily_mae': float((selected['prediction_raw'] - selected['actual']).abs().mean()), 'mean_daily_bias': float(error.mean()), 'mean_daily_mase': mae / seasonal_scale if seasonal_scale else None, 'naive_mean_daily_mae': naive_mae, 'skill_vs_final_naive': 1 - mae / naive_mae if naive_mae else None, 'cumulative_demand_mae': float((totals['prediction_nonnegative'] - totals['actual']).abs().mean()), 'naive_cumulative_demand_mae': float((totals['naive_prediction'] - totals['actual']).abs().mean())}
    return metrics
