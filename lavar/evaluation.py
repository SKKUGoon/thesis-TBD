from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import torch
from torch.utils.data import DataLoader

from lavar._core.model import LAVAR
from lavar._data.dataset import RollingXYDataset, RollingXYDatasetWithY0
from lavar._data.scaler import StandardScalerTorch
from lavar._training.stage1 import train_lavar
from lavar._training.stage2 import train_supply_heads
from lavar.config import LAVARConfig
from lavar.forecaster import GuardrailMetadata, LAVARForecaster

# ---------------------------------------------------------------------------
# Internal dataclasses (ported from notebooks/rolling_eval.py)
# ---------------------------------------------------------------------------


@dataclass
class FoldData:
    X_train_raw: torch.Tensor
    y_train_raw: torch.Tensor
    X_val_raw: torch.Tensor
    y_val_raw: torch.Tensor
    x_scaler: StandardScalerTorch
    X_train: torch.Tensor
    X_val: torch.Tensor


@dataclass
class FoldModels:
    lavar: LAVAR
    dense_model: Optional[torch.nn.Module]
    sparse_model: Optional[torch.nn.Module]
    ultra_model: Optional[torch.nn.Module]
    dense_idx: List[int]
    sparse_idx: List[int]
    ultra_idx: List[int]


# ---------------------------------------------------------------------------
# Public result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class FoldResult:
    fold_id: int
    t_end: int
    metrics: dict
    guardrail_meta: GuardrailMetadata


@dataclass
class EvaluationResults:
    folds: List[FoldResult]
    summary: Dict[str, float]

    def to_dataframe(self) -> pd.DataFrame:
        rows = []
        for fr in self.folds:
            row = {
                "fold_id": fr.fold_id,
                "t_end": fr.t_end,
                "mse": fr.metrics.get("fold_mse"),
                "rmse": fr.metrics.get("fold_rmse"),
                "mae": fr.metrics.get("fold_mae"),
                "naive_mse": fr.metrics.get("naive_mse"),
                "skill_vs_naive": fr.metrics.get("skill_vs_naive"),
                "worst_h": fr.metrics.get("worst_h"),
                "worst_h_mse": fr.metrics.get("worst_h_mse"),
                "guardrail_triggered": fr.guardrail_meta.get("guardrail_triggered", False),
                "fallback_used": fr.guardrail_meta.get("fallback_used", False),
                "clipped_count": fr.guardrail_meta.get("clipped_count", 0),
            }
            rows.append(row)
        return pd.DataFrame(rows)

    def plot(self) -> None:
        try:
            import matplotlib.pyplot as plt
        except ImportError:
            print("matplotlib is required for plotting. Install it with: pip install matplotlib")
            return

        df = self.to_dataframe()
        fig, axes = plt.subplots(2, 1, figsize=(10, 6), sharex=True)

        axes[0].plot(df["fold_id"], df["mse"], marker="o", label="MSE")
        axes[0].plot(
            df["fold_id"],
            df["naive_mse"],
            marker="x",
            linestyle="--",
            label="Naive MSE",
        )
        axes[0].set_ylabel("MSE")
        axes[0].legend()
        axes[0].set_title("Rolling Evaluation — MSE per Fold")

        axes[1].plot(df["fold_id"], df["skill_vs_naive"], marker="o", color="green")
        axes[1].axhline(0, color="gray", linestyle="--", linewidth=0.8)
        axes[1].set_ylabel("Skill vs Naive")
        axes[1].set_xlabel("Fold")

        plt.tight_layout()
        plt.show()


# ---------------------------------------------------------------------------
# Internal helpers (ported from notebooks/rolling_eval.py)
# ---------------------------------------------------------------------------


