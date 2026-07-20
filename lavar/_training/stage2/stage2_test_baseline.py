"""Stage 2 training — baseline mode (latent-rollout only)."""

from __future__ import annotations

from typing import Dict, Optional, Sequence

import torch
from torch.utils.data import DataLoader

from lavar._core.model import LAVAR, LAVARWithSupply, LAVARWithSupplyGRU
from lavar.config import LAVARConfig
from lavar.losses import negative_binomial_nll, zinb_nll


def _slice_targets(y: torch.Tensor, idx: torch.Tensor) -> torch.Tensor:
    # y: (B, H, Dy) -> (B, H, Dy_sel)
    return y.index_select(dim=-1, index=idx)


def _resolve_softplus_beta(cfg: LAVARConfig, epoch: int) -> float:
    mode = getattr(cfg, "stage2_delta_nonneg_mode", "clamp")
    beta_start = float(getattr(cfg, "stage2_softplus_beta_start", 1.0))
    beta_end = float(getattr(cfg, "stage2_softplus_beta_end", 8.0))
    if mode != "softplus_annealed":
        return beta_end

    if cfg.epochs_supply <= 1:
        return beta_end

    progress = float(epoch - 1) / float(cfg.epochs_supply - 1)
    return beta_start + (beta_end - beta_start) * progress


