from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from src.methods.arima.data import calculate_file_sha256


SOURCE_COLUMNS = ['병록번호', '의약품사용일시', '주상병코드', '부상병코드', '성별코드', '나이', '외래입원여부']


@dataclass(frozen=True)
class PatientCompositionDataset:
    daily_features: pd.DataFrame
    feature_blocks: dict[str, list[str]]
    metadata: dict[str, object]


def normalize_diagnosis_prefix(values: pd.Series) -> pd.Series:
    normalized = values.fillna('').str.strip().str.upper().str.replace('.', '', regex=False)
    result = normalized.str.extract(r'^([A-Z][0-9]{2})', expand=False)
    return result.fillna('INVALID').where(normalized.ne(''), 'MISSING')


KCD_RANGES = [
    ('I','A00','B99'), ('II','C00','D48'), ('III','D50','D89'),
    ('IV','E00','E90'), ('V','F00','F99'), ('VI','G00','G99'),
    ('VII','H00','H59'), ('VIII','H60','H95'), ('IX','I00','I99'),
    ('X','J00','J99'), ('XI','K00','K93'), ('XII','L00','L99'),
    ('XIII','M00','M99'), ('XIV','N00','N99'), ('XV','O00','O99'),
    ('XVI','P00','P96'), ('XVII','Q00','Q99'), ('XVIII','R00','R99'),
    ('XIX','S00','T98'), ('XX','V01','Y98'), ('XXI','Z00','Z99'),
    ('XXII','U00','U99'),
]


def map_kcd_chapter(prefixes: pd.Series) -> pd.Series:
    result=pd.Series('INVALID',index=prefixes.index,dtype='string')
    result.loc[prefixes.eq('MISSING')]='MISSING'
    for chapter,lower,upper in KCD_RANGES:
        result.loc[prefixes.between(lower,upper)]=chapter
    return result


def prepare_record_patient_categories(records: pd.DataFrame, composition_mode: str = 'legacy') -> pd.DataFrame:
    normalized = pd.DataFrame(index=records.index)
    digits = records['의약품사용일시'].str.replace(r'\D', '', regex=True).str[:8]
    normalized['date'] = pd.to_datetime(digits, format='%Y%m%d', errors='raise')
    normalized['patient'] = records['병록번호']
    if normalized['patient'].fillna('').str.strip().eq('').any():
        raise ValueError('Patient identifier missing; cannot silently count rows as patients.')
    normalized['main_dx'] = normalize_diagnosis_prefix(records['주상병코드'])
    normalized['sub_dx'] = normalize_diagnosis_prefix(records['부상병코드'])
    normalized['sex'] = records['성별코드'].where(records['성별코드'].isin(['M', 'F']), 'OTHER')
    normalized['encounter'] = records['외래입원여부'].map({'외래': 'OUT', '입원': 'IN'}).fillna('OTHER')
    age = pd.to_numeric(records['나이'], errors='coerce')
    normalized['age'] = pd.cut(age.where(age.between(0, 120)), [-1, 17, 39, 64, 79, 120], labels=['0_17', '18_39', '40_64', '65_79', '80_PLUS']).astype('string').fillna('MISSING_INVALID')
    if composition_mode == 'standard':
        normalized['age'] = pd.cut(age.where(age.between(0,120)), [-1]+list(range(9,130,10)), labels=[f'{start}_{start+9}' for start in range(0,130,10)]).astype('string').fillna('MISSING_INVALID')
        normalized['main_kcd'] = map_kcd_chapter(normalized['main_dx'])
        normalized['sub_kcd'] = map_kcd_chapter(normalized['sub_dx'])
    elif composition_mode != 'legacy':
        raise ValueError(composition_mode)
    return normalized


def count_distinct_record_patients(categories: pd.DataFrame, column: str) -> pd.DataFrame:
    return categories[['date', 'patient', column]].drop_duplicates().groupby(['date', column]).size().rename('count').reset_index().rename(columns={column: 'category'}).assign(dimension=column)


