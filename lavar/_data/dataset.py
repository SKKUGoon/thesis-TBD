from __future__ import annotations

from typing import Tuple

import torch
from torch.utils.data import Dataset


# Dataset: returns x_past, x_future(features), y_future(supply)
class RollingXYDataset(Dataset):
    """
    x: (T, Dx)  non-supply features
    y: (T, Dy)  supply features
    Each sample:
      x_past   = x[t-p : t+1]            -> (p+1, Dx)  (VAR(p) history + current)
      x_future = x[t+1 : t+H+1]          -> (H, Dx)  (optional supervision for latent rollout)
      y_future = y[t+1 : t+H+1]          -> (H, Dy)
    """

    def __init__(
        self,
        x: torch.Tensor,
        y: torch.Tensor,
        p: int,
        horizon: int,
        use_supply_history: bool = False,
    ):
        if len(x) != len(y):
            raise ValueError(f"Feature and target lengths must match: {len(x)} != {len(y)}")
        if p < 1:
            raise ValueError(f"p must be positive. Got {p}.")
        if horizon < 1:
            raise ValueError(f"horizon must be positive. Got {horizon}.")
        if len(x) <= p + horizon:
            raise ValueError(
                f"Dataset must contain more rows than p + horizon: {len(x)} <= {p + horizon}"
            )
        self.x = x
        self.y = y
        self.p = p
        self.h = horizon
        self.use_supply_history = use_supply_history

    def __len__(self) -> int:
        return len(self.x) - self.p - self.h

    def __getitem__(self, idx: int) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        t = idx + self.p
        # Provide VAR(p) history + current => (p+1, Dx)
        x_past = self.x[t - self.p : t + 1]  # (p+1, Dx)
        x_future = self.x[t + 1 : t + self.h + 1]  # (H, Dx)
        if self.use_supply_history:
            y_past = self.y[t - self.p : t + 1]  # (p+1, Dy)
            x_past = torch.cat([x_past, y_past], dim=-1)  # (p+1, Dx+Dy)
            y_future_sup = self.y[t + 1 : t + self.h + 1]  # (H, Dy)
            x_future = torch.cat([x_future, y_future_sup], dim=-1)  # (H, Dx+Dy)
        y_future = self.y[t + 1 : t + self.h + 1]  # (H, Dy)
        return x_past, x_future, y_future


class RollingXYDatasetWithY0(Dataset):
    """
    Stage-2 dataset variant for *increment* targets.

    Each sample:
      - x_past:   x[t-p : t+1]      -> (p+1, Dx)
      - x_future: x[t+1 : t+H+1]    -> (H, Dx)
      - y0:       y[t]              -> (Dy,)      (current / last observed)
      - y_future: y[t+1 : t+H+1]    -> (H, Dy)
    """

    def __init__(
        self,
        x: torch.Tensor,
        y: torch.Tensor,
        p: int,
        horizon: int,
        use_supply_history: bool = False,
    ):
        if len(x) != len(y):
            raise ValueError(f"Feature and target lengths must match: {len(x)} != {len(y)}")
        if p < 1:
            raise ValueError(f"p must be positive. Got {p}.")
        if horizon < 1:
            raise ValueError(f"horizon must be positive. Got {horizon}.")
        if len(x) <= p + horizon:
            raise ValueError(
                f"Dataset must contain more rows than p + horizon: {len(x)} <= {p + horizon}"
            )
        self.x = x
        self.y = y
        self.p = p
        self.h = horizon
        self.use_supply_history = use_supply_history

    def __len__(self) -> int:
        return len(self.x) - self.p - self.h

    def __getitem__(
        self, idx: int
    ) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
        t = idx + self.p
        x_past = self.x[t - self.p : t + 1]  # (p+1, Dx)
        x_future = self.x[t + 1 : t + self.h + 1]  # (H, Dx)
        if self.use_supply_history:
            y_past = self.y[t - self.p : t + 1]  # (p+1, Dy)
            x_past = torch.cat([x_past, y_past], dim=-1)  # (p+1, Dx+Dy)
            y_future_sup = self.y[t + 1 : t + self.h + 1]  # (H, Dy)
            x_future = torch.cat([x_future, y_future_sup], dim=-1)  # (H, Dx+Dy)
        y0 = self.y[t]  # (Dy,)
        y_future = self.y[t + 1 : t + self.h + 1]  # (H, Dy)
        return x_past, x_future, y0, y_future
