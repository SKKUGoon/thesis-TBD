from __future__ import annotations

import copy
import hashlib
import json
import random
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import torch

from lavar import LAVARConfig, LAVARForecaster
from lavar.evaluation import compute_fold_metrics
from paper_code.config import load_best_model_spec
from paper_code.paths import PackagePaths

BASE_CONFIG = {
    "horizon": 14,
    "dyn_p": 7,
    "latent_dim": 8,
    "encoder_hidden": [32, 16],
    "decoder_hidden": [16, 32],
    "supply_hidden": [16],
    "dense_nonzero_rate_thr": 0.70,
    "ultra_nonzero_rate_thr": 0.005,
    "batch_size": 256,
    "train_days": 365 * 2,
    "stage2_mode": "baseline",
    "stage1_use_supply_history": False,
    "use_supply_history": False,
    "stage2_use_explicit_lag_coeff": False,
    "early_stop_patience_lavar": 20,
    "early_stop_patience_supply": 20,
    "zero_gate_k": 7,
    "forecast_round_to_int": True,
}
SCENARIO_CONFIG = {
    "S04_GRU_H32_L1": {
        "stage2_head_type": "gru",
        "gru_hidden_dim": 32,
        "gru_num_layers": 1,
        "epochs_lavar": 200,
        "epochs_supply": 200,
    },
    "S11_LATENT16": {
        "stage2_head_type": "gru",
        "gru_hidden_dim": 32,
        "gru_num_layers": 1,
        "latent_dim": 16,
        "encoder_hidden": [64, 32],
        "decoder_hidden": [32, 64],
        "epochs_lavar": 200,
        "epochs_supply": 300,
    },
}
STAGE1_SIGNATURE_KEYS = [
    "dyn_p",
    "latent_dim",
    "encoder_hidden",
    "decoder_hidden",
    "latent_dynamics_type",
    "dynamics_gru_hidden_dim",
    "encoder_dropout",
    "lr_lavar",
    "epochs_lavar",
    "batch_size",
    "horizon",
    "train_days",
    "stage1_use_supply_history",
]
QUALITY_TRIGGER_GUARDRAIL_COUNT = 3
QUALITY_TRIGGER_SKILL_THRESHOLD = -0.5


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    torch.use_deterministic_algorithms(True, warn_only=True)


def build_config(scenario: str, device: str) -> LAVARConfig:
    payload = copy.deepcopy(BASE_CONFIG)
    payload.update(SCENARIO_CONFIG[scenario])
    payload["device"] = device
    return LAVARConfig(**payload)


def stage1_signature(
    cfg: LAVARConfig,
    hospital: str,
    t_end: int,
    x_columns: list[str],
    seed: int,
) -> str:
    payload = {key: getattr(cfg, key) for key in STAGE1_SIGNATURE_KEYS}
    payload.update(
        hospital=hospital,
        t_end=int(t_end),
        x_columns=list(x_columns),
        seed=int(seed),
    )
    encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha1(encoded).hexdigest()[:20]


def save_stage2_checkpoint(
    forecaster: LAVARForecaster,
    stage2_dir: Path,
    run_id: str,
    fold_id: int,
    t_end: int,
    signature: str,
) -> Path:
    run_dir = stage2_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / f"fold_{fold_id:03d}_end_{t_end}.pt"
    stage2_state = forecaster.stage2_state_dict()
    torch.save(
        {
            "created_at": utc_now(),
            "run_id": run_id,
            "fold_id": int(fold_id),
            "t_end": int(t_end),
            "stage1_signature": signature,
            **stage2_state,
            "cfg": forecaster.cfg.model_dump(),
        },
        path,
    )
    return path