def prepare_fold_data(
    X: torch.Tensor,
    y: torch.Tensor,
    t_end: int,
    cfg: LAVARConfig,
) -> FoldData:
    X_train_raw = X[:t_end]
    y_train_raw = y[:t_end]

    X_val_raw = X[t_end - (cfg.dyn_p + cfg.horizon) : t_end + cfg.horizon]
    y_val_raw = y[t_end - (cfg.dyn_p + cfg.horizon) : t_end + cfg.horizon]

    x_scaler = StandardScalerTorch().fit(X_train_raw)
    X_train = x_scaler.transform(X_train_raw)
    X_val = x_scaler.transform(X_val_raw)

    return FoldData(
        X_train_raw=X_train_raw,
        y_train_raw=y_train_raw,
        X_val_raw=X_val_raw,
        y_val_raw=y_val_raw,
        x_scaler=x_scaler,
        X_train=X_train,
        X_val=X_val,
    )


def build_fold_loaders(
    X_train: torch.Tensor,
    y_train: torch.Tensor,
    X_val: torch.Tensor,
    y_val: torch.Tensor,
    cfg: LAVARConfig,
) -> Tuple[DataLoader, DataLoader, DataLoader, DataLoader]:
    stage1_train_ds = RollingXYDataset(x=X_train, y=y_train, p=cfg.dyn_p, horizon=cfg.horizon)
    stage1_val_ds = RollingXYDataset(x=X_val, y=y_val, p=cfg.dyn_p, horizon=cfg.horizon)
    stage1_train_loader = DataLoader(
        stage1_train_ds,
        batch_size=cfg.batch_size,
        shuffle=True,
        num_workers=cfg.num_workers,
    )
    stage1_val_loader = DataLoader(
        stage1_val_ds,
        batch_size=cfg.batch_size,
        shuffle=False,
        num_workers=cfg.num_workers,
    )

    stage2_train_ds = RollingXYDatasetWithY0(x=X_train, y=y_train, p=cfg.dyn_p, horizon=cfg.horizon)
    stage2_val_ds = RollingXYDatasetWithY0(x=X_val, y=y_val, p=cfg.dyn_p, horizon=cfg.horizon)
    stage2_train_loader = DataLoader(
        stage2_train_ds,
        batch_size=cfg.batch_size,
        shuffle=True,
        num_workers=cfg.num_workers,
    )
    stage2_val_loader = DataLoader(
        stage2_val_ds,
        batch_size=cfg.batch_size,
        shuffle=False,
        num_workers=cfg.num_workers,
    )

    return (
        stage1_train_loader,
        stage1_val_loader,
        stage2_train_loader,
        stage2_val_loader,
    )


def train_fold_models(
    X_train: torch.Tensor,
    y_train: torch.Tensor,
    X_val: torch.Tensor,
    y_val: torch.Tensor,
    cfg: LAVARConfig,
    save_paths: Optional[Dict[str, Optional[str]]] = None,
) -> FoldModels:
    s1_tr, s1_va, s2_tr, s2_va = build_fold_loaders(X_train, y_train, X_val, y_val, cfg)

    lavar = LAVAR(
        input_dim=X_train.shape[1],
        latent_dim=cfg.latent_dim,
        transition_order=cfg.dyn_p,
        encoder_hidden_dims=cfg.encoder_hidden,
        decoder_hidden_dims=cfg.decoder_hidden,
    ).to(cfg.device)

    save_stage1 = save_paths.get("stage1") if save_paths else None
    train_lavar(
        model=lavar,
        train_loader=s1_tr,
        val_loader=s1_va,
        cfg=cfg,
        save_path=save_stage1,
    )

    save_dense = save_paths.get("dense") if save_paths else None
    save_sparse = save_paths.get("sparse") if save_paths else None
    save_ultra = save_paths.get("ultra") if save_paths else None

    split = train_supply_heads(
        lavar=lavar,
        train_loader=s2_tr,
        val_loader=s2_va,
        cfg=cfg,
        save_dense=save_dense,
        save_sparse=save_sparse,
        save_ultra=save_ultra,
    )

    return FoldModels(
        lavar=lavar,
        dense_model=split["dense_model"],
        sparse_model=split["sparse_model"],
        ultra_model=split["ultra_model"],
        dense_idx=split["dense_indices"],
        sparse_idx=split["sparse_indices"],
        ultra_idx=split["ultra_indices"],
    )


