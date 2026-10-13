from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm
from statsmodels.stats.multitest import multipletests

from src.methods.arima.data import calculate_file_sha256


def build_origin_available_features(group: pd.Series, patients: pd.DataFrame, horizon: int) -> tuple[pd.DataFrame, dict[str, list[str]]]:
    """Index is forecast origin; all stochastic inputs end at origin minus one day."""
    features = pd.DataFrame(index=group.index)
    blocks: dict[str, list[str]] = {'drug_ar': [], 'weekday': [], 'annual_calendar': [], 'total_patients': [], 'sex_patients': []}
    for lag in range(1, 8):
        column = f'group_lag_{lag}'
        features[column] = group.shift(lag)
        blocks['drug_ar'].append(column)
    target_dates = features.index + pd.Timedelta(days=horizon - 1)
    for weekday in range(1, 7):
        column = f'target_weekday_{weekday}'
        features[column] = (target_dates.dayofweek == weekday).astype(float)
        blocks['weekday'].append(column)
    for name, values in [('annual_sin', np.sin(2 * np.pi * target_dates.dayofyear / 365.25)), ('annual_cos', np.cos(2 * np.pi * target_dates.dayofyear / 365.25))]:
        features[name] = values
        blocks['annual_calendar'].append(name)
    for source, block in [('total_patients', 'total_patients'), ('male_patients', 'sex_patients'), ('female_patients', 'sex_patients')]:
        for lag in range(1, 29):
            column = f'{source}_lag_{lag}'
            features[column] = patients[source].reindex(group.index).shift(lag)
            blocks[block].append(column)
    return features, blocks


def fit_full_rank_linear_model(features: pd.DataFrame, target: pd.Series, hac_lags: int = 7):
    design = sm.add_constant(features, has_constant='add')
    if np.linalg.matrix_rank(design.to_numpy()) != design.shape[1]:
        raise ValueError('Feature design is not full rank; no silent column removal.')
    return sm.OLS(target, design).fit(cov_type='HAC', cov_kwds={'maxlags': hac_lags})


