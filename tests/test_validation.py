from __future__ import annotations

import json
from pathlib import Path

import pytest
import torch

from lavar._data.dataset import RollingXYDataset
from lavar._data.scaler import StandardScalerTorch
from lavar.config import LAVARConfig
from lavar.evaluation import compute_fold_metrics
from paper_code.config import load_best_model_spec
from paper_code.preprocessing.seosong import _kcd_labels


def test_scaler_requires_fit_before_transform() -> None:
    with pytest.raises(RuntimeError, match="must be fitted"):
        StandardScalerTorch().transform(torch.ones(2, 1))


def test_scaler_rejects_nonfinite_input() -> None:
    with pytest.raises(ValueError, match="non-finite"):
        StandardScalerTorch().fit(torch.tensor([[1.0], [float("nan")]]))


def test_rolling_dataset_rejects_mismatched_lengths() -> None:
    with pytest.raises(ValueError, match="lengths must match"):
        RollingXYDataset(torch.ones(5, 1), torch.ones(4, 1), p=1, horizon=1)


def test_config_rejects_inverted_density_thresholds() -> None:
    with pytest.raises(ValueError, match="Density thresholds"):
        LAVARConfig(dense_nonzero_rate_thr=0.2, ultra_nonzero_rate_thr=0.3)


def test_fold_metrics_require_matching_shapes() -> None:
    with pytest.raises(ValueError, match="identical shapes"):
        compute_fold_metrics(torch.ones(2, 1), torch.ones(1, 1), torch.ones(1))


def test_missing_kcd_reference_is_not_silently_replaced(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="KCD classification table"):
        _kcd_labels(tmp_path / "missing.parquet")


def test_model_spec_must_match_requested_hospital(tmp_path: Path) -> None:
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    payload = {
        "hospital": "seosong",
        "scenarios": ["S11_LATENT16", "S04_GRU_H32_L1"],
        "seeds": [42],
        "ensemble_weight_s11": 0.55,
        "horizon": 14,
        "fold_step": 14,
        "retrain_cadence": 90,
        "expected_mean_mse": 1.0,
    }
    (config_dir / "epurun_best_model.json").write_text(json.dumps(payload))

    with pytest.raises(ValueError, match="Hospital mismatch"):
        load_best_model_spec("epurun", tmp_path)