@torch.no_grad()
def _build_upper_bounds(
    y_train_raw: torch.Tensor,
    cfg: LAVARConfig,
    device: torch.device,
) -> torch.Tensor:
    q = float(getattr(cfg, "pred_guardrail_quantile", 0.995))
    mult = float(getattr(cfg, "pred_guardrail_multiplier", 2.0))
    min_upper = float(getattr(cfg, "pred_guardrail_min_upper", 1.0))

    q = min(max(q, 0.50), 0.9999)
    yq = torch.quantile(y_train_raw.to(torch.float32), q=q, dim=0)
    upper = torch.clamp(yq * mult, min=min_upper)
    return upper.to(device)


@torch.no_grad()
def _sanitize_predictions(
    y_hat: torch.Tensor,
    y0_raw: torch.Tensor,
    upper_bounds: torch.Tensor,
) -> Tuple[torch.Tensor, Dict[str, object]]:
    H, Dy = y_hat.shape
    naive = y0_raw.unsqueeze(0).repeat(H, 1)

    meta: Dict[str, object] = {
        "guardrail_triggered": False,
        "fallback_used": False,
        "fallback_targets": [],
        "nonfinite_count": 0,
        "clipped_count": 0,
    }

    out = y_hat.clone()
    finite_mask = torch.isfinite(out)
    nonfinite_count = int((~finite_mask).sum().item())
    if nonfinite_count > 0:
        bad_targets_mask = (~finite_mask).any(dim=0)
        bad_targets = bad_targets_mask.nonzero(as_tuple=False).squeeze(-1)
        if bad_targets.numel() > 0:
            out[:, bad_targets] = naive[:, bad_targets]
            meta["guardrail_triggered"] = True
            meta["fallback_used"] = True
            meta["fallback_targets"] = bad_targets.tolist()
            meta["nonfinite_count"] = nonfinite_count

    out = torch.clamp(out, min=0.0)
    upper = upper_bounds.unsqueeze(0).expand_as(out)
    clipped_high = out > upper
    clipped_count = int(clipped_high.sum().item())
    if clipped_count > 0:
        out = torch.minimum(out, upper)
        meta["guardrail_triggered"] = True
        meta["clipped_count"] = clipped_count

    if not torch.isfinite(out).all():
        out = naive
        meta["guardrail_triggered"] = True
        meta["fallback_used"] = True
        meta["fallback_targets"] = list(range(Dy))

    return out, meta