def run_and_write_feature_screening(output: Path) -> None:
    output.mkdir(parents=True, exist_ok=False)
    group_path = Path('data/processed/cheonan/analysis/arima_native_missing_20261007_01/daily_group_consumption.parquet')
    patient_path = Path('data/processed/cheonan/diagnostics/eda_20261006_receipt_drug/daily_patient_counts.csv')
    group = pd.read_parquet(group_path).set_index('date')['consumption']
    patients = pd.read_csv(patient_path, parse_dates=['date']).set_index('date')
    assert (patients['total_patients'] == patients[['male_patients', 'female_patients', 'other_or_conflicting_patients']].sum(axis=1)).all()
    hypotheses = []
    scores = []
    forecast_rows = []
    for horizon in [1, 7, 14]:
        features, blocks = build_origin_available_features(group, patients, horizon)
        target = group.shift(-(horizon - 1)).rename('target')
        joint = features.join(target).dropna()
        training = joint.loc[joint.index + pd.Timedelta(days=horizon - 1) <= pd.Timestamp('2022-12-31')]
        baseline_columns = blocks['drug_ar']
        for block in ['weekday', 'annual_calendar', 'total_patients', 'sex_patients']:
            conditioning_columns = baseline_columns if block == 'weekday' else baseline_columns + blocks['weekday']
            columns = conditioning_columns + blocks[block]
            model = fit_full_rank_linear_model(training[columns], training['target'], max(7, horizon - 1))
            restriction = np.zeros((len(blocks[block]), len(model.params)))
            for row, column in enumerate(blocks[block]):
                restriction[row, model.params.index.get_loc(column)] = 1
            joint_test = model.f_test(restriction)
            hypotheses.append({'horizon': horizon, 'block': block, 'observations': len(training), 'joint_hac_f': float(joint_test.fvalue), 'p_value': float(joint_test.pvalue), 'conditioning_columns': conditioning_columns, 'tested_columns': blocks[block], 'interpretation': 'conditional predictive association, not causality'})
        candidates = {'ar7': baseline_columns, 'ar7_weekday': baseline_columns + blocks['weekday'], 'ar7_weekday_total': baseline_columns + blocks['weekday'] + blocks['total_patients'], 'ar7_weekday_sex': baseline_columns + blocks['weekday'] + blocks['sex_patients'], 'ar7_weekday_annual': baseline_columns + blocks['weekday'] + blocks['annual_calendar']}
        origins = pd.date_range('2023-01-01', pd.Timestamp('2023-06-30') - pd.Timedelta(days=13), freq='W-MON')
        for name, columns in candidates.items():
            fitted_month = None
            model = None
            errors = []
            for origin in origins:
                if origin.strftime('%Y-%m') != fitted_month:
                    fitted_month = origin.strftime('%Y-%m')
                    available = joint.loc[joint.index + pd.Timedelta(days=horizon - 1) < origin]
                    model = fit_full_rank_linear_model(available[columns], available['target'], max(7, horizon - 1))
                    parameter_training_label_end = available.index.max() + pd.Timedelta(days=horizon - 1)
                row = sm.add_constant(features.loc[[origin], columns], has_constant='add')
                prediction = max(0.0, float(model.predict(row).iloc[0]))
                actual = float(target.loc[origin])
                errors.append(abs(prediction - actual))
                forecast_rows.append({'horizon': horizon, 'model': name, 'origin': origin, 'target_date': origin + pd.Timedelta(days=horizon - 1), 'available_data_end': origin - pd.Timedelta(days=1), 'parameter_training_label_end': parameter_training_label_end, 'prediction': prediction, 'actual': actual})
            scores.append({'horizon': horizon, 'model': name, 'origins': len(errors), 'development_point_horizon_mae': float(np.mean(errors))})
        altered_group = group.copy()
        altered_patients = patients.copy()
        cutoff = pd.Timestamp('2022-10-03')
        altered_group.loc[cutoff:] = 10000
        altered_patients.loc[cutoff:, ['male_patients', 'female_patients', 'total_patients']] = 10000
        changed_features, _ = build_origin_available_features(altered_group, altered_patients, horizon)
        pd.testing.assert_frame_equal(features.loc[:cutoff], changed_features.loc[:cutoff])
    adjusted = multipletests([row['p_value'] for row in hypotheses], method='fdr_bh')[1]
    for row, value in zip(hypotheses, adjusted):
        row['bh_adjusted_p'] = float(value)
    pd.DataFrame(hypotheses).to_parquet(output / 'initial_hac_tests.parquet', index=False)
    pd.DataFrame(scores).to_parquet(output / 'development_scores.parquet', index=False)
    pd.DataFrame(forecast_rows).to_parquet(output / 'development_predictions.parquet', index=False)
    manifest = {'status': 'completed', 'role': 'exploratory_X_screening_group_total_direct_linear_probe_not_VARX_execution', 'group_source': str(group_path), 'group_sha256': calculate_file_sha256(group_path), 'patient_source': str(patient_path), 'patient_sha256': calculate_file_sha256(patient_path), 'code_sha256': calculate_file_sha256(Path(__file__)), 'imputation_applied': False, 'initial_end': '2022-12-31', 'development_end': '2023-06-30', 'evaluated_2024': False, 'forecast_horizons': [1, 7, 14], 'future_feature_mutation_check': 'passed', 'availability': 'unverified origin-minus-one-day scenario', 'limitations': ['record-bearing patients, not confirmed all hospital visitors', 'metadata group previously selected using retrospective catalog', 'post-pilot exploratory feature screening, not untouched confirmatory test', 'one-day stochastic availability not verified', 'no policy history table or encounter-category aggregation created', 'individual-code VARX not fitted; group-total feature probe only'], 'scores': scores, 'joint_tests': hypotheses}
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(json.dumps({'output': str(output), 'scores': scores, 'joint_tests': [{'horizon': row['horizon'], 'block': row['block'], 'bh_adjusted_p': row['bh_adjusted_p']} for row in hypotheses]}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    arguments = parser.parse_args()
    run_and_write_feature_screening(arguments.output)
