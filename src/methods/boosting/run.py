from __future__ import annotations

import argparse
import importlib.metadata
import json
from pathlib import Path
import time

from catboost import CatBoostRegressor
import lightgbm as lgb
import numpy as np
import pandas as pd
import xgboost as xgb

from src.methods.arima.data import calculate_file_sha256
from src.methods.arima.run import build_completed_monday_origins
from src.methods.xgboost.forecasting import build_direct_horizon_matrix, build_lagged_origin_features
from src.methods.xgboost.known_calendar import read_known_calendar


def forecast_last_observed_same_weekday(target: pd.Series, origin: pd.Timestamp, dates: pd.DatetimeIndex) -> np.ndarray:
    """Defined baseline for observed movement targets without filling missing dates."""
    history = target.loc[target.index < origin].dropna()
    predictions = []
    for date in dates:
        same_weekday = history[history.index.dayofweek == date.dayofweek]
        if same_weekday.empty:
            raise ValueError('Baseline has no observed same-weekday history.')
        predictions.append(float(same_weekday.iloc[-1]))
    return np.asarray(predictions)


def fit_and_save_boosting_model(estimator: str, training: np.ndarray, labels: np.ndarray, config: dict, checkpoint: Path) -> object:
    common = {'learning_rate': .05, 'random_seed': config['seed']}
    if estimator == 'xgboost':
        model = xgb.train({'objective': 'reg:squarederror', 'tree_method': 'hist', 'max_depth': 3, 'eta': .05, 'min_child_weight': 20, 'colsample_bytree': .8, 'lambda': 10, 'max_bin': 64, 'nthread': config['threads'], 'seed': config['seed']}, xgb.DMatrix(training, label=labels), num_boost_round=config['boost_rounds'])
        model.save_model(str(checkpoint.with_suffix('.ubj')))
    elif estimator == 'catboost':
        model = CatBoostRegressor(**common, iterations=config['boost_rounds'], depth=3, l2_leaf_reg=10, border_count=64, loss_function='RMSE', thread_count=config['threads'], verbose=False, allow_writing_files=False)
        model.fit(training, labels)
        model.save_model(str(checkpoint.with_suffix('.cbm')))
    elif estimator == 'lightgbm':
        model = lgb.LGBMRegressor(n_estimators=config['boost_rounds'], learning_rate=.05, max_depth=3, num_leaves=8, min_child_samples=20, colsample_bytree=.8, reg_lambda=10, max_bin=64, n_jobs=config['threads'], random_state=config['seed'], verbosity=-1)
        model.fit(training, labels)
        model.booster_.save_model(str(checkpoint.with_suffix('.txt')))
    else:
        raise ValueError(estimator)
    return model


def build_and_write_two_item_targets(config: dict, output: Path) -> tuple[dict[str, pd.Series], dict[str, pd.DataFrame], dict[str, str]]:
    dates = pd.date_range('2020-01-01', '2024-12-31')
    drug_root = Path(config['drug_dataset'])
    group = pd.read_parquet(drug_root / 'daily_group_consumption.parquet').set_index('date')['consumption'].reindex(dates)
    items = pd.read_parquet(drug_root / 'daily_item_consumption.parquet').pivot(index='date', columns='item_code', values='recorded_count').reindex(dates)
    movements = pd.read_parquet(config['material_movements'])
    selected = movements[movements['품목코드'] == config['material_code']].copy()
    assert selected['단위'].nunique() == 1 and selected['단위'].iloc[0] == 'ea'
    assert not selected.date.duplicated().any()
    material = selected.set_index('date')['출고수량'].reindex(dates).astype(float)
    assert (material.dropna() >= 0).all()
    catalogue = pd.read_parquet(config['material_catalogue'])
    entry = catalogue[catalogue.item_code == config['material_code']].iloc[0]
    assert entry.exact_duplicate_excess_rows == 0 and not entry.structural_reasons
    names = {'drug': json.loads((drug_root / 'manifest.json').read_text())['group_members'][0]['product_name'], 'material': entry.display_name}
    patients = pd.read_parquet(Path(config['patient_dataset']) / 'daily_patient_composition.parquet').reindex(dates)
    columns = list(patients.columns)
    bases = {
        'drug': build_lagged_origin_features(items, patients, columns, config['drug_lags'], config['patient_lags']),
        'material': build_lagged_origin_features(pd.DataFrame(index=dates), patients, columns, 0, config['patient_lags']),
    }
    target_frame = pd.DataFrame({'drug': group, 'material': material}, index=dates).rename_axis('date')
    target_frame.to_parquet(output / 'targets.parquet')
    return {'drug': group, 'material': material}, bases, names


