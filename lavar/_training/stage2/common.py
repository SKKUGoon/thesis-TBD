"""Stage 2 shared utilities — density computation and index splitting."""

from __future__ import annotations

from typing import List, Optional, Tuple

import torch
from torch.utils.data import DataLoader


@torch.no_grad()
def compute_nonzero_rate_from_loader(train_loader: DataLoader) -> torch.Tensor:
    """
    Compute per-target nonzero rate from a train DataLoader.

    Expected batch: (x_past, x_future, y_future) where y_future has shape (B, H, Dy).
    Returns: nonzero_rate with shape (Dy,), on CPU.
    """
    nonzero_sum: Optional[torch.Tensor] = None
    count_sum: int = 0

    for batch in train_loader:
        if len(batch) == 3:
            _x_past, _x_future, y_future = batch
        elif len(batch) == 4:
            _x_past, _x_future, _y0, y_future = batch
        else:
            raise ValueError(
                "Density split training expects batches of (x_past, x_future, y_future). "
                f"Got batch size {len(batch)}."
            )
        # y_future: (B, H, Dy)
        y_flat = y_future.reshape(-1, y_future.shape[-1])  # (B*H, Dy)
        nz = (y_flat > 0).to(dtype=torch.float32).sum(dim=0)  # (Dy,)
        if nonzero_sum is None:
            nonzero_sum = nz.detach().cpu()
        else:
            nonzero_sum += nz.detach().cpu()
        count_sum += int(y_flat.shape[0])

    if nonzero_sum is None or count_sum == 0:
        raise RuntimeError("train_loader yielded no batches; cannot compute nonzero rates.")

    return nonzero_sum / float(count_sum)  # (Dy,)


def split_indices_by_density(
    nonzero_rate: torch.Tensor,
    dense_thr: float,
    ultra_thr: float,
) -> Tuple[List[int], List[int], List[int]]:
    """
    Split Dy indices into dense/sparse/ultra-sparse based on nonzero_rate thresholds.
    """
    if nonzero_rate.dim() != 1:
        raise ValueError(f"nonzero_rate must have shape (Dy,). Got {tuple(nonzero_rate.shape)}")
    if not (0.0 <= ultra_thr <= dense_thr <= 1.0):
        raise ValueError(
            f"Expected 0 <= ultra_thr <= dense_thr <= 1. Got ultra={ultra_thr}, dense={dense_thr}"
        )

    dense_mask = nonzero_rate >= dense_thr
    ultra_mask = nonzero_rate <= ultra_thr
    sparse_mask = (~dense_mask) & (~ultra_mask)

    dense_idx = dense_mask.nonzero(as_tuple=False).squeeze(-1).tolist()
    sparse_idx = sparse_mask.nonzero(as_tuple=False).squeeze(-1).tolist()
    ultra_idx = ultra_mask.nonzero(as_tuple=False).squeeze(-1).tolist()
    return dense_idx, sparse_idx, ultra_idx