@torch.no_grad()
def forecast_fold(
    X: torch.Tensor,
    y: torch.Tensor,
    t_end: int,
    cfg: LAVARConfig,
    x_scaler: StandardScalerTorch,
    models: FoldModels,
    y_train_raw: torch.Tensor,
    return_meta: bool = False,
) -> Tuple[torch.Tensor, torch.Tensor] | Tuple[torch.Tensor, torch.Tensor, Dict[str, object]]:
    x_recent_raw = X[t_end - cfg.dyn_p : t_end]
    x_recent = x_scaler.transform(x_recent_raw)

    dev = torch.device(cfg.device)
    x_recent = x_recent.to(dev)

    Dy = y.shape[1]
    y_hat_full = torch.zeros(cfg.horizon, Dy, device=dev, dtype=torch.float32)

    y0_raw = y_train_raw[t_end - 1].to(dev)
    upper_bounds = _build_upper_bounds(y_train_raw=y_train_raw, cfg=cfg, device=dev)

    if models.dense_model is not None and len(models.dense_idx) > 0:
        y0_dense = y0_raw[models.dense_idx]
        y_hat_dense = models.dense_model(x_recent.unsqueeze(0), y0=y0_dense.unsqueeze(0)).squeeze(0)
        y_hat_full[:, models.dense_idx] = y_hat_dense

    if models.sparse_model is not None and len(models.sparse_idx) > 0:
        y0_sparse = y0_raw[models.sparse_idx]
        y_hat_sparse = models.sparse_model(
            x_recent.unsqueeze(0), y0=y0_sparse.unsqueeze(0)
        ).squeeze(0)
        y_hat_full[:, models.sparse_idx] = y_hat_sparse

    if models.ultra_model is not None and len(models.ultra_idx) > 0:
        y_hat_ultra = models.ultra_model(x_recent.unsqueeze(0)).squeeze(0)
        y_hat_full[:, models.ultra_idx] = y_hat_ultra

    y_hat_full, guardrail_meta = _sanitize_predictions(
        y_hat=y_hat_full,
        y0_raw=y0_raw,
        upper_bounds=upper_bounds,
    )

    if bool(getattr(cfg, "forecast_round_to_int", True)):
        y_hat_full = torch.round(y_hat_full)

    y_hat = y_hat_full.detach().cpu()
    y_true = y[t_end : t_end + cfg.horizon].detach().cpu()
    if return_meta:
        return y_hat, y_true, guardrail_meta
    return y_hat, y_true


def compute_fold_metrics(
    y_hat: torch.Tensor,
    y_true: torch.Tensor,
    y_last: torch.Tensor,
) -> Dict[str, object]:
    if y_hat.ndim != 2 or y_true.ndim != 2:
        raise ValueError(
            "Predictions and targets must both be two-dimensional. "
            f"Got {y_hat.shape} and {y_true.shape}."
        )
    if y_hat.shape != y_true.shape:
        raise ValueError(
            f"Predictions and targets must have identical shapes: {y_hat.shape} != {y_true.shape}"
        )
    if y_hat.numel() == 0:
        raise ValueError("Predictions and targets must not be empty.")
    if y_last.ndim != 1 or y_last.shape[0] != y_hat.shape[1]:
        raise ValueError(
            "The last observation must be one-dimensional with one value per target. "
            f"Got {y_last.shape} for {y_hat.shape[1]} targets."
        )
    if not torch.isfinite(y_hat).all():
        raise ValueError("Predictions contain non-finite values.")
    if not torch.isfinite(y_true).all() or not torch.isfinite(y_last).all():
        raise ValueError("Targets contain non-finite values.")

    err = y_hat - y_true
    se = err**2
    fold_mse = se.mean().item()

    mse_by_horizon = se.mean(dim=1)
    mse_by_target = se.mean(dim=0)
    rmse_by_target = mse_by_target.sqrt()

    bias_by_target = err.mean(dim=0)
    var_by_target = err.var(dim=0, unbiased=False)

    ae = err.abs()
    fold_mae = ae.mean().item()
    fold_rmse = se.mean().sqrt().item()

    y_naive = y_last.unsqueeze(0).repeat(y_hat.shape[0], 1)
    naive_err = y_naive - y_true
    naive_se = naive_err**2
    naive_mse = naive_se.mean().item()
    naive_mse_by_target = naive_se.mean(dim=0)
    skill_vs_naive = (1.0 - fold_mse / naive_mse) if naive_mse > 0 else float("nan")
    skill_vs_naive_by_target = torch.full_like(mse_by_target, float("nan"))
    valid_skill_mask = naive_mse_by_target > 0
    skill_vs_naive_by_target[valid_skill_mask] = (
        1.0 - mse_by_target[valid_skill_mask] / naive_mse_by_target[valid_skill_mask]
    )

    worst_h = int(mse_by_horizon.argmax().item())
    worst_h_mse = mse_by_horizon[worst_h].item()

    k = int(min(5, mse_by_target.numel()))
    worst_t_idx = torch.topk(mse_by_target, k=k).indices.tolist()
    worst_targets = ", ".join(
        f"{i}(mse={mse_by_target[i].item():.3g}, bias={bias_by_target[i].item():+.3g})"
        for i in worst_t_idx
    )

    return {
        "fold_mse": fold_mse,
        "fold_rmse": fold_rmse,
        "fold_mae": fold_mae,
        "mse_by_horizon": mse_by_horizon,
        "mse_by_target": mse_by_target,
        "rmse_by_target": rmse_by_target,
        "bias_by_target": bias_by_target,
        "var_by_target": var_by_target,
        "naive_mse": naive_mse,
        "naive_mse_by_target": naive_mse_by_target,
        "skill_vs_naive": skill_vs_naive,
        "skill_vs_naive_by_target": skill_vs_naive_by_target,
        "worst_h": worst_h,
        "worst_h_mse": worst_h_mse,
        "worst_targets": worst_targets,
    }


