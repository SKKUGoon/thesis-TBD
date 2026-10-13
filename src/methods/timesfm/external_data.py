"""Aggregate private external-hospital records without retaining patient identifiers."""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path

import numpy as np
import pandas as pd
from python_calamine import CalamineWorkbook

from src.methods.timesfm.data import file_sha256
from src.methods.xgboost.patient_data import KCD_RANGES, map_kcd_chapter, normalize_diagnosis_prefix


def clean_text(values: pd.Series) -> pd.Series:
    """Normalize spreadsheet numeric identifiers and padding without row removal."""
    return values.astype('string').fillna('').str.strip().str.replace(r'\.0$', '', regex=True)


def normalize_external_records(frame: pd.DataFrame, hospital: str) -> pd.DataFrame:
    """Build transient categories; identifiers are used only for patient-day counts."""
    out = pd.DataFrame(index=frame.index)
    if hospital == 'epurun':
        dates = clean_text(frame['date'])
        out['date'] = pd.to_datetime(dates, format='%Y%m%d', errors='coerce')
        out['patient'] = clean_text(frame['차트번호'])
        out['code'] = clean_text(frame['청구코드'])
        out['name'] = clean_text(frame['한글명칭'])
        out['sex'] = clean_text(frame['male']).map({'1':'M','0':'F'}).fillna('OTHER')
        age = pd.to_numeric(frame['age'], errors='coerce')
        main, sub = frame['kcd1'], frame['kcd2']
        # Insurance type is not inpatient/outpatient encounter type.
        out['encounter'] = 'OTHER'
    else:
        out['date'] = pd.to_datetime(clean_text(frame['ManComDte']), format='mixed', errors='coerce')
        out['patient'] = clean_text(frame['DatChtNum'])
        out['code'] = clean_text(frame['DatEdiCod'])
        out['name'] = clean_text(frame['DatOdrNam'])
        resident = clean_text(frame['ManResNum'])
        sex_digit = resident.str.extract(r'^\d{6}-?(\d)', expand=False)
        out['sex'] = sex_digit.map({**dict.fromkeys('1357','M'), **dict.fromkeys('2468','F')}).fillna('OTHER')
        yy = pd.to_numeric(resident.str.extract(r'^(\d{2})', expand=False), errors='coerce')
        century = sex_digit.map({'1':1900,'2':1900,'5':1900,'6':1900,'3':2000,'4':2000,'7':2000,'8':2000})
        age = out.date.dt.year - century - yy
        primary = clean_text(frame['ByuMajYon']).eq('Y')
        main = frame['ByuDisCod'].where(primary, '')
        sub = frame['ByuDisCod'].where(~primary, '')
        out['encounter'] = 'OUT'
    out['age'] = pd.cut(age.where(age.between(0,120)), [-1]+list(range(9,130,10)), labels=[f'{s}_{s+9}' for s in range(0,130,10)]).astype('string').fillna('MISSING_INVALID')
    out['main_kcd'] = map_kcd_chapter(normalize_diagnosis_prefix(main.astype('string')))
    out['sub_kcd'] = map_kcd_chapter(normalize_diagnosis_prefix(sub.astype('string')))
    return out


def build_patient_features(patients: pd.DataFrame, dates: pd.DatetimeIndex) -> pd.DataFrame:
    """Count distinct category-bearing patients; no-record dates remain missing."""
    observed_dates = patients.date.unique()
    counts = patients[['date','patient']].drop_duplicates().groupby('date').size()
    result = {'total__ALL': counts.reindex(dates).astype(float)}
    categories = {'sex':['F','M','OTHER'], 'encounter':['IN','OUT','OTHER'],
                  'age':[f'{s}_{s+9}' for s in range(0,130,10)]+['MISSING_INVALID'],
                  'main_kcd':[r[0] for r in KCD_RANGES]+['MISSING','INVALID'],
                  'sub_kcd':[r[0] for r in KCD_RANGES]+['MISSING','INVALID']}
    for dim, labels in categories.items():
        d = patients[['date','patient',dim]].drop_duplicates()
        for label in labels:
            subset = d[d[dim].eq(label)].groupby('date').size()
            values = subset.reindex(dates, fill_value=0).astype(float)
            values.loc[~dates.isin(observed_dates)] = np.nan
            result[f'{dim}__{label}'] = values
    daily = pd.DataFrame(result, index=dates).rename_axis('date')
    if daily.shape[1] != 69:
        raise ValueError('Unexpected patient feature schema')
    return daily


