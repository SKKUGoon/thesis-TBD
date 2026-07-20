from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.figure import Figure

from paper_code.paths import PackagePaths


def plot_report_figure(hospital: str, root: Path | None = None, top_k: int = 6) -> Figure:
    paths = PackagePaths.from_root(root)
    data = pd.read_csv(paths.final_results(hospital) / "ensemble_ci_per_drug.csv")
    targets = (
        data.groupby("target", as_index=False)["y_true"]
        .mean()
        .sort_values("y_true", ascending=False)["target"]
        .head(top_k)
        .tolist()
    )
    figure, axes = plt.subplots(len(targets), 1, figsize=(13, 3 * len(targets)), squeeze=False)
    for axis, target in zip(axes[:, 0], targets):
        subset = data[data["target"] == target].sort_values("abs_t")
        axis.plot(subset["abs_t"], subset["y_true"], color="#111827", label="True")
        axis.plot(subset["abs_t"], subset["y_pred_ens"], color="#2563eb", label="Prediction")
        axis.fill_between(
            subset["abs_t"],
            subset["y_pred_q025"],
            subset["y_pred_q975"],
            color="#93c5fd",
            alpha=0.35,
            label="95% CI",
        )
        axis.set_title(str(target))
        axis.set_ylim(bottom=0)
        axis.grid(alpha=0.25)
    axes[0, 0].legend(ncol=3, frameon=False)
    figure.suptitle(f"{hospital.title()}: selected ensemble predictions")
    figure.tight_layout()
    return figure