def get_or_train_stage1(
    cfg: LAVARConfig,
    hospital: str,
    stage1_dir: Path,
    x_train: torch.Tensor,
    y_train: torch.Tensor,
    t_end: int,
    x_columns: list[str],
    seed: int,
) -> tuple[LAVARForecaster, str, bool]:
    signature = stage1_signature(cfg, hospital, t_end, x_columns, seed)
    state_path = stage1_dir / f"{signature}.pth"
    metadata_path = stage1_dir / f"{signature}.json"
    if state_path.is_file():
        forecaster = LAVARForecaster(cfg)
        forecaster.restore_stage1_state(
            x_train,
            y_train,
            torch.load(state_path, map_location="cpu", weights_only=True),
        )
        return forecaster, signature, True

    forecaster = LAVARForecaster(cfg)
    forecaster.fit_stage1_shared(x_train, y=y_train)
    if forecaster._lavar is None:
        raise RuntimeError("Stage 1 training did not initialize the LAVAR model.")
    torch.save(forecaster._lavar.state_dict(), state_path)
    metadata_path.write_text(
        json.dumps(
            {
                "created_at": utc_now(),
                "stage1_signature": signature,
                "seed": int(seed),
                "t_end": int(t_end),
                "cfg": cfg.model_dump(),
                "x_dim": int(x_train.shape[1]),
                "y_dim": int(y_train.shape[1]),
                "x_columns": list(x_columns),
            },
            ensure_ascii=True,
            indent=2,
        ),
        encoding="utf-8",
    )
    return forecaster, signature, False


def summarize_folds(rows: list[dict]) -> dict:
    frame = pd.DataFrame(rows)
    valid_skill = frame["skill_vs_naive"].dropna()
    return {
        "mean_mse": float(frame["mse"].mean()),
        "median_mse": float(frame["mse"].median()),
        "mean_mae": float(frame["mae"].mean()),
        "median_mae": float(frame["mae"].median()),
        "mean_skill": float(valid_skill.mean()) if len(valid_skill) else np.nan,
        "median_skill": float(valid_skill.median()) if len(valid_skill) else np.nan,
        "n_folds": int(len(frame)),
    }


def train_run(
    hospital: str,
    run_id: str,
    cfg: LAVARConfig,
    x_tensor: torch.Tensor,
    y_tensor: torch.Tensor,
    x_columns: list[str],
    seed: int,
    stage1_dir: Path,
    stage2_dir: Path,
    run_metadata_dir: Path,
    retrain_cadence: int,
    fold_step: int,
    force: bool,
) -> tuple[dict, pd.DataFrame]:
    run_dir = run_metadata_dir / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    fold_path = run_dir / "fold_metrics.csv"
    summary_path = run_dir / "summary.json"
    if not force and summary_path.is_file() and fold_path.is_file():
        return json.loads(summary_path.read_text(encoding="utf-8")), pd.read_csv(fold_path)

    total = len(x_tensor)
    t_end = max(cfg.train_days, cfg.dyn_p + cfg.horizon + 1)
    if t_end + cfg.horizon > total:
        raise ValueError(f"Insufficient data for {run_id}: T={total}")
    rows: list[dict] = []
    fold_id = 0
    last_fit: int | None = None
    forecaster: LAVARForecaster | None = None
    consecutive_guardrails = 0

    while t_end + cfg.horizon <= total:
        full_refit = last_fit is None or t_end - last_fit >= retrain_cadence
        if not full_refit and rows and rows[-1]["skill_vs_naive"] < QUALITY_TRIGGER_SKILL_THRESHOLD:
            full_refit = True
        if not full_refit and consecutive_guardrails >= QUALITY_TRIGGER_GUARDRAIL_COUNT:
            full_refit = True
        signature: str | None = None
        stage1_reused = False
        if full_refit:
            x_train = x_tensor[:t_end]
            y_train = y_tensor[:t_end]
            forecaster, signature, stage1_reused = get_or_train_stage1(
                cfg,
                hospital,
                stage1_dir,
                x_train,
                y_train,
                t_end,
                x_columns,
                seed,
            )
            forecaster.fit_stage2_private(x_train, y_train)
            save_stage2_checkpoint(
                forecaster,
                stage2_dir,
                run_id,
                fold_id,
                t_end,
                signature,
            )
            last_fit = t_end
            consecutive_guardrails = 0
        if forecaster is None:
            raise RuntimeError("Forecaster was not initialized")

        y_true = y_tensor[t_end : t_end + cfg.horizon]
        prediction_result = forecaster.predict_with_metadata(
            x_tensor[t_end - cfg.dyn_p : t_end],
            y_recent=y_tensor[t_end - cfg.dyn_p : t_end],
        )
        prediction = torch.from_numpy(prediction_result.values).float()
        if prediction_result.guardrail_metadata["guardrail_triggered"]:
            consecutive_guardrails += 1
        else:
            consecutive_guardrails = 0
        metrics = compute_fold_metrics(prediction, y_true.float(), y_tensor[t_end - 1].float())
        rows.append(
            {
                "run_id": run_id,
                "fold_id": int(fold_id),
                "t_end": int(t_end),
                "mse": float(metrics["fold_mse"]),
                "rmse": float(metrics["fold_rmse"]),
                "mae": float(metrics["fold_mae"]),
                "naive_mse": float(metrics["naive_mse"]),
                "skill_vs_naive": float(metrics["skill_vs_naive"]),
                "worst_h": int(metrics["worst_h"]),
                "worst_h_mse": float(metrics["worst_h_mse"]),
                "stage1_signature": signature,
                "stage1_reused": bool(stage1_reused),
                "full_refit": bool(full_refit),
                **prediction_result.guardrail_metadata,
            }
        )
        pd.DataFrame(rows).to_csv(fold_path, index=False)
        fold_id += 1
        t_end += fold_step

    summary = summarize_folds(rows)
    summary.update(
        run_id=run_id,
        created_at=utc_now(),
        device=cfg.device,
        horizon=int(cfg.horizon),
        fold_step=int(fold_step),
        lavar_retrain_cadence=int(retrain_cadence),
        quality_triggers=True,
        seed=int(seed),
    )
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary, pd.DataFrame(rows)