def aggregate_and_write_patient_composition(original: Path, output: Path, top_main_diagnoses: int = 32, composition_mode: str = 'legacy') -> PatientCompositionDataset:
    output.mkdir(parents=True, exist_ok=False)
    daily_parts = []
    audit_rows = []
    sources = []
    all_missing_patient_days = 0
    total_patient_days = 0
    for path in sorted(original.glob('20*.parquet')):
        patient_parts = []
        counts = Counter()
        record_count = 0
        for batch in pq.ParquetFile(path).iter_batches(batch_size=65536, columns=SOURCE_COLUMNS):
            records = batch.to_pandas()
            record_count += len(records)
            for column in SOURCE_COLUMNS:
                counts[column] += int(records[column].fillna('').str.strip().eq('').sum())
            patient_parts.append(prepare_record_patient_categories(records, composition_mode).drop_duplicates())
        patients = pd.concat(patient_parts, ignore_index=True).drop_duplicates()
        year = int(path.name[:4])
        audit_rows.extend({'source': path.name, 'year': year, 'column': column, 'record_rows': record_count, 'blank_rows': counts[column], 'blank_percent': 100 * counts[column] / record_count} for column in SOURCE_COLUMNS)
        patient_days = patients[['date', 'patient']].drop_duplicates()
        total_patient_days += len(patient_days)
        valid_main = patients[~patients['main_dx'].isin(['MISSING', 'INVALID'])][['date', 'patient']].drop_duplicates()
        all_missing_patient_days += len(patient_days) - len(valid_main)
        daily_parts.append(patient_days.groupby('date').size().rename('count').reset_index().assign(category='ALL', dimension='total'))
        for column in ['sex', 'encounter', 'age', 'main_dx', 'sub_dx'] + (['main_kcd','sub_kcd'] if composition_mode == 'standard' else []):
            daily_parts.append(count_distinct_record_patients(patients, column))
        for column in ['main_dx', 'sub_dx']:
            chapter = patients[['date', 'patient', column]].copy()
            chapter[column] = chapter[column].where(chapter[column].isin(['MISSING', 'INVALID']), chapter[column].str[:1])
            daily_parts.append(count_distinct_record_patients(chapter, column).assign(dimension=column + '_letter'))
        sources.append({'path': str(path), 'rows': record_count, 'sha256': calculate_file_sha256(path)})
        print(json.dumps({'phase': 'patient_aggregation', 'source': path.name, 'rows': record_count, 'patient_days': len(patient_days)}, ensure_ascii=False), flush=True)
    long_counts = pd.concat(daily_parts, ignore_index=True).groupby(['date', 'dimension', 'category'], as_index=False)['count'].sum()
    initial = long_counts[long_counts['date'] < '2023-01-01']
    vocabulary = {dimension: sorted(frame['category'].unique()) for dimension, frame in initial.groupby('dimension')}
    main_ranking = initial[(initial.dimension == 'main_dx') & ~initial.category.isin(['MISSING', 'INVALID'])].groupby('category')['count'].sum().sort_values(ascending=False, kind='stable')
    top_codes = sorted(main_ranking.head(top_main_diagnoses).index)
    calendar = pd.date_range('2020-01-01', '2024-12-31')
    observed_dates = long_counts['date'].unique()
    if not calendar.isin(observed_dates).all():
        raise ValueError('No hospital-use records on some dates; patient composition cannot be automatically zero-filled there.')
    feature_arrays = {}
    blocks = {'total_sex': [], 'demographics': [], 'composition': []}
    dimensions = ['total','sex','encounter','age','main_kcd','sub_kcd'] if composition_mode == 'standard' else ['total', 'sex', 'encounter', 'age', 'main_dx', 'main_dx_letter', 'sub_dx_letter']
    for dimension in dimensions:
        frame = long_counts[long_counts.dimension == dimension].copy()
        selected_categories = top_codes + ['MISSING', 'INVALID'] if dimension == 'main_dx' else vocabulary[dimension]
        frame['category'] = frame['category'].where(frame.category.isin(selected_categories), 'OTHER_UNSEEN' if dimension != 'main_dx' else 'OTHER_CODE')
        categories = sorted(set(selected_categories + (['OTHER_CODE'] if dimension == 'main_dx' else ['OTHER_UNSEEN'])))
        for category in categories:
            values = frame[frame.category == category].groupby('date')['count'].sum().reindex(calendar, fill_value=0).astype(float)
            feature = f'{dimension}__{category}'
            feature_arrays[feature] = values.to_numpy()
            blocks['composition'].append(feature)
            if dimension in ['total', 'sex', 'encounter', 'age']:
                blocks['demographics'].append(feature)
            if dimension in ['total', 'sex']:
                blocks['total_sex'].append(feature)
    daily = pd.DataFrame(feature_arrays, index=calendar).rename_axis('date')
    # Every ordinary category is a distinct-patient count. Collapsed OTHER main codes need a union, not a sum.
    # Recompute that union separately from retained patient identifiers would require a second pass.
    # Therefore omit the summed OTHER feature and preserve full code counts separately for audit.
    if composition_mode == 'legacy':
        daily = daily.drop(columns=['main_dx__OTHER_CODE'])
        blocks['composition'].remove('main_dx__OTHER_CODE')
    daily.to_parquet(output / 'daily_patient_composition.parquet')
    long_counts.to_parquet(output / 'all_daily_diagnosis_counts.parquet', index=False)
    audit = pd.DataFrame(audit_rows)
    audit.to_parquet(output / 'column_missingness_by_file.parquet', index=False)
    yearly = audit.groupby(['year', 'column'], as_index=False)[['record_rows', 'blank_rows']].sum()
    yearly['blank_percent'] = 100 * yearly.blank_rows / yearly.record_rows
    yearly.to_parquet(output / 'column_missingness_by_year.parquet', index=False)
    overall = audit.groupby('column')[['record_rows', 'blank_rows']].sum()
    overall['blank_percent'] = 100 * overall.blank_rows / overall.record_rows
    overall.to_parquet(output / 'column_missingness_overall.parquet')
    metadata = {'status': 'completed', 'record_rows': sum(source['rows'] for source in sources), 'total_patient_days': total_patient_days, 'patient_days_without_valid_main_diagnosis': all_missing_patient_days, 'patient_days_without_valid_main_diagnosis_percent': 100 * all_missing_patient_days / total_patient_days, 'initial_vocabulary_end': '2022-12-31', 'top_main_codes': top_codes, 'initial_main_code_count': len(vocabulary['main_dx']), 'feature_blocks': blocks, 'feature_count': len(daily.columns), 'composition_mode': composition_mode, 'age_definition': 'ten-year intervals 0-9 through 120-129, valid ages <=120' if composition_mode == 'standard' else 'legacy broad intervals', 'KCD_mapping': KCD_RANGES if composition_mode == 'standard' else None, 'sources': sources, 'missingness': overall.reset_index().to_dict('records'), 'original_values_changed': False, 'imputation_applied': False, 'category_zero_meaning': 'zero record-bearing patients in this known category within export; not a physical drug-consumption zero', 'limits': ['not confirmed hospital-wide attenders', 'diagnosis/encounter/sex/age categories can overlap within patient-day', 'first ICD letter is coding group, not a complete official clinical chapter', 'top32 ICD3 selected from initial patient-days only; all ICD3 counts retained', 'new coding groups kept in OTHER_UNSEEN; summed OTHER ICD3 feature omitted to avoid double counting', 'no patient identifiers persisted', 'event-date availability assumed, original release timestamps absent']}
    (output / 'manifest.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2))
    return PatientCompositionDataset(daily, blocks, metadata)


def write_static_standard_patient_features(aggregate_directory: Path, output: Path) -> None:
    output.mkdir(parents=True,exist_ok=False)
    counts=pd.read_parquet(aggregate_directory/'all_daily_diagnosis_counts.parquet')
    source=json.loads((aggregate_directory/'manifest.json').read_text())
    calendar=pd.date_range('2020-01-01','2024-12-31')
    categories={'total':['ALL'],'sex':['M','F','OTHER'],'encounter':['IN','OUT','OTHER'],'age':[f'{start}_{start+9}' for start in range(0,130,10)]+['MISSING_INVALID'],'main_kcd':[chapter for chapter,_,_ in KCD_RANGES]+['MISSING','INVALID'],'sub_kcd':[chapter for chapter,_,_ in KCD_RANGES]+['MISSING','INVALID']}
    arrays={}
    blocks={'total_sex':[],'demographics':[],'composition':[]}
    for dimension,names in categories.items():
        for name in names:
            feature=f'{dimension}__{name}'
            subset=counts[(counts.dimension==dimension)&(counts.category==name)].set_index('date')['count']
            arrays[feature]=subset.reindex(calendar,fill_value=0).to_numpy(dtype=float)
            blocks['composition'].append(feature)
            if dimension in ['total','sex','encounter','age']:
                blocks['demographics'].append(feature)
            if dimension in ['total','sex']:
                blocks['total_sex'].append(feature)
    daily=pd.DataFrame(arrays,index=calendar).rename_axis('date')
    daily.to_parquet(output/'daily_patient_composition.parquet')
    for name in ['all_daily_diagnosis_counts.parquet','column_missingness_by_file.parquet','column_missingness_by_year.parquet','column_missingness_overall.parquet']:
        pd.read_parquet(aggregate_directory/name).to_parquet(output/name)
    source.update({'feature_blocks':blocks,'feature_count':len(daily.columns),'static_categories':categories,'top_main_codes_used_as_features':False,'source_aggregate_manifest':str(aggregate_directory/'manifest.json'),'source_aggregate_manifest_sha256':calculate_file_sha256(aggregate_directory/'manifest.json'),'limits':['record-bearing patients, not confirmed hospital-wide attenders','multiple diagnoses/encounters can overlap within patient-day','event-date one-day availability scenario, ingestion timestamps unverified','official KCD chapter boundaries, ten-year age groups and explicit missing/invalid buckets','no raw patient identifiers retained','hospital switching/clinical/purchasing policy excluded']})
    (output/'manifest.json').write_text(json.dumps(source,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--original', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--composition-mode', choices=['legacy','standard'], default='legacy')
    args = parser.parse_args()
    aggregate_and_write_patient_composition(args.original, args.output, composition_mode=args.composition_mode)
