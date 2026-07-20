from __future__ import annotations

from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.figure import Figure

from paper_code.evaluation.metrics import select_targets_with_highest_mean_ground_truth
from paper_code.paths import PackagePaths


def plot_report_figure(hospital: str, root: Path | None = None, top_k: int = 6) -> Figure:
    paths = PackagePaths.from_root(root)
    data = pd.read_csv(paths.final_results(hospital) / "ensemble_ci_per_drug.csv")
    if "forecast_date" not in data.columns:
        raise ValueError(
            "ensemble_ci_per_drug.csv must contain forecast_date; "
            "reproduce the hospital results before plotting"
        )
    targets = select_targets_with_highest_mean_ground_truth(data, top_k)
    figure, axes = plt.subplots(len(targets), 1, figsize=(13, 3 * len(targets)), squeeze=False)
    for axis, target in zip(axes[:, 0], targets):
        subset = data[data["target"] == target].sort_values("abs_t")
        forecast_dates = pd.to_datetime(subset["forecast_date"], errors="raise")
        axis.plot(forecast_dates, subset["y_true"], color="#111827", label="True")
        axis.plot(
            forecast_dates,
            subset["y_pred_ens"],
            color="#2563eb",
            label="Prediction",
        )
        axis.fill_between(
            forecast_dates,
            subset["y_pred_q025"],
            subset["y_pred_q975"],
            color="#93c5fd",
            alpha=0.35,
            label="95% CI",
        )
        axis.set_title(str(target))
        axis.set_ylim(bottom=0)
        axis.grid(alpha=0.25)
        date_locator = mdates.AutoDateLocator(minticks=3, maxticks=8)
        axis.xaxis.set_major_locator(date_locator)
        axis.xaxis.set_major_formatter(mdates.ConciseDateFormatter(date_locator))
        axis.set_xlabel("Forecast date")
    axes[0, 0].legend(ncol=3, frameon=False)
    figure.suptitle(f"{hospital.title()}: selected ensemble predictions")
    figure.tight_layout()
    return figure
