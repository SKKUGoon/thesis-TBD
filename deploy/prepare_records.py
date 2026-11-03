"""Export only verified item metadata and daily aggregates into a private read-only replay dataset."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd


def export_records(root: Path, output: Path) -> None:
    """Write a new aggregate snapshot, preserving all source files and missing observations."""
    dataset = root / 'data/processed/cheonan/analysis/timesfm_cohorts_20261008_02'
    sources = [dataset/'frozen_cohorts.parquet', dataset/'daily_targets.parquet', root/'data/processed/cheonan/analysis/patient_standard_20261007_02/daily_patient_composition.parquet']
    cohort, targets, patients = (pd.read_parquet(p) for p in sources)
    recorded_manifest = json.loads((dataset/'manifest.json').read_text())
    hashes = {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    for p in sources[:2]:
        if hashes[str(p.relative_to(root))] != recorded_manifest['output_sha256'][p.name]:
            raise ValueError('RECORDED_DATA_HASH_MISMATCH')
    if cohort.channel.tolist()!=targets.columns.tolist() or len(cohort)!=30 or len(patients.columns)!=69:
        raise ValueError('RECORDS_SHAPE_MISMATCH')
    if not targets.index.is_unique or not patients.index.is_unique:
        raise ValueError('DUPLICATE_DAILY_AGGREGATE')
    days = targets.index.intersection(patients.index).sort_values()
    def clean(value: object) -> int | float | None:
        if pd.isna(value):return None
        numeric=float(value)
        if numeric<0:raise ValueError('NEGATIVE_RECORDED_TARGET')
        return int(numeric) if numeric.is_integer() else numeric
    body = {'dataset_id':'cheonan-records-v1','source':'recorded_daily_aggregates','input_sha256':hashes,
            'availability':'retrospective weekly closure assumption; actual publication timestamps unverified',
            'inventory_source':'not_registered','ordering_policy_source':'not_registered',
            'items':cohort[['channel','cohort','name','unit','source_key']].to_dict('records'),
            'patient_keys':patients.columns.tolist(),
            'days':{str(day.date()):{'usage':[clean(v) for v in targets.loc[day]],'patients':[clean(v) for v in patients.loc[day]]} for day in days}}
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x') as stream:json.dump(body,stream,ensure_ascii=False,allow_nan=False,separators=(',',':'))
    output.chmod(0o444)
    print(json.dumps({'items':len(cohort),'days':len(days),'patient_features':69,'missing_targets':int(targets.isna().sum().sum()),'output_sha256':hashlib.sha256(output.read_bytes()).hexdigest()},ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    export_records(Path(__file__).resolve().parents[1],args.output)
