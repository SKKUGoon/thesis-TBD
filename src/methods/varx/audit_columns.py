from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq


def audit_and_write_candidate_columns(original: Path, output: Path) -> None:
    columns = ['성별코드', '나이', '외래입원여부', '주상병코드', '부상병코드', '외래방문일자', '입원일자', '퇴원일자']
    rows = []
    for path in sorted(original.glob('202[012]*.parquet')):
        frame = next(pq.ParquetFile(path).iter_batches(batch_size=8192, columns=columns)).to_pandas()
        rows.append({'source': path.name, 'sample_rows': len(frame), 'nonblank_counts': {column: int(frame[column].fillna('').str.strip().ne('').sum()) for column in columns}, 'outpatient_inpatient_codes': frame['외래입원여부'].value_counts().to_dict(), 'numeric_age_fraction': float(pd.to_numeric(frame['나이'], errors='coerce').notna().mean())})
    result = {'sampling': 'first 8192 rows in each 2020–2022 usage Parquet; not random or full-population quality proof', 'patient_level_values_exported': False, 'files': rows, 'feature_meaning': 'Record-bearing patients are not independently verified hospital-wide attenders. Admission/discharge dates and diagnosis coding lack original availability timestamps; final discharge fields cannot be backfilled into earlier origins.'}
    with output.open('x') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--original', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    arguments = parser.parse_args()
    audit_and_write_candidate_columns(arguments.original, arguments.output)
