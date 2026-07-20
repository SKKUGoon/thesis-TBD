from __future__ import annotations

import re
from bisect import bisect_right
from pathlib import Path

import pandas as pd
import torch

from lavar.config import LAVARConfig
from lavar.forecaster import LAVARForecaster, Stage2ModelState

CHECKPOINT_RE = re.compile(r"fold_(\d+)_end_(\d+)\.pt$")


def list_stage2_checkpoints(stage2_dir: Path, run_id: str) -> pd.DataFrame:
    rows: list[dict] = []
    for path in sorted((stage2_dir / run_id).glob("fold_*_end_*.pt")):
        match = CHECKPOINT_RE.search(path.name)
        if match:
            rows.append(
                {
                    "run_id": run_id,
                    "fit_fold_id": int(match.group(1)),
                    "fit_t_end": int(match.group(2)),
                    "ckpt_path": path,
                }
            )
    if not rows:
        raise ValueError(f"No Stage 2 checkpoints found for {run_id} in {stage2_dir}")
    return pd.DataFrame(rows).sort_values("fit_t_end").reset_index(drop=True)


def pick_checkpoint_for_t_end(checkpoints: pd.DataFrame, t_end: int) -> pd.Series:
    fit_ends = checkpoints["fit_t_end"].tolist()
    index = bisect_right(fit_ends, int(t_end)) - 1
    if index < 0:
        raise ValueError(f"No checkpoint has fit_t_end <= {t_end}")
    return checkpoints.iloc[index]


def rebuild_forecaster(
    payload: dict,
    stage1_dir: Path,
    x_train: torch.Tensor,
    y_train: torch.Tensor,
    force_device: str = "cpu",
) -> LAVARForecaster:
    cfg_payload = dict(payload["cfg"])
    cfg_payload["device"] = force_device
    cfg = LAVARConfig(**cfg_payload)
    stage1_path = stage1_dir / f"{payload['stage1_signature']}.pth"
    if not stage1_path.is_file():
        raise FileNotFoundError(f"Missing Stage 1 checkpoint: {stage1_path}")
    state = torch.load(stage1_path, map_location="cpu", weights_only=True)
    forecaster = LAVARForecaster(cfg).restore_stage1_state(x_train, y_train, state)
    stage2_state: Stage2ModelState = {
        "dense_idx": payload["dense_idx"],
        "sparse_idx": payload["sparse_idx"],
        "ultra_idx": payload["ultra_idx"],
        "dense_state": payload["dense_state"],
        "sparse_state": payload["sparse_state"],
        "ultra_state": payload["ultra_state"],
    }
    return forecaster.restore_stage2_state(stage2_state, y_train)