def aggregate_and_write_external_dataset(root: Path, hospital: str, dataset_id: str) -> Path:
    """Read raw records in bounded files/chunks and write new aggregate-only data."""
    output = root/'data/processed'/hospital/'analysis'/dataset_id
    output.mkdir(parents=True, exist_ok=False)
    start = '2021-01-01' if hospital == 'epurun' else '2022-01-01'
    dates = pd.date_range(start, '2024-12-31')
    reference = root/'data/raw/references/ATC_20250912_110516.csv'
    atc = pd.read_csv(reference, encoding='cp949', dtype=str, usecols=['제품코드'])
    drug_codes = set(clean_text(atc['제품코드']))
    daily_counts: Counter = Counter()
    prior_names: Counter = Counter()
    patient_parts = []
    audit = []
    sources = []

    def consume(frame: pd.DataFrame, label: str) -> None:
        records = normalize_external_records(frame, hospital)
        valid = records.date.between(start, '2024-12-31')
        audit.append({'source_part':label, 'rows':len(records), 'invalid_dates':int(records.date.isna().sum()), 'outside_analysis_dates':int((records.date.notna() & ~valid).sum())})
        records = records[valid].copy()
        patients = records[records.patient.ne('')][['date','patient','sex','encounter','age','main_kcd','sub_kcd']].drop_duplicates()
        patient_parts.append(patients)
        meds = records[records.code.isin(drug_codes)]
        daily_counts.update(meds.groupby(['date','code']).size().to_dict())
        prior = meds[meds.date.le('2022-12-31')]
        prior_names.update(prior.groupby(['code','name']).size().to_dict())

    if hospital == 'epurun':
        path = root/'data/raw/epurun/patient_epurun_20251009_011300.csv'
        columns = ['date','차트번호','청구코드','한글명칭','male','age','kcd1','kcd2']
        sources.append(path)
        for i, chunk in enumerate(pd.read_csv(path, dtype=str, usecols=columns, chunksize=200000)):
            consume(chunk, f'{path.name}:chunk_{i}')
            print(f'{hospital}: aggregated chunk {i+1}', flush=True)
    else:
        # Inpatient rows can be attached to earlier admission dates; never backfill them.
        paths = sorted(p for p in (root/'data/raw/seosong/outpatient').glob('*.xlsx') if any(str(y) in p.name for y in (2022,2023,2024)))
        sources.extend(paths)
        for path in paths:
            workbook = CalamineWorkbook.from_path(str(path))
            for sheet in workbook.sheet_names:
                values = workbook.get_sheet_by_name(sheet).to_python()
                header = [str(v).strip() for v in values[1]]
                data = pd.DataFrame(values[2:], columns=header)
                consume(data, f'{path.name}:{sheet}')
                del values, data
            print(f'{hospital}: aggregated {path.name}', flush=True)
    patients = pd.concat(patient_parts, ignore_index=True).drop_duplicates()
    patient_features = build_patient_features(patients, dates)
    ranking = Counter()
    for (date, code), count in daily_counts.items():
        if date <= pd.Timestamp('2022-12-31'):
            ranking[code] += count
    eligible = sorted(ranking, key=lambda c:(-ranking[c],c))
    selected = eligible[:20]
    if len(selected) != 20:
        raise ValueError('Fewer than 20 drug products supported by prior records')
    records = []
    targets = {}
    for rank, code in enumerate(selected,1):
        name_choices = [(n, count) for (c,n),count in prior_names.items() if c==code and n]
        if not name_choices:
            raise ValueError('Selected medicine has no prior name')
        name = sorted(name_choices, key=lambda x:(-x[1],x[0]))[0][0]
        channel = f'drug_{rank:02}'
        counts = {date:count for (date,c),count in daily_counts.items() if c==code}
        targets[channel] = pd.Series(counts, dtype=float).reindex(dates)
        records.append({'channel':channel,'name':name,'cohort':'drug','unit':'처방·청구 기록 행','source_key':code,'rank':rank,'selection_score':ranking[code]})
    targets_frame = pd.DataFrame(targets,index=dates).rename_axis('date')
    catalogue = pd.DataFrame(records)
    catalogue.to_parquet(output/'frozen_cohorts.parquet',index=False)
    targets_frame.to_parquet(output/'daily_targets.parquet')
    patient_features.to_parquet(output/'daily_patient_composition.parquet')
    pd.DataFrame(audit).to_parquet(output/'source_audit.parquet',index=False)
    pd.DataFrame([{'code':code,'prior_record_rows':ranking[code],'selected':code in selected} for code in eligible]).to_parquet(output/'candidate_ranking.parquet',index=False)
    manifest = {'hospital':hospital,'selection_start':start,'selection_end':'2022-12-31','evaluation_year':2024,
                'target':'Daily counts of every source prescription/claim row for a product; physical units not verified',
                'no_record':'NaN, never automatically zero','prescription_duplicates_removed':False,
                'scope':'all available dated claim records' if hospital=='epurun' else 'outpatient only; excludes inpatient/ward source with admission-date backfill risk',
                'classification':'2025 ATC product list used for retrospective drug identification only; original product code retained; not historically available metadata validation',
                'patient_features':patient_features.columns.tolist(),'missing_encounter_definition':hospital=='epurun',
                'seosong_main_flag_definition':'ByuMajYon Y treated as primary, other rows as secondary; source dictionary unverified',
                'availability':'record event date plus next-day availability scenario; actual ingestion time and row corrections unverified',
                'source_sha256':{str(p.relative_to(root)):file_sha256(p) for p in [*sources,reference]},
                'source_rows':int(sum(a['rows'] for a in audit)),'patient_days':len(patients[['date','patient']].drop_duplicates()),
                'observed_patient_dates':int(patient_features.total__ALL.notna().sum()),
                'dataset_sha256':{p.name:file_sha256(p) for p in output.glob('*.parquet')},'code_sha256':file_sha256(Path(__file__))}
    (output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    print(f'{hospital}: dataset ready, 20 channels, {manifest["source_rows"]} source rows',flush=True)
    return output


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--hospital', choices=['epurun','seosong'],required=True)
    parser.add_argument('--dataset-id', required=True)
    args = parser.parse_args()
    aggregate_and_write_external_dataset(Path.cwd(),args.hospital,args.dataset_id)