def train_best_components(
    hospital: str,
    root: Path | None = None,
    device: str = "cpu",
    force: bool = False,
) -> pd.DataFrame:
    """Train or resume the ten component runs used by the selected ensemble."""
    paths = PackagePaths.from_root(root)
    spec = load_best_model_spec(hospital, paths.root)
    stage1_dir = paths.stage1(hospital)
    stage2_dir = paths.stage2(hospital)
    metadata_dir = paths.run_metadata(hospital)
    for directory in (stage1_dir, stage2_dir, metadata_dir):
        directory.mkdir(parents=True, exist_ok=True)

    x_frame = pd.read_parquet(paths.processed(hospital) / "lavar_ready_x.parquet")
    y_frame = pd.read_parquet(paths.processed(hospital) / "lavar_ready_y.parquet")
    full_index = pd.date_range(
        min(x_frame.index.min(), y_frame.index.min()),
        max(x_frame.index.max(), y_frame.index.max()),
        freq="D",
    )
    x_frame = x_frame.reindex(full_index).fillna(0)
    y_frame = y_frame.reindex(full_index).fillna(0)
    x_tensor = torch.tensor(x_frame.values, dtype=torch.float32)
    y_tensor = torch.tensor(y_frame.values, dtype=torch.float32)

    summaries = []
    fold_frames = []
    for scenario in ("S04_GRU_H32_L1", "S11_LATENT16"):
        for seed in spec.seeds:
            set_seed(seed)
            run_id = f"RUN_{scenario}_SEED{seed}"
            summary, folds = train_run(
                hospital,
                run_id,
                build_config(scenario, device),
                x_tensor,
                y_tensor,
                list(x_frame.columns),
                seed,
                stage1_dir,
                stage2_dir,
                metadata_dir,
                spec.retrain_cadence,
                spec.fold_step,
                force,
            )
            summary.update(scenario=scenario, seed=int(seed))
            summaries.append(summary)
            folds = folds.assign(scenario=scenario, seed=int(seed))
            fold_frames.append(folds)
            pd.DataFrame(summaries).to_csv(metadata_dir / "progress_summary.csv", index=False)
            pd.concat(fold_frames, ignore_index=True).to_csv(
                metadata_dir / "progress_fold_metrics.csv",
                index=False,
            )
    return pd.DataFrame(summaries)