def train_supply_head_indexed(
    lavar: LAVAR,
    train_loader: DataLoader,
    val_loader: DataLoader,
    cfg: LAVARConfig,
    y_indices: Sequence[int],
    head_type: str,
    save_path: Optional[str],
) -> Optional[LAVARWithSupply]:
    """
    Train one supply head on a subset of targets (baseline mode).

    Latent rollout only — no supply-history augmentation.
    """
    y_indices = list(y_indices)
    if len(y_indices) == 0:
        return None

    if head_type not in {"nb", "zinb", "delta_mse"}:
        raise ValueError(f"head_type must be 'nb', 'zinb', or 'delta_mse'. Got {head_type!r}")

    device = torch.device(cfg.device)
    idx = torch.as_tensor(y_indices, dtype=torch.long, device=device)

    # Horizon loss weights: (1, H, 1) for broadcasting against (B, H, Dy_sel)
    hlw_mode = getattr(cfg, "horizon_loss_weight", "uniform")
    if hlw_mode == "linear":
        _w = torch.arange(1, cfg.horizon + 1, dtype=torch.float32)
        _w = _w / _w.mean()  # normalize so mean=1.0
        horizon_weights = _w.reshape(1, cfg.horizon, 1).to(device)
    else:
        horizon_weights = torch.ones(1, cfg.horizon, 1, device=device)

    use_gru = getattr(cfg, "stage2_head_type", "mlp") == "gru" and head_type == "delta_mse"

    if use_gru:
        model = LAVARWithSupplyGRU(
            lavar=lavar,
            supply_dim=len(y_indices),
            horizon=cfg.horizon,
            gru_hidden_dim=cfg.gru_hidden_dim,
            gru_num_layers=cfg.gru_num_layers,
            gru_dropout=cfg.gru_dropout,
        ).to(device)
    else:
        model = LAVARWithSupply(
            lavar=lavar,
            supply_dim=len(y_indices),
            horizon=cfg.horizon,
            supply_hidden=cfg.supply_hidden,
            supply_head_type=head_type,  # type: ignore[arg-type]
        ).to(device)

    # Freeze LAVAR weights; train only supply head.
    for p in model.lavar.parameters():
        p.requires_grad = False
    model.lavar.eval()

    weight_decay = getattr(cfg, "weight_decay_supply", 0.0)
    opt = torch.optim.AdamW(
        model.supply_head.parameters(), lr=cfg.lr_supply, weight_decay=weight_decay
    )

    best_val = float("inf")
    best_state: Optional[Dict[str, torch.Tensor]] = None
    best_epoch = 0
    patience = getattr(cfg, "early_stop_patience_supply", None)
    min_delta = float(getattr(cfg, "min_delta", 0.0))
    stale_epochs = 0

    for epoch in range(1, cfg.epochs_supply + 1):
        if head_type == "delta_mse":
            model.set_delta_nonneg_mode(getattr(cfg, "stage2_delta_nonneg_mode", "clamp"))
            model.set_softplus_beta(_resolve_softplus_beta(cfg, epoch))

        model.train()
        for batch in train_loader:
            if len(batch) == 3:
                x_past, _x_future, y_future = batch  # x_past: (B, p+1, D_in)
                y0 = None
            elif len(batch) == 4:
                x_past, _x_future, y0, y_future = batch  # y0: (B, Dy)
            else:
                raise ValueError(
                    f"baseline training expects batches of 3 or 4 elements. Got {len(batch)}."
                )
            x_past = x_past.to(device)  # (B, p+1, D_in)
            y_future = y_future.to(device)  # (B, H, Dy)
            y_sel = _slice_targets(y_future, idx)  # (B, H, Dy_sel)

            if head_type == "delta_mse":
                if y0 is None:
                    raise ValueError(
                        "Delta-MSE training requires y0. "
                        "Use RollingXYDatasetWithY0 for stage2 loaders."
                    )
                y0 = y0.to(device)  # (B, Dy)
                y0_sel = y0.index_select(dim=-1, index=idx)  # (B, Dy_sel)

                out = model(x_past, y0=y0_sel, return_delta=True)
                if not isinstance(out, dict):
                    raise TypeError("Delta-MSE head must return a parameter dictionary.")
                delta_hat = out["delta"]  # (B, H, Dy_sel)

                # Δy_h = y_{t+h} - y_{t+h-1}
                y_prev = torch.cat([y0_sel.unsqueeze(1), y_sel[:, :-1, :]], dim=1)  # (B, H, Dy_sel)
                delta_true = y_sel - y_prev  # (B, H, Dy_sel)
                loss = torch.mean(horizon_weights * (delta_hat - delta_true) ** 2)
            else:
                params = model(x_past, return_params=True)
                if not isinstance(params, dict):
                    raise TypeError("Probabilistic head must return a parameter dictionary.")
                if head_type == "nb":
                    loss = negative_binomial_nll(params["mu"], params["theta"], y_sel)
                else:
                    loss = zinb_nll(params["pi"], params["mu"], params["theta"], y_sel)

            opt.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.supply_head.parameters(), 1.0)
            opt.step()

        # --- validation ---
        model.eval()
        with torch.no_grad():
            va_loss = 0.0
            vn = 0
            for batch in val_loader:
                if len(batch) == 3:
                    x_past, _x_future, y_future = batch
                    y0 = None
                elif len(batch) == 4:
                    x_past, _x_future, y0, y_future = batch
                else:
                    raise ValueError(
                        f"baseline training expects batches of 3 or 4 elements. Got {len(batch)}."
                    )
                x_past = x_past.to(device)  # (B, p+1, D_in)
                y_future = y_future.to(device)  # (B, H, Dy)
                y_sel = _slice_targets(y_future, idx)  # (B, H, Dy_sel)

                if head_type == "delta_mse":
                    if y0 is None:
                        raise ValueError(
                            "Delta-MSE training requires y0. "
                            "Use RollingXYDatasetWithY0 for stage2 loaders."
                        )
                    y0 = y0.to(device)  # (B, Dy)
                    y0_sel = y0.index_select(dim=-1, index=idx)  # (B, Dy_sel)

                    out = model(x_past, y0=y0_sel, return_delta=True)
                    if not isinstance(out, dict):
                        raise TypeError("Delta-MSE head must return a parameter dictionary.")
                    delta_hat = out["delta"]  # (B, H, Dy_sel)

                    y_prev = torch.cat(
                        [y0_sel.unsqueeze(1), y_sel[:, :-1, :]], dim=1
                    )  # (B, H, Dy_sel)
                    delta_true = y_sel - y_prev  # (B, H, Dy_sel)
                    loss = torch.mean(horizon_weights * (delta_hat - delta_true) ** 2)
                else:
                    params = model(x_past, return_params=True)
                    if not isinstance(params, dict):
                        raise TypeError("Probabilistic head must return a parameter dictionary.")
                    if head_type == "nb":
                        loss = negative_binomial_nll(params["mu"], params["theta"], y_sel)
                    else:
                        loss = zinb_nll(params["pi"], params["mu"], params["theta"], y_sel)

                va_loss += float(loss.item()) * x_past.size(0)
                vn += int(x_past.size(0))

            va_loss /= max(vn, 1)

        if va_loss < (best_val - min_delta):
            best_val = va_loss
            best_epoch = epoch
            stale_epochs = 0
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
            if save_path:
                torch.save(best_state, save_path)
        else:
            stale_epochs += 1

        if patience is not None and stale_epochs >= int(patience):
            print(
                f"[stage2:baseline:{head_type}] Early stopping at epoch={epoch}; "
                f"best_epoch={best_epoch}, best_val={best_val:.6f}"
            )
            break

    if best_state is not None:
        model.load_state_dict(best_state)
        print(
            f"[stage2:baseline:{head_type}] Loaded best checkpoint from "
            f"epoch={best_epoch}, val={best_val:.6f}"
        )

    return model
