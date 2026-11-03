"""Validate portable aggregate data and immutable prerecorded forecast results."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from typing import Any


def load_seed(path: Path) -> dict[str, Any]:
    """Read and validate every channel, horizon, quantity and aggregate date."""
    seed = json.loads(path.read_text())
    if seed['version'] != 1 or len(seed['items']) != 30:
        raise ValueError('SEED_CONTRACT_MISMATCH')
    codes = [i['channel'] for i in seed['items']]
    if len(set(codes)) != 30 or sum(i['cohort'] == 'drug' for i in seed['items']) != 20:
        raise ValueError('SEED_CHANNEL_MISMATCH')
    if not seed['forecasts']:
        raise ValueError('SEED_FORECASTS_EMPTY')
    for origin, values in seed['forecasts'].items():
        start = date.fromisoformat(origin)
        expected = {(code, str(start + timedelta(days=h))) for code in codes for h in range(14)}
        actual = {(v['channel'], v['target_day']) for v in values}
        if start.weekday() != 0 or len(values) != 420 or actual != expected:
            raise ValueError('SEED_HORIZON_MISMATCH')
        for v in values:
            quantity = Decimal(v['prediction'])
            if not quantity.is_finite() or quantity < 0:
                raise ValueError('SEED_QUANTITY_INVALID')
        for offset in range(-28, 0):
            record = seed['days'][str(start + timedelta(days=offset))]
            if len(record['usage']) != 30 or set(record['patients']) != {'total', 'inpatient', 'outpatient'}:
                raise ValueError('SEED_AGGREGATES_INVALID')
            for value in [*record['usage'], *record['patients'].values()]:
                if value is not None and (not Decimal(str(value)).is_finite() or Decimal(str(value)) < 0):
                    raise ValueError('SEED_AGGREGATES_INVALID')
    seed['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    return seed
