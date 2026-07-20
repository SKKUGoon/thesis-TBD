from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .paths import PackagePaths

SUPPORTED_HOSPITALS = frozenset({"epurun", "seosong"})
REQUIRED_SPEC_KEYS = frozenset(
    {
        "ensemble_weight_s11",
        "expected_mean_mse",
        "fold_step",
        "horizon",
        "hospital",
        "retrain_cadence",
        "scenarios",
        "seeds",
    }
)


@dataclass(frozen=True)
class BestModelSpec:
    hospital: str
    scenarios: tuple[str, ...]
    seeds: tuple[int, ...]
    ensemble_weight_s11: float
    horizon: int
    fold_step: int
    retrain_cadence: int
    expected_mean_mse: float

    @property
    def ensemble_weight_s04(self) -> float:
        return 1.0 - self.ensemble_weight_s11


def load_best_model_spec(hospital: str, root: Path | None = None) -> BestModelSpec:
    if hospital not in SUPPORTED_HOSPITALS:
        raise ValueError(
            f"Unsupported hospital {hospital!r}; expected one of {sorted(SUPPORTED_HOSPITALS)}."
        )
    paths = PackagePaths.from_root(root)
    config_path = paths.root / "configs" / f"{hospital}_best_model.json"
    if not config_path.is_file():
        raise FileNotFoundError(f"Missing model specification: {config_path}")
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    missing_keys = sorted(REQUIRED_SPEC_KEYS - set(payload))
    if missing_keys:
        raise ValueError(f"Missing model specification keys in {config_path}: {missing_keys}")
    if payload["hospital"] != hospital:
        raise ValueError(
            f"Hospital mismatch in {config_path}: {payload['hospital']!r} != {hospital!r}"
        )
    spec = BestModelSpec(
        hospital=payload["hospital"],
        scenarios=tuple(payload["scenarios"]),
        seeds=tuple(int(seed) for seed in payload["seeds"]),
        ensemble_weight_s11=float(payload["ensemble_weight_s11"]),
        horizon=int(payload["horizon"]),
        fold_step=int(payload["fold_step"]),
        retrain_cadence=int(payload["retrain_cadence"]),
        expected_mean_mse=float(payload["expected_mean_mse"]),
    )
    if not spec.scenarios:
        raise ValueError(f"Model specification has no scenarios: {config_path}")
    if not spec.seeds or len(set(spec.seeds)) != len(spec.seeds):
        raise ValueError(f"Model seeds must be non-empty and unique: {config_path}")
    if not 0.0 <= spec.ensemble_weight_s11 <= 1.0:
        raise ValueError(f"ensemble_weight_s11 must be in [0, 1]: {config_path}")
    for name, value in (
        ("fold_step", spec.fold_step),
        ("horizon", spec.horizon),
        ("retrain_cadence", spec.retrain_cadence),
    ):
        if value < 1:
            raise ValueError(f"{name} must be positive in {config_path}. Got {value}.")
    return spec
