from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Mapping, Optional, Tuple, TypedDict

import numpy as np
import torch
from torch.utils.data import DataLoader

from lavar._core.model import LAVAR, LAVARWithSupply, LAVARWithSupplyGRU
from lavar._data.dataset import RollingXYDataset, RollingXYDatasetWithY0
from lavar._data.scaler import StandardScalerTorch
from lavar._training.stage1 import train_lavar
from lavar._training.stage2 import train_supply_heads
from lavar.config import LAVARConfig


class GuardrailMetadata(TypedDict):
    guardrail_triggered: bool
    fallback_used: bool
    fallback_targets: list[int]
    nonfinite_count: int
    clipped_count: int


@dataclass(frozen=True)
class PredictionResult:
    values: np.ndarray
    guardrail_metadata: GuardrailMetadata


class Stage2ModelState(TypedDict):
    dense_idx: list[int]
    sparse_idx: list[int]
    ultra_idx: list[int]
    dense_state: dict[str, torch.Tensor] | None
    sparse_state: dict[str, torch.Tensor] | None
    ultra_state: dict[str, torch.Tensor] | None


class LAVARForecaster:
    """sklearn-style wrapper around the two-stage LAVAR pipeline."""

    def __init__(self, config: Optional[LAVARConfig] = None) -> None:
        self.cfg = config or LAVARConfig()

        self._lavar: Optional[LAVAR] = None
        self._dense_model: Optional[LAVARWithSupply] = None
        self._sparse_model: Optional[LAVARWithSupply] = None
        self._ultra_model: Optional[LAVARWithSupply] = None
        self._dense_idx: List[int] = []
        self._sparse_idx: List[int] = []
        self._ultra_idx: List[int] = []

        self._x_scaler: Optional[StandardScalerTorch] = None
        self._y_scaler: Optional[StandardScalerTorch] = None
        self._input_dim: Optional[int] = None
        self._supply_dim: Optional[int] = None

        # Guardrail upper bounds (computed from training y)
        self._upper_bounds: Optional[torch.Tensor] = None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_fitted(self) -> bool:
        return self._lavar is not None

    @property
    def is_heads_fitted(self) -> bool:
        return (
            self._dense_model is not None
            or self._sparse_model is not None
            or self._ultra_model is not None
        )

    # ------------------------------------------------------------------
    # fit
    # ------------------------------------------------------------------

    def fit(self, X: np.ndarray | torch.Tensor, y: np.ndarray | torch.Tensor) -> "LAVARForecaster":
        self.fit_stage1_shared(X, y=y)
        self.fit_stage2_private(X, y)
        return self

    def fit_stage1_shared(
        self,
        X: np.ndarray | torch.Tensor,
        y: Optional[np.ndarray | torch.Tensor] = None,
    ) -> "LAVARForecaster":
        """Train Stage 1 backbone (global/shared style by default)."""
        cfg = self.cfg
        X_t = self._to_tensor(X)

        # Stage 1 windowing does not consume y unless stage1_use_supply_history=True.
        if y is None:
            y_t = torch.zeros((X_t.shape[0], 1), dtype=torch.float32)
        else:
            y_t = self._to_tensor(y)

        # Shared scaler for non-supply features.
        self._x_scaler = StandardScalerTorch().fit(X_t)
        X_scaled = self._x_scaler.transform(X_t)

        stage1_use_supply = bool(getattr(cfg, "stage1_use_supply_history", False))
        if stage1_use_supply:
            if y is None:
                raise ValueError(
                    "stage1_use_supply_history=True requires y in fit_stage1_shared()."
                )
            self._y_scaler = StandardScalerTorch().fit(y_t)
        else:
            # Keep stage-2 private scaler unset until fit_stage2_private().
            self._y_scaler = None

        input_dim = X_t.shape[1] + (y_t.shape[1] if stage1_use_supply else 0)
        self._input_dim = input_dim

        self._lavar = LAVAR(
            input_dim=input_dim,
            latent_dim=cfg.latent_dim,
            transition_order=cfg.dyn_p,
            encoder_hidden_dims=cfg.encoder_hidden,
            decoder_hidden_dims=cfg.decoder_hidden,
            dynamics_type=cfg.latent_dynamics_type,
            dynamics_gru_hidden_dim=cfg.dynamics_gru_hidden_dim,
            encoder_dropout=cfg.encoder_dropout,
        )

        s1_train, s1_val = self._build_stage1_loaders(
            X_scaled, y_t, use_supply_history=stage1_use_supply
        )
        train_lavar(
            model=self._lavar,
            train_loader=s1_train,
            val_loader=s1_val,
            cfg=cfg,
            save_path=None,
        )
        return self

    def fit_stage2_private(
        self,
        X: np.ndarray | torch.Tensor,
        y: np.ndarray | torch.Tensor,
    ) -> "LAVARForecaster":
        """Train Stage 2 heads as hospital-private adaptation on fixed Stage 1."""
        if not self.is_fitted or self._lavar is None or self._x_scaler is None:
            raise RuntimeError("LAVAR must be fitted first. Call fit_stage1_shared() first.")
        cfg = self.cfg
        X_t = self._to_tensor(X)
        y_t = self._to_tensor(y)

        self._supply_dim = y_t.shape[1]
        X_scaled = self._x_scaler.transform(X_t)

        expected_input_dim = X_t.shape[1] + (y_t.shape[1] if cfg.use_supply_history else 0)
        if self._lavar.input_dim != expected_input_dim:
            raise ValueError(
                "Stage 1 / Stage 2 input mismatch. "
                f"LAVAR input_dim={self._lavar.input_dim}, "
                f"but Stage 2 expects {expected_input_dim}. "
                "Train Stage 1 with stage1_use_supply_history=True when using "
                "Stage 2 supply-history augmentation."
            )

        if cfg.use_supply_history:
            self._y_scaler = StandardScalerTorch().fit(y_t)
        else:
            self._y_scaler = None

        self._fit_heads_internal(X_scaled, y_t, use_supply_history=cfg.use_supply_history)
        self._upper_bounds = self._build_upper_bounds(y_t)
        return self

    def restore_stage1_state(
        self,
        X: np.ndarray | torch.Tensor,
        y: np.ndarray | torch.Tensor,
        state_dict: Mapping[str, torch.Tensor],
    ) -> "LAVARForecaster":
        """Initialize the shared model and scalers from a Stage 1 state dictionary."""
        X_t = self._to_tensor(X)
        y_t = self._to_tensor(y)
        if len(X_t) != len(y_t):
            raise ValueError(f"Feature and target lengths must match: {len(X_t)} != {len(y_t)}")

        self._x_scaler = StandardScalerTorch().fit(X_t)
        self._y_scaler = (
            StandardScalerTorch().fit(y_t) if self.cfg.stage1_use_supply_history else None
        )
        input_dim = X_t.shape[1] + (y_t.shape[1] if self.cfg.stage1_use_supply_history else 0)
        self._input_dim = int(input_dim)
        self._lavar = self._new_lavar(input_dim).to(self.cfg.device)
        self._lavar.load_state_dict(state_dict)
        self._lavar.eval()
        return self

    def stage2_state_dict(self) -> Stage2ModelState:
        """Return the private-head state needed to restore Stage 2 inference."""
        return {
            "dense_idx": list(self._dense_idx),
            "sparse_idx": list(self._sparse_idx),
            "ultra_idx": list(self._ultra_idx),
            "dense_state": self._dense_model.state_dict() if self._dense_model else None,
            "sparse_state": self._sparse_model.state_dict() if self._sparse_model else None,
            "ultra_state": self._ultra_model.state_dict() if self._ultra_model else None,
        }

    def restore_stage2_state(
        self,
        state: Stage2ModelState,
        y_train: np.ndarray | torch.Tensor,
    ) -> "LAVARForecaster":
        """Restore private heads and guardrails onto an initialized Stage 1 model."""
        if self._lavar is None:
            raise RuntimeError("Restore Stage 1 before restoring Stage 2.")
        y_t = self._to_tensor(y_train)
        self._supply_dim = int(y_t.shape[1])
        if self.cfg.use_supply_history:
            self._y_scaler = StandardScalerTorch().fit(y_t)

        self._dense_idx = [int(index) for index in state["dense_idx"]]
        self._sparse_idx = [int(index) for index in state["sparse_idx"]]
        self._ultra_idx = [int(index) for index in state["ultra_idx"]]
        self._dense_model = self._load_supply_head(
            self._lavar, state["dense_state"], self._dense_idx, self.cfg, "delta_mse"
        )
        self._sparse_model = self._load_supply_head(
            self._lavar, state["sparse_state"], self._sparse_idx, self.cfg, "delta_mse"
        )
        self._ultra_model = self._load_supply_head(
            self._lavar, state["ultra_state"], self._ultra_idx, self.cfg, "zinb"
        )
        device = torch.device(self.cfg.device)
        for model in (self._dense_model, self._sparse_model, self._ultra_model):
            if model is not None:
                model.to(device).eval()
        self._upper_bounds = self._build_upper_bounds(y_t).to(device)
        return self

    # ------------------------------------------------------------------
    # fit_heads
    # ------------------------------------------------------------------

    def fit_heads(
        self, X: np.ndarray | torch.Tensor, y: np.ndarray | torch.Tensor
    ) -> "LAVARForecaster":
        return self.fit_stage2_private(X, y)

    # ------------------------------------------------------------------
    # predict
    # ------------------------------------------------------------------

    @torch.no_grad()
    def predict(
        self,
        X_recent: np.ndarray | torch.Tensor,
        y_recent: Optional[np.ndarray | torch.Tensor] = None,
    ) -> np.ndarray:
        return self.predict_with_metadata(X_recent, y_recent=y_recent).values

    @torch.no_grad()
    def predict_with_metadata(
        self,
        X_recent: np.ndarray | torch.Tensor,
        y_recent: Optional[np.ndarray | torch.Tensor] = None,
    ) -> PredictionResult:
        if not self.is_heads_fitted:
            raise RuntimeError("Supply heads must be fitted. Call fit() or fit_heads() first.")
        if self._x_scaler is None or self._supply_dim is None:
            raise RuntimeError("The fitted forecaster is missing scaler or target metadata.")
        cfg = self.cfg
        dev = torch.device(cfg.device)

        X_r = self._to_tensor(X_recent)  # (p or p+1, Dx)
        X_r = self._x_scaler.transform(X_r)

        # Build augmented input if use_supply_history
        if cfg.use_supply_history:
            if y_recent is None:
                raise ValueError("y_recent is required when use_supply_history=True.")
            if self._y_scaler is None:
                raise RuntimeError("The fitted forecaster is missing its target scaler.")
            y_r = self._to_tensor(y_recent)
            y_r_scaled = self._y_scaler.transform(y_r)
            X_r = torch.cat([X_r, y_r_scaled], dim=-1)

        x_input = X_r.unsqueeze(0).to(dev)  # (1, T, input_dim)

        Dy = self._supply_dim
        y_hat_full = torch.zeros(cfg.horizon, Dy, device=dev, dtype=torch.float32)

        # y0 for delta heads: last observed raw supply
        if y_recent is not None:
            y0_raw = self._to_tensor(y_recent)[-1].to(dev)
        else:
            y0_raw = torch.zeros(Dy, device=dev)

        if self._dense_model is not None and len(self._dense_idx) > 0:
            y0_dense = y0_raw[self._dense_idx]
            pred = self._dense_model(x_input, y0=y0_dense.unsqueeze(0)).squeeze(0)
            y_hat_full[:, self._dense_idx] = pred

        if self._sparse_model is not None and len(self._sparse_idx) > 0:
            y0_sparse = y0_raw[self._sparse_idx]
            pred = self._sparse_model(x_input, y0=y0_sparse.unsqueeze(0)).squeeze(0)
            y_hat_full[:, self._sparse_idx] = pred

        if self._ultra_model is not None and len(self._ultra_idx) > 0:
            pred = self._ultra_model(x_input).squeeze(0)
            y_hat_full[:, self._ultra_idx] = pred

        # Guardrails
        y_hat_full, guardrail_metadata = self._sanitize_predictions(y_hat_full, y0_raw)

        # Zero gating: clamp predictions for targets with zero recent history
        zero_gate_k = getattr(cfg, "zero_gate_k", 0)
        if zero_gate_k > 0 and y_recent is not None:
            y_recent_t = self._to_tensor(y_recent).to(dev)
            k = min(zero_gate_k, y_recent_t.shape[0])
            recent_sum = y_recent_t[-k:].sum(dim=0)  # (Dy,)
            zero_mask = recent_sum == 0  # (Dy,) bool
            y_hat_full[:, zero_mask] = 0.0

        if cfg.forecast_round_to_int:
            y_hat_full = torch.round(y_hat_full)

        return PredictionResult(
            values=y_hat_full.detach().cpu().numpy(),
            guardrail_metadata=guardrail_metadata,
        )

    # ------------------------------------------------------------------
    # save / load
    # ------------------------------------------------------------------

    def save(self, path: Path | str) -> None:
        state = {
            "config": self.cfg.model_dump(),
            "input_dim": self._input_dim,
            "supply_dim": self._supply_dim,
            "x_scaler_mean": self._x_scaler.mean if self._x_scaler else None,
            "x_scaler_std": self._x_scaler.std if self._x_scaler else None,
            "y_scaler_mean": self._y_scaler.mean if self._y_scaler else None,
            "y_scaler_std": self._y_scaler.std if self._y_scaler else None,
            "lavar_state": self._lavar.state_dict() if self._lavar else None,
            "dense_model_state": self._dense_model.state_dict() if self._dense_model else None,
            "sparse_model_state": self._sparse_model.state_dict() if self._sparse_model else None,
            "ultra_model_state": self._ultra_model.state_dict() if self._ultra_model else None,
            "dense_idx": self._dense_idx,
            "sparse_idx": self._sparse_idx,
            "ultra_idx": self._ultra_idx,
            "upper_bounds": self._upper_bounds,
        }
        torch.save(state, path)

    @classmethod
    def load(cls, path: Path | str) -> "LAVARForecaster":
        state = torch.load(path, map_location="cpu", weights_only=True)
        cfg = LAVARConfig(**state["config"])
        forecaster = cls(config=cfg)

        forecaster._input_dim = state["input_dim"]
        forecaster._supply_dim = state["supply_dim"]
        forecaster._dense_idx = state["dense_idx"]
        forecaster._sparse_idx = state["sparse_idx"]
        forecaster._ultra_idx = state["ultra_idx"]
        forecaster._upper_bounds = state["upper_bounds"]

        # Scalers
        if state["x_scaler_mean"] is not None:
            forecaster._x_scaler = StandardScalerTorch()
            forecaster._x_scaler.mean = state["x_scaler_mean"]
            forecaster._x_scaler.std = state["x_scaler_std"]
        if state["y_scaler_mean"] is not None:
            forecaster._y_scaler = StandardScalerTorch()
            forecaster._y_scaler.mean = state["y_scaler_mean"]
            forecaster._y_scaler.std = state["y_scaler_std"]

        # LAVAR
        if state["lavar_state"] is not None:
            forecaster._lavar = LAVAR(
                input_dim=state["input_dim"],
                latent_dim=cfg.latent_dim,
                transition_order=cfg.dyn_p,
                encoder_hidden_dims=cfg.encoder_hidden,
                decoder_hidden_dims=cfg.decoder_hidden,
                dynamics_type=cfg.latent_dynamics_type,
                dynamics_gru_hidden_dim=cfg.dynamics_gru_hidden_dim,
                encoder_dropout=cfg.encoder_dropout,
            )
            forecaster._lavar.load_state_dict(state["lavar_state"])

        # Supply heads
        forecaster._dense_model = cls._load_supply_head(
            forecaster._lavar,
            state["dense_model_state"],
            state["dense_idx"],
            cfg,
            "delta_mse",
        )
        forecaster._sparse_model = cls._load_supply_head(
            forecaster._lavar,
            state["sparse_model_state"],
            state["sparse_idx"],
            cfg,
            "delta_mse",
        )
        forecaster._ultra_model = cls._load_supply_head(
            forecaster._lavar,
            state["ultra_model_state"],
            state["ultra_idx"],
            cfg,
            "zinb",
        )

        return forecaster

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _to_tensor(arr: np.ndarray | torch.Tensor) -> torch.Tensor:
        if isinstance(arr, np.ndarray):
            return torch.from_numpy(arr).float()
        return arr.float()

    def _new_lavar(self, input_dim: int) -> LAVAR:
        cfg = self.cfg
        return LAVAR(
            input_dim=input_dim,
            latent_dim=cfg.latent_dim,
            transition_order=cfg.dyn_p,
            encoder_hidden_dims=cfg.encoder_hidden,
            decoder_hidden_dims=cfg.decoder_hidden,
            dynamics_type=cfg.latent_dynamics_type,
            dynamics_gru_hidden_dim=cfg.dynamics_gru_hidden_dim,
            encoder_dropout=cfg.encoder_dropout,
        )

    def _build_stage1_loaders(
        self,
        X_scaled: torch.Tensor,
        y: torch.Tensor,
        use_supply_history: bool,
    ) -> Tuple[DataLoader, DataLoader]:
        cfg = self.cfg
        n = len(X_scaled)
        split = max(1, n - cfg.horizon * 2)

        y_for_ds = y
        if use_supply_history:
            y_for_ds = self._y_scaler.transform(y)

        train_ds = RollingXYDataset(
            x=X_scaled[:split],
            y=y_for_ds[:split],
            p=cfg.dyn_p,
            horizon=cfg.horizon,
            use_supply_history=use_supply_history,
        )
        val_ds = RollingXYDataset(
            x=X_scaled[split:],
            y=y_for_ds[split:],
            p=cfg.dyn_p,
            horizon=cfg.horizon,
            use_supply_history=use_supply_history,
        )
        train_loader = DataLoader(
            train_ds,
            batch_size=cfg.batch_size,
            shuffle=True,
            num_workers=cfg.num_workers,
        )
        val_loader = DataLoader(
            val_ds,
            batch_size=cfg.batch_size,
            shuffle=False,
            num_workers=cfg.num_workers,
        )
        return train_loader, val_loader

    def _build_stage2_loaders(
        self,
        X_scaled: torch.Tensor,
        y: torch.Tensor,
        use_supply_history: bool,
    ) -> Tuple[DataLoader, DataLoader]:
        cfg = self.cfg
        n = len(X_scaled)
        split = max(1, n - cfg.horizon * 2)

        y_for_ds = y
        if use_supply_history:
            y_for_ds = self._y_scaler.transform(y)

        train_ds = RollingXYDatasetWithY0(
            x=X_scaled[:split],
            y=y_for_ds[:split],
            p=cfg.dyn_p,
            horizon=cfg.horizon,
            use_supply_history=use_supply_history,
        )
        val_ds = RollingXYDatasetWithY0(
            x=X_scaled[split:],
            y=y_for_ds[split:],
            p=cfg.dyn_p,
            horizon=cfg.horizon,
            use_supply_history=use_supply_history,
        )
        train_loader = DataLoader(
            train_ds,
            batch_size=cfg.batch_size,
            shuffle=True,
            num_workers=cfg.num_workers,
        )
        val_loader = DataLoader(
            val_ds,
            batch_size=cfg.batch_size,
            shuffle=False,
            num_workers=cfg.num_workers,
        )
        return train_loader, val_loader

    def _fit_heads_internal(
        self,
        X_scaled: torch.Tensor,
        y: torch.Tensor,
        use_supply_history: bool,
    ) -> None:
        cfg = self.cfg
        s2_train, s2_val = self._build_stage2_loaders(
            X_scaled, y, use_supply_history=use_supply_history
        )
        result = train_supply_heads(
            lavar=self._lavar,
            train_loader=s2_train,
            val_loader=s2_val,
            cfg=cfg,
            save_dense=None,
            save_sparse=None,
            save_ultra=None,
        )
        self._dense_model = result["dense_model"]
        self._sparse_model = result["sparse_model"]
        self._ultra_model = result["ultra_model"]
        self._dense_idx = result["dense_indices"]
        self._sparse_idx = result["sparse_indices"]
        self._ultra_idx = result["ultra_indices"]

    def _build_upper_bounds(self, y: torch.Tensor) -> torch.Tensor:
        cfg = self.cfg
        q = min(max(float(cfg.pred_guardrail_quantile), 0.50), 0.9999)
        mult = float(cfg.pred_guardrail_multiplier)
        min_upper = float(cfg.pred_guardrail_min_upper)
        yq = torch.quantile(y.float(), q=q, dim=0)
        return torch.clamp(yq * mult, min=min_upper)

    def _sanitize_predictions(
        self, y_hat: torch.Tensor, y0_raw: torch.Tensor
    ) -> tuple[torch.Tensor, GuardrailMetadata]:
        H, Dy = y_hat.shape
        naive = y0_raw.unsqueeze(0).expand(H, Dy)

        meta: GuardrailMetadata = {
            "guardrail_triggered": False,
            "fallback_used": False,
            "fallback_targets": [],
            "nonfinite_count": 0,
            "clipped_count": 0,
        }

        out = y_hat.clone()

        # Handle non-finite values
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

        # Clamp to [0, upper_bound]
        out = torch.clamp(out, min=0.0)
        if self._upper_bounds is not None:
            upper = self._upper_bounds.to(out.device).unsqueeze(0).expand_as(out)
            clipped_high = out > upper
            clipped_count = int(clipped_high.sum().item())
            if clipped_count > 0:
                out = torch.minimum(out, upper)
                meta["guardrail_triggered"] = True
                meta["clipped_count"] = clipped_count

        # Final safety net
        if not torch.isfinite(out).all():
            out = naive.clone()
            meta["guardrail_triggered"] = True
            meta["fallback_used"] = True
            meta["fallback_targets"] = list(range(Dy))

        return out, meta

    @staticmethod
    def _load_supply_head(
        lavar: Optional[LAVAR],
        state_dict: Optional[dict],
        indices: List[int],
        cfg: LAVARConfig,
        head_type: str,
    ) -> Optional[LAVARWithSupply | LAVARWithSupplyGRU]:
        if state_dict is None or lavar is None or len(indices) == 0:
            return None

        use_gru = getattr(cfg, "stage2_head_type", "mlp") == "gru" and head_type == "delta_mse"

        if use_gru:
            model = LAVARWithSupplyGRU(
                lavar=lavar,
                supply_dim=len(indices),
                horizon=cfg.horizon,
                gru_hidden_dim=cfg.gru_hidden_dim,
                gru_num_layers=cfg.gru_num_layers,
                gru_dropout=cfg.gru_dropout,
            )
        else:
            model = LAVARWithSupply(
                lavar=lavar,
                supply_dim=len(indices),
                horizon=cfg.horizon,
                supply_hidden=cfg.supply_hidden,
                supply_head_type=head_type,
            )

        if head_type == "delta_mse":
            model.set_delta_nonneg_mode(getattr(cfg, "stage2_delta_nonneg_mode", "clamp"))
            model.set_softplus_beta(float(getattr(cfg, "stage2_softplus_beta_end", 8.0)))
        model.load_state_dict(state_dict)
        return model
