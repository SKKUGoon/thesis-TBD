"""Create a private portable bundle from aggregate records and existing forecasts."""
from __future__ import annotations

import argparse
from datetime import date, timedelta
import hashlib
import json
import math
from pathlib import Path
import shutil
import sys
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.demo.seed import load_seed


def build_bundle(output: Path, records: Path, forecast_run: Path) -> Path:
    """Write a new bundle and archive; never overwrite source or prior output."""
    import pandas as pd

    if output.exists() or output.with_suffix('.tar.gz').exists():
        raise FileExistsError(output)
    subprocess.run(['npm', 'run', 'build'], cwd=ROOT / 'frontend', check=True, capture_output=True, text=True)
    source = json.loads(records.read_text())
    items = source['items']
    frames = [pd.read_parquet(forecast_run / f'{kind}_joint_evaluation.parquet') for kind in ('drug', 'material')]
    forecasts: dict[str, list[dict]] = {}
    lookup = {item['channel']: i for i, item in enumerate(items)}
    for frame in frames:
        for row in frame.to_dict('records'):
            origin = str(row['origin'].date())
            actual = source['days'][str(row['target_date'].date())]['usage'][lookup[row['channel']]]
            if bool(row['observed']) != (actual is not None) or (actual is not None and float(actual) != float(row['actual'])):
                raise ValueError('FORECAST_RECORDS_TARGET_MISMATCH')
            if not math.isfinite(float(row['prediction_raw'])):
                raise ValueError('FORECAST_RAW_NOT_FINITE')
            forecasts.setdefault(origin, []).append({'channel': row['channel'], 'target_day': str(row['target_date'].date()), 'prediction': str(row['prediction']), 'prediction_raw': str(row['prediction_raw'])})
    for values in forecasts.values():
        values.sort(key=lambda v: (v['channel'], v['target_day']))
    lower = str(date.fromisoformat(min(forecasts)) - timedelta(days=28))
    upper = max(forecasts)
    keys = source['patient_keys']
    indices = {name: keys.index(key) for name, key in [('total', 'total__ALL'), ('inpatient', 'encounter__IN'), ('outpatient', 'encounter__OUT')]}
    days = {day: {'usage': record['usage'], 'patients': {name: record['patients'][i] for name, i in indices.items()}} for day, record in source['days'].items() if lower <= day < upper}
    inputs = [records, *(forecast_run / f'{kind}_joint_evaluation.parquet' for kind in ('drug', 'material'))]
    seed = {'version': 1, 'dataset_id': 'cheonan-prerecorded-2024-v1', 'items': items, 'days': days, 'forecasts': forecasts,
            'provenance': {'mode': 'prerecorded_replay', 'run_id': forecast_run.name, 'sources': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}, 'inventory_source': 'not_registered'}}
    output.mkdir(parents=True, mode=0o700)
    private = output / 'private'
    private.mkdir(mode=0o700)
    seed_path = private / 'seed.json'
    seed_path.write_text(json.dumps(seed, ensure_ascii=False, separators=(',', ':'), allow_nan=False))
    load_seed(seed_path)
    shutil.copytree(ROOT / 'src/demo', output / 'src/demo', ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copytree(ROOT / 'frontend/dist', output / 'frontend/dist')
    (output / 'deploy').mkdir()
    shutil.copyfile(ROOT / 'deploy/web.conf', output / 'deploy/web.conf')
    (output / 'deploy/Dockerfile.web').write_text('FROM nginxinc/nginx-unprivileged:1.28-alpine@sha256:7377697a821c131a924a7105fafbe7414db4e9fcc77a6f08f776f33f141ec3f8\nCOPY deploy/web.conf /etc/nginx/conf.d/default.conf\nCOPY frontend/dist /usr/share/nginx/html\nENTRYPOINT ["nginx", "-g", "daemon off;"]\n')
    for name in ['Dockerfile', 'requirements.txt', 'config.json', 'compose.yaml', 'prepare.py', 'README.md']:
        shutil.copyfile(ROOT / 'deploy/demo' / name, output / name)
    (output / '.dockerignore').write_text('**\n!Dockerfile\n!requirements.txt\n!src/\n!src/demo/\n!src/demo/**\n!frontend/\n!frontend/dist/\n!frontend/dist/**\n!deploy/\n!deploy/web.conf\n!deploy/Dockerfile.web\n')
    manifest = {'seed_sha256': seed['sha256'] if 'sha256' in seed else hashlib.sha256(seed_path.read_bytes()).hexdigest(), 'origins': sorted(forecasts), 'items': len(items), 'values': sum(map(len, forecasts.values())), 'files': {str(p.relative_to(output)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(output.rglob('*')) if p.is_file()}}
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    archive = output.with_suffix('.tar.gz')
    with tarfile.open(archive, 'w:gz') as tar:
        tar.add(output, arcname=output.name)
    archive.chmod(0o600)
    return archive


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--records', type=Path, default=ROOT / 'deploy/.state/records/cheonan-records-v1.json')
    parser.add_argument('--forecast-run', type=Path, default=ROOT / 'runs/cheonan/timesfm_vector_20261008_01')
    args = parser.parse_args()
    print(build_bundle(args.output.resolve(), args.records, args.forecast_run))
