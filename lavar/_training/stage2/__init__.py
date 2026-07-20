"""Stage 2 dispatcher — density-split supply-head training.

Shared utilities live in common.py; mode-specific training loops live in:
  - stage2_test_baseline.py              (stage2_mode="baseline", default)
  - stage2_test_supply_history_latent.py (stage2_mode="supply_history_latent")
"""

from __future__ import annotations

from typing import Dict, Optional

from torch.utils.data import DataLoader

from lavar._core.model import LAVAR
from lavar._training.stage2.common import (
    compute_nonzero_rate_from_loader,
    split_indices_by_density,
)
from lavar._training.stage2.stage2_test_baseline import (
    train_supply_head_indexed as _train_head_baseline,
)
from lavar._training.stage2.stage2_test_supply_history_latent import (
    train_supply_head_indexed as _train_head_supply_history_latent,
)
from lavar.config import LAVARConfig

# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

_MODE_DISPATCH = {
    "baseline": _train_head_baseline,
    "supply_history_latent": _train_head_supply_history_latent,
}


def train_supply_heads(
    lavar: LAVAR,
    train_loader: DataLoader,
    val_loader: DataLoader,
    cfg: LAVARConfig,
    save_dense: Optional[str] = "lavar_supply_dense_best.pth",
    save_sparse: Optional[str] = "lavar_supply_sparse_best.pth",
    save_ultra: Optional[str] = "lavar_supply_ultra_best.pth",
) -> Dict[str, object]:
    """
    Stage 2: train three supply heads split by value density (nonzero rate).

      - dense bucket  -> delta-MSE head
      - sparse bucket -> delta-MSE head
      - ultra-sparse  -> ZINB head

    Dispatches to the mode-specific training loop based on cfg.stage2_mode.
    """
    mode = cfg.stage2_mode
    if mode not in _MODE_DISPATCH:
        raise ValueError(f"Unknown stage2_mode={mode!r}. Expected one of {sorted(_MODE_DISPATCH)}.")

    _train_head = _MODE_DISPATCH[mode]

    # --- density split ---
    nonzero_rate = compute_nonzero_rate_from_loader(train_loader)
    dense_thr = float(cfg.dense_nonzero_rate_thr)
    ultra_thr = float(cfg.ultra_nonzero_rate_thr)
    dense_idx, sparse_idx, ultra_idx = split_indices_by_density(
        nonzero_rate, dense_thr=dense_thr, ultra_thr=ultra_thr
    )

    out: Dict[str, object] = {
        "dense_indices": dense_idx,
        "sparse_indices": sparse_idx,
        "ultra_indices": ultra_idx,
        "dense_model": None,
        "sparse_model": None,
        "ultra_model": None,
    }

    out["dense_model"] = _train_head(
        lavar=lavar,
        train_loader=train_loader,
        val_loader=val_loader,
        cfg=cfg,
        y_indices=dense_idx,
        head_type="delta_mse",
        save_path=save_dense,
    )
    out["sparse_model"] = _train_head(
        lavar=lavar,
        train_loader=train_loader,
        val_loader=val_loader,
        cfg=cfg,
        y_indices=sparse_idx,
        head_type="delta_mse",
        save_path=save_sparse,
    )
    out["ultra_model"] = _train_head(
        lavar=lavar,
        train_loader=train_loader,
        val_loader=val_loader,
        cfg=cfg,
        y_indices=ultra_idx,
        head_type="zinb",
        save_path=save_ultra,
    )

    return out