def run_and_write_two_item_boosting(config_path: Path, output: Path) -> None:
    started = time.perf_counter()
    output.mkdir(parents=True, exist_ok=False)
    config = json.loads(config_path.read_text())
    (output / 'config_snapshot.json').write_text(json.dumps(config, ensure_ascii=False, indent=2))
    targets, bases, names = build_and_write_two_item_targets(config, output)
    calendar = read_known_calendar(Path(config['calendar_reference']))
    fits = []
    for item_kind, target in targets.items():
        matrix, labels, matrix_origins, target_dates = build_direct_horizon_matrix(bases[item_kind], target, 14, calendar)
        values = matrix.to_numpy()
        (output / f'{item_kind}_feature_names.json').write_text(json.dumps(list(matrix.columns)))
        for estimator in ('xgboost', 'catboost', 'lightgbm'):
            for phase, start, end in [('validation','2023-07-01','2023-12-31'), ('evaluation','2024-01-01','2024-12-31')]:
                rows = []
                checkpoint_root = output / 'fitted_models' / item_kind / estimator / phase
                checkpoint_root.mkdir(parents=True)
                fitted_month = None
                for origin in build_completed_monday_origins(start, end, 14):
                    if origin.strftime('%Y-%m') != fitted_month:
                        fitted_month = origin.strftime('%Y-%m')
                        mask = np.isfinite(labels) & (target_dates < origin) & (matrix_origins < origin)
                        fit_started = time.perf_counter()
                        model = fit_and_save_boosting_model(estimator, values[mask], labels[mask], config, checkpoint_root / fitted_month)
                        training_end = target_dates[mask].max()
                        fits.append({'item': item_kind, 'model': estimator, 'phase': phase, 'origin': origin, 'training_start': target.index.min(), 'training_end': training_end, 'training_rows': int(mask.sum()), 'features': values.shape[1], 'seconds': time.perf_counter()-fit_started})
                    selection = matrix_origins == origin
                    order = np.argsort(matrix.loc[selection, 'horizon'].to_numpy())
                    dates = target_dates[selection][order]
                    inputs = values[selection][order]
                    prediction = model.predict(xgb.DMatrix(inputs) if estimator == 'xgboost' else inputs)
                    assert len(prediction) == 14 and np.isfinite(prediction).all()
                    baseline = forecast_last_observed_same_weekday(target, origin, dates)
                    for horizon, date, value, naive in zip(range(1,15), dates, prediction, baseline):
                        rows.append({'origin': origin, 'target_date': date, 'horizon_day': horizon, 'actual': target.loc[date], 'prediction_raw': float(value), 'prediction': max(0.,float(value)), 'naive_prediction': float(naive), 'input_end': origin-pd.Timedelta(days=1), 'training_end': training_end})
                predictions = pd.DataFrame(rows)
                predictions.to_parquet(output / f'{item_kind}_{estimator}_{phase}.parquet', index=False)
                observed = predictions.dropna(subset=['actual'])
                print(json.dumps({'item': names[item_kind], 'model': estimator, 'phase': phase, 'mae_observed': float((observed.actual-observed.prediction).abs().mean()), 'observed_rows': len(observed), 'all_rows': len(predictions)}, ensure_ascii=False), flush=True)
                pd.DataFrame(fits).to_parquet(output / 'fit_events.parquet', index=False)
    input_paths = [Path(config['drug_dataset'])/'manifest.json', Path(config['patient_dataset'])/'manifest.json', Path(config['material_movements']), Path(config['material_catalogue'])] + list(Path(config['calendar_reference']).glob('*.parquet'))
    manifest = {'status':'completed', 'names': names, 'elapsed_seconds':time.perf_counter()-started, 'versions':{name:importlib.metadata.version(name) for name in ['xgboost','catboost','lightgbm']}, 'input_sha256':{str(path):calculate_file_sha256(path) for path in input_paths}, 'code_sha256':{str(path):calculate_file_sha256(path) for path in Path('src/methods/boosting').glob('*.py')}, 'missing_imputed':False, 'target_zero_filled':False, 'material_observed_days':int(targets['material'].notna().sum()), 'targets':'drug consumption; material recorded issue quantity, not orders', 'model_design':'separate scalar horizon-aware pooled direct model per item and estimator', 'tuning':'none beyond inherited drug lag configuration; fixed exploratory comparison'}
    (output / 'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    arguments=parser.parse_args()
    run_and_write_two_item_boosting(arguments.config,arguments.output)
