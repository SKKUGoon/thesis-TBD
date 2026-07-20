# LAVAR: Latent Autoencoder VAR
# Combination of Encoder and Decoder with VAR as latent factor dynamics (nonlinear + VAR)
# - The model is non linear in the observation space, but linear in the latent state dynamics
# Intuition
# Mathmatics
# - $LatentDynamics(linear): z_t = f(z_{t-1}, \epsilon_t)$
# - $ObservationModel(nonlinear): x_t = g(z_t, \eta_t)$
# - z_t is a low dimentional and structured
# - g is a neural network that maps non linear observation space to latent space
# - f is a learnable VAR model with matrix parameters
#   - Latent dynamics are often approximately linear
#   - Non linearity is pushed into the observation model
#   - Linear latent dynamics will give us the interpretability

from __future__ import annotations

from typing import Dict, Literal, Optional, Sequence, Union

import torch
import torch.nn as nn
import torch.nn.functional as F

from lavar._core.dynamics import GRUDynamics, VARDynamics
from lavar._core.heads import MLP, SupplyHeadGRU, SupplyHeadMSE, SupplyHeadNB, SupplyHeadZINB


class LAVAR(nn.Module):
    r"""
    LAVAR - Latent Autoencoder VAR

    Observation:
      x_t = g_theta(z_t) + \eta_t

    Latent dynamics (VAR(p)):
      z_t = sum_{i=1}^{p} A_i z(t-i)
    """

    def __init__(
        self,
        input_dim: int,
        latent_dim: int,
        transition_order: int = 1,
        eps_zero: float = 1e-6,
        encoder_hidden_dims: Optional[Sequence[int]] = (128, 64),
        decoder_hidden_dims: Optional[Sequence[int]] = (64, 128),
        activation: nn.Module | None = None,
        dynamics_type: str = "var",
        dynamics_gru_hidden_dim: int = 32,
        encoder_dropout: float = 0.0,
    ):
        super().__init__()

        self.input_dim = input_dim
        self.latent_dim = latent_dim
        self.transition_order = transition_order
        self.eps_zero = eps_zero

        if encoder_hidden_dims is None:
            h1 = max(32, 2 * input_dim)
            h2 = max(16, h1 // 2)
            encoder_hidden_dims = [h1, h2]
        else:
            encoder_hidden_dims = list(encoder_hidden_dims)
        if decoder_hidden_dims is None:
            h1 = max(32, 2 * latent_dim)
            h2 = max(16, h1 // 2)
            decoder_hidden_dims = [h2, h1]
        else:
            decoder_hidden_dims = list(decoder_hidden_dims)

        activation = activation or nn.ReLU()
        self.encoder = MLP(
            input_dim, encoder_hidden_dims, latent_dim, activation, dropout_rate=encoder_dropout
        )
        self.decoder = MLP(latent_dim, decoder_hidden_dims, input_dim, activation)
        if dynamics_type == "gru":
            self.dynamics = GRUDynamics(latent_dim, transition_order, dynamics_gru_hidden_dim)
        else:
            self.dynamics = VARDynamics(latent_dim, transition_order)

    @staticmethod
    def replace_zeros(x: torch.Tensor, eps: float = 1e-6) -> torch.Tensor:
        """
        Replace exact zeros with eps to avoid dead gradients when subsequent ops
        include division, log, or gating that can become inactive at 0.
        """
        if eps <= 0.0:
            return x
        return torch.where(
            x == 0, torch.full_like(x, eps), x
        )  # replace zeros with eps (full like) where x == 0

    def encode(self, x: torch.Tensor) -> torch.Tensor:
        x = self.replace_zeros(x, self.eps_zero)
        return self.encoder(x)

    def decode(self, z: torch.Tensor) -> torch.Tensor:
        return self.decoder(z)

    def forward(self, x_seq: torch.Tensor) -> Dict[str, torch.Tensor]:
        """
        x_seq: (B, p+1, input_dim)
               Where last index is the current time and previous p are the history.
          - B: batch size
          - T: number of time steps in the window
          - D: number of features
        """
        B, T, D = x_seq.shape
        p = self.transition_order  # VAR order
        if T != p + 1:
            raise ValueError(f"x_seq must have shape (B, p+1, D). Got {x_seq.shape}")

        # Encode each time step independently
        # Temporal structure is enforced via latent dynamics loss
        z_seq = self.encode(x_seq.reshape(-1, D)).reshape(B, T, self.latent_dim)

        # VAR(p) predicts z_t from previous p latents
        z_pred = self.dynamics(z_seq[:, :-1, :])  # (B, k)

        # Reconstruct x_t from z_t (current latent)
        z_true = z_seq[:, -1, :]
        x_hat = self.decode(z_true)

        return {
            "x_hat": x_hat,
            "z_pred": z_pred,
            "z_true": z_true,
        }

    def rollout_latent(self, z_history: torch.Tensor, horizon: int) -> torch.Tensor:
        """
        z_history: (B, past, k), where k is the latent dimension
        returns: z_future: (B, horizon, k)
        """
        B, p, k = z_history.shape
        z_future = []

        z_buffer = z_history.clone()
        for _ in range(horizon):
            z_next = self.dynamics(z_buffer)  # (B, k)
            z_future.append(z_next)
            z_buffer = torch.cat([z_buffer[:, 1:, :], z_next.unsqueeze(1)], dim=1)

        return torch.stack(z_future, dim=1)


class LAVARWithSupply(nn.Module):
    def __init__(
        self,
        lavar: LAVAR,
        supply_dim: int,
        horizon: int,
        supply_hidden: Optional[Sequence[int]] = (64, 64),
        supply_activation: nn.Module | None = None,
        supply_head_type: Literal["nb", "zinb", "delta_mse"] = "zinb",
    ):
        super().__init__()
        self.lavar = lavar
        self.horizon = horizon
        self.supply_head_type = supply_head_type
        self.delta_nonneg_mode: Literal["clamp", "softplus", "softplus_annealed"] = "clamp"
        self.softplus_beta: float = 1.0

        supply_in_dim = lavar.latent_dim

        if supply_head_type == "nb":
            self.supply_head: Union[SupplyHeadNB, SupplyHeadZINB] = SupplyHeadNB(
                input_dim=supply_in_dim,
                output_dim=supply_dim,
                hidden_dims=supply_hidden,
                activation=supply_activation,
            )
        elif supply_head_type == "zinb":
            self.supply_head = SupplyHeadZINB(
                input_dim=supply_in_dim,
                output_dim=supply_dim,
                hidden_dims=supply_hidden,
                activation=supply_activation,
            )
        elif supply_head_type == "delta_mse":
            # Regression head predicting Δu (same dimensionality as y).
            self.supply_head = SupplyHeadMSE(
                input_dim=supply_in_dim,
                output_dim=supply_dim,
                hidden_dims=supply_hidden,
                activation=supply_activation,
            )
        else:
            raise ValueError(f"Unknown supply_head_type={supply_head_type!r}")

    def set_delta_nonneg_mode(
        self,
        mode: Literal["clamp", "softplus", "softplus_annealed"],
    ) -> None:
        self.delta_nonneg_mode = mode

    def set_softplus_beta(self, beta: float) -> None:
        if beta <= 0:
            raise ValueError(f"softplus beta must be > 0. Got {beta}.")
        self.softplus_beta = float(beta)

    def _project_nonnegative(self, x: torch.Tensor) -> torch.Tensor:
        if self.delta_nonneg_mode == "clamp":
            return x.clamp_min(0.0)

        if self.delta_nonneg_mode in {"softplus", "softplus_annealed"}:
            beta = float(self.softplus_beta)
            return F.softplus(x, beta=beta) / beta

        raise ValueError(f"Unknown delta_nonneg_mode={self.delta_nonneg_mode!r}")

    def forward(
        self,
        x_seq: torch.Tensor,
        y0: Optional[torch.Tensor] = None,
        return_params: bool = False,
        return_delta: bool = False,
    ) -> Union[torch.Tensor, Dict[str, torch.Tensor]]:
        """
        x_seq: (B, p, Dx) or (B, p+1, Dx)
          - If (p+1), the last step is treated as "current" and we use the last p steps
            as VAR history to roll out the future.
        return_params:
          - If False (default), return the mean prediction tensor: (B, H, Dy)
          - If True, return distribution parameters dict for probabilistic heads.
        """
        B, T, D = x_seq.shape
        p = self.lavar.transition_order

        if T == p + 1:
            # Drop the oldest step; keep the most recent p steps ending at "current"
            x_hist = x_seq[:, 1:, :]
        elif T == p:
            x_hist = x_seq
        else:
            raise ValueError(
                f"x_seq must have shape (B, p, D) or (B, p+1, D) with p={p}. Got {x_seq.shape}"
            )

        z_history = self.lavar.encode(x_hist.reshape(-1, D)).reshape(B, p, -1)
        z_future = self.lavar.rollout_latent(z_history, self.horizon)

        # Decode supplies
        B, H, k = z_future.shape
        supply_in = z_future.reshape(B * H, k)

        if self.supply_head_type == "delta_mse":
            if y0 is None:
                raise ValueError("y0 must be provided when supply_head_type='delta_mse'.")
            # Predict Δu for each horizon step.
            delta = self.supply_head(supply_in).reshape(B, H, -1)  # (B, H, Dy)
            if return_delta:
                return {"delta": delta}
            # Raw delta-y integration: y_{t+h} = y_t + cumsum(Δy_{1..h})
            y0_ = y0.to(device=delta.device, dtype=delta.dtype)  # (B, Dy)
            y_raw = y0_.unsqueeze(1) + torch.cumsum(delta, dim=1)
            y_hat = self._project_nonnegative(y_raw)
            return y_hat

        params = self.supply_head(supply_in)  # dict of (B*H, Dy)
        out: Dict[str, torch.Tensor] = {k: v.reshape(B, H, -1) for k, v in params.items()}
        if return_params:
            return out
        # Default point forecast for probabilistic heads is the mean (mu).
        return out["mu"]


class LAVARWithSupplyGRU(nn.Module):
    """GRU-based sequential decoder for Stage 2 delta-MSE supply heads.

    Unlike LAVARWithSupply (pointwise MLP), this passes the full rolled-out
    latent trajectory z_{1:H} through a GRU so each horizon step conditions
    on all prior steps.

    Only supports supply_head_type="delta_mse".  For NB/ZINB heads use
    the original LAVARWithSupply.
    """

    def __init__(
        self,
        lavar: LAVAR,
        supply_dim: int,
        horizon: int,
        gru_hidden_dim: int = 32,
        gru_num_layers: int = 1,
        gru_dropout: float = 0.0,
    ):
        super().__init__()
        self.lavar = lavar
        self.horizon = horizon
        self.supply_head_type = "delta_mse"
        self.delta_nonneg_mode: Literal["clamp", "softplus", "softplus_annealed"] = "clamp"
        self.softplus_beta: float = 1.0

        self.supply_head = SupplyHeadGRU(
            input_dim=lavar.latent_dim,
            output_dim=supply_dim,
            gru_hidden_dim=gru_hidden_dim,
            num_layers=gru_num_layers,
            dropout=gru_dropout,
        )

    def set_delta_nonneg_mode(
        self,
        mode: Literal["clamp", "softplus", "softplus_annealed"],
    ) -> None:
        self.delta_nonneg_mode = mode

    def set_softplus_beta(self, beta: float) -> None:
        if beta <= 0:
            raise ValueError(f"softplus beta must be > 0. Got {beta}.")
        self.softplus_beta = float(beta)

    def _project_nonnegative(self, x: torch.Tensor) -> torch.Tensor:
        if self.delta_nonneg_mode == "clamp":
            return x.clamp_min(0.0)
        if self.delta_nonneg_mode in {"softplus", "softplus_annealed"}:
            beta = float(self.softplus_beta)
            return F.softplus(x, beta=beta) / beta
        raise ValueError(f"Unknown delta_nonneg_mode={self.delta_nonneg_mode!r}")

    def forward(
        self,
        x_seq: torch.Tensor,
        y0: Optional[torch.Tensor] = None,
        return_delta: bool = False,
    ) -> Union[torch.Tensor, Dict[str, torch.Tensor]]:
        """
        x_seq: (B, p, Dx) or (B, p+1, Dx)
        y0:    (B, Dy_sel) — last observed value for delta integration
        """
        B, T, D = x_seq.shape
        p = self.lavar.transition_order

        if T == p + 1:
            x_hist = x_seq[:, 1:, :]
        elif T == p:
            x_hist = x_seq
        else:
            raise ValueError(
                f"x_seq must have shape (B, p, D) or (B, p+1, D) with p={p}. Got {x_seq.shape}"
            )

        z_history = self.lavar.encode(x_hist.reshape(-1, D)).reshape(B, p, -1)
        z_future = self.lavar.rollout_latent(z_history, self.horizon)

        if y0 is None:
            raise ValueError("y0 must be provided for GRU delta-MSE head.")

        # GRU processes full sequence — no flatten
        delta = self.supply_head(z_future)  # (B, H, Dy_sel)
        if return_delta:
            return {"delta": delta}

        y0_ = y0.to(device=delta.device, dtype=delta.dtype)
        y_raw = y0_.unsqueeze(1) + torch.cumsum(delta, dim=1)
        y_hat = self._project_nonnegative(y_raw)
        return y_hat
