from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time

import numpy as np
import pandas as pd
from scipy import sparse
from sklearn.linear_model import Ridge
from sklearn.preprocessing import MaxAbsScaler
import xgboost as xgb

from src.methods.arima.forecasting import forecast_weekly_naive
from .known_calendar import calculate_known_calendar_features, KnownCalendar, read_known_calendar


@dataclass(frozen=True)
class CompositionalForecastRun:
    predictions: pd.DataFrame
    fit_events: pd.DataFrame
    feature_names: tuple[str, ...]
    importance: pd.DataFrame


def build_lagged_origin_features(items: pd.DataFrame, patients: pd.DataFrame, patient_columns: list[str], drug_lags: int, patient_lags: int) -> pd.DataFrame:
    """Recorded item counts and patient composition are shifted strictly before origin."""
    arrays = {}
    for name in items:
        for lag in range(1, drug_lags + 1):
            arrays[f'item_{name}_lag_{lag}'] = items[name].shift(lag)
    for name in patient_columns:
        for lag in range(1, patient_lags + 1):
            arrays[f'patient_{name}_lag_{lag}'] = patients[name].shift(lag)
    return pd.DataFrame(arrays, index=items.index, dtype=np.float32)


def build_direct_horizon_matrix(base: pd.DataFrame, targets: pd.Series, horizon_days: int, calendar: KnownCalendar | None = None) -> tuple[pd.DataFrame, np.ndarray, pd.DatetimeIndex, pd.DatetimeIndex]:
    parts = []
    labels = []
    origin_indexes = []
    target_indexes = []
    complete_base = base.dropna()
    for horizon in range(1, horizon_days + 1):
        target_dates = complete_base.index + pd.Timedelta(days=horizon - 1)
        frame = complete_base.copy()
        frame['horizon'] = horizon
        frame['target_weekday'] = target_dates.dayofweek
        frame['target_year_sin'] = np.sin(2 * np.pi * target_dates.dayofyear / 365.25)
        frame['target_year_cos'] = np.cos(2 * np.pi * target_dates.dayofyear / 365.25)
        for weekday in range(1, 7):
            frame[f'target_weekday_{weekday}'] = (target_dates.dayofweek == weekday).astype(float)
        if calendar is not None:
            for name,values in calculate_known_calendar_features(complete_base.index,target_dates,calendar).items():
                frame[name]=values
        parts.append(frame)
        labels.append(targets.reindex(target_dates).to_numpy())
        origin_indexes.extend(complete_base.index)
        target_indexes.extend(target_dates)
    return pd.concat(parts, ignore_index=True).astype(np.float32), np.concatenate(labels), pd.DatetimeIndex(origin_indexes), pd.DatetimeIndex(target_indexes)


def run_monthly_compositional_forecasts(group: pd.Series, base: pd.DataFrame, origins: pd.DatetimeIndex, estimator: str, model_config: dict[str, object], horizon_days: int, alpha: float = 1, checkpoints: Path | None = None) -> CompositionalForecastRun:
    calendar = read_known_calendar(Path(model_config['calendar_reference'])) if 'calendar_reference' in model_config else None
    matrix, labels, matrix_origins, target_dates = build_direct_horizon_matrix(base, group, horizon_days, calendar)
    values = matrix.to_numpy()
    valid_labels = np.isfinite(labels)
    prediction_rows = []
    fit_rows = []
    model = None
    scaler = None
    fitted_month = None
    training_label_end = None
    importance_rows = []
    for origin in origins:
        if origin.strftime('%Y-%m') != fitted_month:
            fitted_month = origin.strftime('%Y-%m')
            mask = valid_labels & (target_dates < origin) & (matrix_origins < origin)
            if len(set(matrix_origins[mask])) < 365:
                raise ValueError('Insufficient distinct training origins.')
            training = values[mask]
            started = time.perf_counter()
            if estimator == 'xgboost':
                parameters = dict(model_config['xgboost_parameters'])
                parameters['seed'] = model_config['seed']
                model = xgb.train(parameters, xgb.DMatrix(training, label=labels[mask]), num_boost_round=model_config['boost_rounds'])
                importance_rows.extend({'month': fitted_month, 'feature': matrix.columns[int(key[1:])], 'gain': float(gain)} for key, gain in model.get_score(importance_type='gain').items())
            elif estimator == 'ridge':
                scaler = MaxAbsScaler()
                scaled = scaler.fit_transform(sparse.csr_matrix(training))
                model = Ridge(alpha=alpha, solver='lsqr', tol=1e-4)
                model.fit(scaled, labels[mask])
            else:
                raise ValueError(estimator)
            training_label_end = target_dates[mask].max()
            event = {'month': fitted_month, 'origin': origin, 'estimator': estimator, 'drug_history_start': group.index.min(), 'training_label_end': training_label_end, 'training_rows': int(mask.sum()), 'distinct_training_origins': int(pd.Index(matrix_origins[mask]).nunique()), 'features': len(matrix.columns), 'fit_seconds': time.perf_counter() - started}
            fit_rows.append(event)
            if checkpoints is not None:
                checkpoints.mkdir(parents=True, exist_ok=True)
                if estimator == 'xgboost':
                    model.save_model(checkpoints / f'{fitted_month}.ubj')
                else:
                    np.savez_compressed(checkpoints / f'{fitted_month}.npz', coefficients=model.coef_, intercept=model.intercept_, max_abs=scaler.max_abs_, alpha=alpha)
        prediction_mask = matrix_origins == origin
        horizon_values = matrix.loc[prediction_mask, 'horizon'].to_numpy()
        sort_order = np.argsort(horizon_values)
        forecast_matrix = values[prediction_mask][sort_order]
        dates = target_dates[prediction_mask][sort_order]
        if len(dates) != horizon_days:
            raise ValueError('Forecast input incomplete; no fallback.')
        if estimator == 'xgboost':
            raw = model.predict(xgb.DMatrix(forecast_matrix))
        else:
            raw = model.predict(scaler.transform(sparse.csr_matrix(forecast_matrix)))
        if not np.isfinite(raw).all():
            raise ValueError('Nonfinite forecast.')
        baseline = forecast_weekly_naive(group.loc[:origin - pd.Timedelta(days=1)], dates)
        for horizon, date, prediction, naive in zip(range(1, horizon_days + 1), dates, raw, baseline):
            prediction_rows.append({'origin': origin, 'target_date': date, 'horizon_day': horizon, 'available_data_end': origin - pd.Timedelta(days=1), 'parameter_training_end': training_label_end, 'prediction_raw': float(prediction), 'prediction_nonnegative': max(0.0, float(prediction)), 'naive_prediction': float(naive), 'actual': float(group.loc[date])})
    return CompositionalForecastRun(pd.DataFrame(prediction_rows), pd.DataFrame(fit_rows), tuple(matrix.columns), pd.DataFrame(importance_rows))