def make_fold_save_paths(
    save_root: str,
    fold_id: int,
    t_end: int,
) -> Dict[str, str]:
    fold_dir = os.path.join(save_root, f"fold_{fold_id:03d}_end_{t_end}")
    os.makedirs(fold_dir, exist_ok=True)
    return {
        "stage1": os.path.join(fold_dir, "lavar_best.pth"),
        "dense": os.path.join(fold_dir, "lavar_supply_dense_best.pth"),
        "sparse": os.path.join(fold_dir, "lavar_supply_sparse_best.pth"),
        "ultra": os.path.join(fold_dir, "lavar_supply_ultra_best.pth"),
    }


# ---------------------------------------------------------------------------
# RollingEvaluator
# ---------------------------------------------------------------------------

_QUALITY_TRIGGER_SKILL_THR = -0.5  # skill < this triggers full refit
_QUALITY_TRIGGER_GUARDRAIL_COUNT = 3  # consecutive guardrail folds triggers refit


class RollingEvaluator:
    """Rolling-origin evaluation with independent refit cadences for LAVAR and supply heads."""

    def __init__(self, config: LAVARConfig) -> None:
        self.cfg = config

    def evaluate(
        self,
        X: np.ndarray | torch.Tensor,
        y: np.ndarray | torch.Tensor,
        lavar_retrain_cadence: int = 90,
        heads_retrain_cadence: int = 90,
        quality_triggers: bool = True,
        fold_step: int = 14,
        verbose: bool = True,
    ) -> EvaluationResults:
        cfg = self.cfg
        X_t = _to_tensor(X)
        y_t = _to_tensor(y)

        T = len(X_t)
        min_train = max(cfg.train_days, cfg.dyn_p + cfg.horizon + 1)
        start = min_train

        if start + cfg.horizon > T:
            raise ValueError(
                "Not enough data for even one fold. "
                f"T={T}, min_train={min_train}, horizon={cfg.horizon}"
            )

        model = LAVARForecaster(cfg)
        last_full_fit: Optional[int] = None
        last_heads_fit: Optional[int] = None
        consecutive_guardrails = 0

        folds: List[FoldResult] = []
        fold_id = 0

        t_end = start
        while t_end + cfg.horizon <= T:
            need_full_refit = False
            need_heads_refit = False

            # Decide what to retrain
            if last_full_fit is None:
                need_full_refit = True
            else:
                days_since_full = t_end - last_full_fit
                if days_since_full >= lavar_retrain_cadence:
                    need_full_refit = True

            if not need_full_refit and last_heads_fit is not None:
                days_since_heads = t_end - last_heads_fit
                if days_since_heads >= heads_retrain_cadence:
                    need_heads_refit = True

            # Quality triggers
            if quality_triggers and not need_full_refit and len(folds) > 0:
                last_fold = folds[-1]
                skill = last_fold.metrics.get("skill_vs_naive", 0.0)
                if skill < _QUALITY_TRIGGER_SKILL_THR:
                    need_full_refit = True
                    if verbose:
                        print(
                            f"  [quality] skill={skill:.3f} < "
                            f"{_QUALITY_TRIGGER_SKILL_THR} → full refit"
                        )
                if last_fold.guardrail_meta.get("guardrail_triggered", False):
                    consecutive_guardrails += 1
                else:
                    consecutive_guardrails = 0
                if consecutive_guardrails >= _QUALITY_TRIGGER_GUARDRAIL_COUNT:
                    need_full_refit = True
                    consecutive_guardrails = 0
                    if verbose:
                        print(
                            f"  [quality] {_QUALITY_TRIGGER_GUARDRAIL_COUNT} "
                            "consecutive guardrails → full refit"
                        )

            # Train
            X_train = X_t[:t_end]
            y_train = y_t[:t_end]

            if need_full_refit:
                model.fit(X_train, y_train)
                last_full_fit = t_end
                last_heads_fit = t_end
                if verbose:
                    print(f"  Fold {fold_id}: full refit at t_end={t_end}")
            elif need_heads_refit:
                model.fit_heads(X_train, y_train)
                last_heads_fit = t_end
                if verbose:
                    print(f"  Fold {fold_id}: heads refit at t_end={t_end}")

            # Forecast
            X_recent = X_t[t_end - cfg.dyn_p : t_end]
            y_recent = y_t[t_end - cfg.dyn_p : t_end]
            prediction = model.predict_with_metadata(X_recent, y_recent=y_recent)
            y_hat = prediction.values

            y_hat_t = torch.from_numpy(y_hat).float()
            y_true_t = y_t[t_end : t_end + cfg.horizon].float()

            # Truncate if we're near the end
            actual_h = min(cfg.horizon, T - t_end)
            y_hat_t = y_hat_t[:actual_h]
            y_true_t = y_true_t[:actual_h]

            y_last = y_t[t_end - 1]
            metrics = compute_fold_metrics(y_hat_t, y_true_t, y_last)

            folds.append(
                FoldResult(
                    fold_id=fold_id,
                    t_end=t_end,
                    metrics=metrics,
                    guardrail_meta=prediction.guardrail_metadata,
                )
            )

            if verbose and fold_id % 5 == 0:
                print(
                    f"  Fold {fold_id}: t_end={t_end}, mse={metrics['fold_mse']:.4f}, "
                    f"skill={metrics['skill_vs_naive']:.3f}"
                )

            fold_id += 1
            t_end += fold_step

        # Summary
        summary = self._compute_summary(folds)
        if verbose:
            print(
                f"\n  Summary ({len(folds)} folds): "
                f"median_mse={summary['median_mse']:.4f}, "
                f"mean_mse={summary['mean_mse']:.4f}, "
                f"median_skill={summary['median_skill']:.3f}"
            )

        return EvaluationResults(folds=folds, summary=summary)

    @staticmethod
    def _compute_summary(folds: List[FoldResult]) -> Dict[str, float]:
        if not folds:
            return {
                "mean_mse": float("nan"),
                "median_mse": float("nan"),
                "mean_skill": float("nan"),
                "median_skill": float("nan"),
            }
        mses = [f.metrics["fold_mse"] for f in folds]
        skills = [
            f.metrics["skill_vs_naive"] for f in folds if not np.isnan(f.metrics["skill_vs_naive"])
        ]
        maes = [f.metrics["fold_mae"] for f in folds]
        return {
            "mean_mse": float(np.mean(mses)),
            "median_mse": float(np.median(mses)),
            "mean_mae": float(np.mean(maes)),
            "median_mae": float(np.median(maes)),
            "mean_skill": float(np.mean(skills)) if skills else float("nan"),
            "median_skill": float(np.median(skills)) if skills else float("nan"),
            "n_folds": len(folds),
        }


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def _to_tensor(arr: np.ndarray | torch.Tensor) -> torch.Tensor:
    if isinstance(arr, np.ndarray):
        return torch.from_numpy(arr).float()
    return arr.float()
