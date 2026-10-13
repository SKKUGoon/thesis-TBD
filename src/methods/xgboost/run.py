from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.metadata
import json
from pathlib import Path
import resource
import subprocess
import time

import numpy as np
import pandas as pd

from src.methods.arima.data import calculate_file_sha256
from src.methods.arima.forecasting import calculate_fixed_seasonal_scale, calculate_group_forecast_metrics
from src.methods.arima.run import build_completed_monday_origins
from .forecasting import build_lagged_origin_features, run_monthly_compositional_forecasts


def run_and_write_xgboost_comparison(config_path: Path, run_directory: Path) -> None:
    started = time.perf_counter()
    run_directory.mkdir(parents=True, exist_ok=False)
    config = json.loads(config_path.read_text())
    (run_directory / 'config_snapshot.json').write_text(json.dumps(config, ensure_ascii=False, indent=2))
    group_root = Path(config['group_dataset'])
    patient_root = Path(config['patient_dataset'])
    group = pd.read_parquet(group_root / 'daily_group_consumption.parquet').set_index('date')['consumption']
    item_rows = pd.read_parquet(group_root / 'daily_item_consumption.parquet')
    items = item_rows.pivot(index='date', columns='item_code', values='recorded_count')
    patients = pd.read_parquet(patient_root / 'daily_patient_composition.parquet')
    patient_metadata = json.loads((patient_root / 'manifest.json').read_text())
    scale = calculate_fixed_seasonal_scale(group.loc[:'2022-12-31'])['scale']
    trials = []
    development_origins = build_completed_monday_origins(config['development_start'], config['development_end'], 14)
    selected = []
    for variant in config['variants']:
        columns = [] if variant == 'drug_calendar' else patient_metadata['feature_blocks']['total_sex' if variant == 'patient_total' else 'composition']
        pairs = config['lag_pairs'] if columns else [[7, 7], [28, 7]]
        for estimator in ['xgboost', 'ridge']:
            candidate_rows = []
            for drug_lags, patient_lags in pairs:
                alphas = [0] if estimator == 'xgboost' else config['ridge_alphas']
                for alpha in alphas:
                    base = build_lagged_origin_features(items, patients, columns, drug_lags, patient_lags)
                    fitted = run_monthly_compositional_forecasts(group, base, development_origins, estimator, config, 14, alpha)
                    metrics = calculate_group_forecast_metrics(fitted.predictions, scale)
                    if metrics['common_origins'] != len(development_origins):
                        raise ValueError('Development origin coverage changed.')
                    row = {'variant': variant, 'estimator': estimator, 'drug_lags': drug_lags, 'patient_lags': patient_lags if columns else 0, 'ridge_alpha': alpha, 'development_mae': metrics['first_14_days']['mean_daily_mae'], 'development_metrics': metrics, 'fit_seconds': float(fitted.fit_events.fit_seconds.sum()), 'features': len(fitted.feature_names)}
                    trials.append(row)
                    candidate_rows.append(row)
                    filename = f'{variant}_{estimator}_ky{drug_lags}_kp{patient_lags}_a{alpha}'
                    fitted.predictions.to_parquet(run_directory / f'dev_{filename}.parquet', index=False)
                    fitted.fit_events.to_parquet(run_directory / f'dev_fits_{filename}.parquet', index=False)
                    (run_directory / 'tuning_trials.json').write_text(json.dumps(trials, ensure_ascii=False, indent=2))
                    print(json.dumps({'phase': 'development', 'variant': variant, 'estimator': estimator, 'ky': drug_lags, 'kp': patient_lags, 'alpha': alpha, 'mae': row['development_mae'], 'seconds': row['fit_seconds']}, ensure_ascii=False), flush=True)
            selected.append(min(candidate_rows, key=lambda row: (row['development_mae'], row['features'], row['ridge_alpha'])))
    (run_directory / 'selection_frozen_before_evaluation.json').write_text(json.dumps({'selected': selected, 'actual_frozen_at_utc': datetime.now(timezone.utc).isoformat(), 'selection_end': config['development_end'], 'actual_XGBoost_model_parameters': config['xgboost_parameters'], 'boost_rounds': config['boost_rounds']}, ensure_ascii=False, indent=2))
    results = []
    for row in selected:
        columns = [] if row['variant'] == 'drug_calendar' else patient_metadata['feature_blocks']['total_sex' if row['variant'] == 'patient_total' else 'composition']
        base = build_lagged_origin_features(items, patients, columns, row['drug_lags'], max(1, row['patient_lags']))
        for phase in ['validation', 'evaluation']:
            origins = build_completed_monday_origins(config[f'{phase}_start'], config[f'{phase}_end'], 14)
            name = f"{phase}_{row['variant']}_{row['estimator']}"
            fitted = run_monthly_compositional_forecasts(group, base, origins, row['estimator'], config, 14, row['ridge_alpha'], run_directory / 'fitted_models' / name)
            metrics = calculate_group_forecast_metrics(fitted.predictions, scale)
            assert metrics['common_origins'] == len(origins)
            assert (fitted.predictions.parameter_training_end < fitted.predictions.origin).all()
            fitted.predictions.to_parquet(run_directory / f'{name}_predictions.parquet', index=False)
            fitted.fit_events.to_parquet(run_directory / f'{name}_fits.parquet', index=False)
            if not fitted.importance.empty:
                fitted.importance.to_parquet(run_directory / f'{name}_gain.parquet', index=False)
            (run_directory / f'{name}_features.json').write_text(json.dumps(fitted.feature_names, ensure_ascii=False))
            result = {'phase': phase, **row, 'metrics': metrics}
            results.append(result)
            (run_directory / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2))
            print(json.dumps({'phase': phase, 'variant': row['variant'], 'estimator': row['estimator'], 'mae': metrics['first_14_days']['mean_daily_mae'], 'skill': metrics['first_14_days']['skill_vs_final_naive']}, ensure_ascii=False), flush=True)
    pd.DataFrame([{'phase': row['phase'], 'variant': row['variant'], 'estimator': row['estimator'], 'span': span, **row['metrics'][f'first_{span}_days']} for row in results for span in [1, 7, 14]]).to_parquet(run_directory / 'metrics.parquet', index=False)
    manifest = {'status': 'completed', 'run_role': config['run_role'], 'plan_version': config['plan_version'], 'config': config, 'elapsed_seconds': time.perf_counter() - started, 'peak_RSS_MiB': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**20, 'libraries': {name: importlib.metadata.version(name) for name in ['numpy', 'pandas', 'pyarrow', 'xgboost', 'scikit-learn']}, 'code_hashes': {str(path): calculate_file_sha256(path) for path in Path('src/methods/xgboost').glob('*.py')}, 'dataset_manifest_hashes': {str(path): calculate_file_sha256(path) for path in [patient_root/'manifest.json', group_root/'manifest.json']}, 'git_head': subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(), 'initial_feature_vocabulary_end': '2022-12-31', 'selected': selected, 'failures': [], 'imputation_applied': False, 'source_original_values_changed': False, 'completed_at_utc': datetime.now(timezone.utc).isoformat(), 'limits': ['patient composition records, not confirmed all hospital visitors', 'one-day event-date availability scenario unverified', 'group total missing days kept missing', 'individual record-count zero features do not prove physical consumption zero', 'pooled horizon-aware direct regressor, not 14 independent models or joint VARX', 'diagnosis top32 and first-letter groups; all source ICD3 counts also preserved', 'current metadata grouping retrospective', '2024 previously examined; retrospective evaluation, not pristine holdout', 'nonlinear versus linear comparison depends on specified regularization and search budget']}
    (run_directory/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    print(json.dumps({'completed': str(run_directory), 'elapsed_seconds': manifest['elapsed_seconds']},ensure_ascii=False),flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    try:
        run_and_write_xgboost_comparison(args.config, args.output)
    except Exception as error:
        if args.output.exists():
            (args.output/'manifest.json').write_text(json.dumps({'status':'failed','error_type':type(error).__name__,'error':str(error)},ensure_ascii=False,indent=2))
        raise
