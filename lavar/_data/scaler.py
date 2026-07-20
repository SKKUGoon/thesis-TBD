from __future__ import annotations

from typing import Optional

import torch


# Simple scalers (fit on train only)
class StandardScalerTorch:
    def __init__(self, eps: float = 1e-6):
        self.eps = eps
        self.mean: Optional[torch.Tensor] = None
        self.std: Optional[torch.Tensor] = None

    def fit(self, x: torch.Tensor) -> "StandardScalerTorch":
        if x.ndim != 2:
            raise ValueError(f"Scaler input must be two-dimensional. Got {x.shape}.")
        if x.shape[0] == 0:
            raise ValueError("Scaler input must not be empty.")
        if not torch.isfinite(x).all():
            raise ValueError("Scaler input contains non-finite values.")
        self.mean = x.mean(dim=0, keepdim=True)
        self.std = x.std(dim=0, keepdim=True).clamp_min(self.eps)
        return self

    def transform(self, x: torch.Tensor) -> torch.Tensor:
        if self.mean is None or self.std is None:
            raise RuntimeError("StandardScalerTorch must be fitted before transform().")
        return (x - self.mean) / self.std

    def inverse_transform(self, x: torch.Tensor) -> torch.Tensor:
        if self.mean is None or self.std is None:
            raise RuntimeError("StandardScalerTorch must be fitted before inverse_transform().")
        return x * self.std + self.mean
