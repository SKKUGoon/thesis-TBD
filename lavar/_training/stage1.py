from __future__ import annotations

# Stage 1: Train LAVAR
# This is the core. Trains only the latent state model.
# No supplies are involved
# Its goal is to learn
# Meaningful latent state z_t
# Linear latent dynamics z_t = A z_{t-1:t-p}. p is the latent history length
# Decoder that prevents latent collapse
from pathlib import Path

import torch
from torch.utils.data import DataLoader

from lavar._core.model import LAVAR
from lavar.config import LAVARConfig


def train_lavar(
    model: LAVAR,
    train_loader: DataLoader,
    val_loader: DataLoader,
    cfg: LAVARConfig,
    save_path: Path | str | None = "lavar_best.pth",
) -> None:
    device = torch.device(cfg.device)
    model.to(device)

    if model.transition_order != cfg.dyn_p:
        raise ValueError(
            "Model transition order must match cfg.dyn_p before training: "
            f"{model.transition_order} != {cfg.dyn_p}."
        )

    opt = torch.optim.Adam(model.parameters(), lr=cfg.lr_lavar)

    # Training start
    best_val = float("inf")
    best_state = None
    best_epoch = 0
    patience = getattr(cfg, "early_stop_patience_lavar", None)
    min_delta = float(getattr(cfg, "min_delta", 0.0))
    stale_epochs = 0

    for epoch in range(1, cfg.epochs_lavar + 1):
        model.train()
        tr_loss, n = 0.0, 0

        for x_past, x_future, _y_future in train_loader:
            # x_past: (B, p+1, Dx) where the last step is "current" and previous p are history
            x_past = x_past.to(device)
            x_future = x_future.to(device)  # (B, horizon, Dx)

            x_seq = x_past
            out = model(x_seq)
            x_hat = out["x_hat"]  # (B, Dx)
            z_pred = out["z_pred"]  # (B, k latent dimension)
            z_true = out["z_true"]  # (B, k latent dimension)

            reconstruct = torch.mean(
                (x_hat - x_seq[:, -1, :]) ** 2
            )  # Autoencoder reconstruction loss
            dynamics = torch.mean((z_pred - z_true) ** 2)  # Latent dynamic loss

            loss = cfg.lambda_recon * reconstruct + cfg.lambda_dyn * dynamics

            # TODO: Multi step latent supervision
            if cfg.multi_step_latent_supervision:
                B, _, Dx = x_seq.shape
                z_hist = model.encode(x_seq[:, :-1, :].reshape(-1, Dx)).reshape(
                    B, cfg.dyn_p, -1
                )  # (B, p, k)
                z_roll = model.rollout_latent(z_hist, cfg.horizon)  # (B, H, k)
                z_fut_true = model.encode(x_future.reshape(-1, Dx)).reshape(
                    B, cfg.horizon, -1
                )  # (B, H, k)
                loss += cfg.lambda_dyn * torch.mean((z_roll - z_fut_true) ** 2)

                # Optional: also supervise in observation space by decoding rolled-out latents.
                # This helps when latent matching alone is not enough to keep rollouts meaningful.
                if getattr(cfg, "multi_step_x_reconstruction", False):
                    B, H, k = z_roll.shape
                    x_roll = model.decode(z_roll.reshape(B * H, k)).reshape(B, H, -1)  # (B, H, Dx)
                    loss += float(getattr(cfg, "lambda_future_recon", 1.0)) * torch.mean(
                        (x_roll - x_future) ** 2
                    )

            opt.zero_grad()  # What does this do?
            loss.backward()  # Backpropagate the loss
            torch.nn.utils.clip_grad_norm_(
                model.parameters(), 1.0
            )  # Clip gradients to prevent exploding gradients
            opt.step()

            tr_loss += loss.item() * x_past.size(0)
            n += x_past.size(0)

        tr_loss /= max(n, 1)

        model.eval()
        with torch.no_grad():
            va_loss = 0.0
            vn = 0
            for x_past, x_future, _y_future in val_loader:
                x_past = x_past.to(device)
                x_future = x_future.to(device)

                out = model(x_past)
                reconstruct = torch.mean((out["x_hat"] - x_past[:, -1, :]) ** 2)
                dynamics = torch.mean((out["z_pred"] - out["z_true"]) ** 2)
                loss = cfg.lambda_recon * reconstruct + cfg.lambda_dyn * dynamics

                if cfg.multi_step_latent_supervision:
                    B, _, Dx = x_past.shape
                    z_hist = model.encode(x_past[:, :-1, :].reshape(-1, Dx)).reshape(
                        B, cfg.dyn_p, -1
                    )
                    z_roll = model.rollout_latent(z_hist, cfg.horizon)
                    z_fut_true = model.encode(x_future.reshape(-1, Dx)).reshape(B, cfg.horizon, -1)
                    loss += cfg.lambda_dyn * torch.mean((z_roll - z_fut_true) ** 2)

                    if getattr(cfg, "multi_step_x_reconstruction", False):
                        B, H, k = z_roll.shape
                        x_roll = model.decode(z_roll.reshape(B * H, k)).reshape(B, H, -1)
                        loss += float(getattr(cfg, "lambda_future_recon", 1.0)) * torch.mean(
                            (x_roll - x_future) ** 2
                        )

                va_loss += loss.item() * x_past.size(0)
                vn += x_past.size(0)

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
                f"[stage1] Early stopping at epoch={epoch}; "
                f"best_epoch={best_epoch}, best_val={best_val:.6f}"
            )
            break

    if best_state is not None:
        model.load_state_dict(best_state)
        print(f"[stage1] Loaded best checkpoint from epoch={best_epoch}, val={best_val:.6f}")
