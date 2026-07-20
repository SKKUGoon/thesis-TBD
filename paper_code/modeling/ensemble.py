from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import torch

from paper_code.config import BestModelSpec, load_best_model_spec
from paper_code.evaluation.metrics import compute_seed_fold_metrics, summarize_seed_metrics
from paper_code.modeling.checkpoints import (
    list_stage2_checkpoints,
    pick_checkpoint_for_t_end,
    rebuild_forecaster,
)
from paper_code.paths import PackagePaths


def build_run_plan(spec: BestModelSpec) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "scenario": scenario,
            "seed": seed,
            "run_id": f"RUN_{scenario}_SEED{seed}",
        }
        for seed in spec.seeds
        for scenario in spec.scenarios
    )


def reconstruct_component_predictions(
    spec: BestModelSpec,
    paths: PackagePaths,
    device: str = "cpu",
) -> pd.DataFrame:
    """Reconstruct every rolling-fold prediction from packaged checkpoints."""
    data_dir = paths.processed(spec.hospital)
    x_frame = pd.read_parquet(data_dir / "lavar_ready_x.parquet")
    y_frame = pd.read_parquet(data_dir / "lavar_ready_y.parquet")
    x_tensor = torch.tensor(x_frame.values, dtype=torch.float32)
    y_tensor = torch.tensor(y_frame.values, dtype=torch.float32)
    rows: list[dict] = []

    for run in build_run_plan(spec).itertuples(index=False):
        fold_path = paths.run_metadata(spec.hospital) / "runs" / run.run_id / "fold_metrics.csv"
        folds = pd.read_csv(fold_path).sort_values("fold_id").reset_index(drop=True)
        checkpoints = list_stage2_checkpoints(paths.stage2(spec.hospital), run.run_id)
        for fold in folds.itertuples(index=False):
            fold_id = int(fold.fold_id)
            t_end = int(fold.t_end)
            checkpoint = pick_checkpoint_for_t_end(checkpoints, t_end)
            fit_t_end = int(checkpoint.fit_t_end)
            payload = torch.load(checkpoint.ckpt_path, map_location="cpu", weights_only=True)
            forecaster = rebuild_forecaster(
                payload,
                paths.stage1(spec.hospital),
                x_tensor[:fit_t_end],
                y_tensor[:fit_t_end],
                force_device=device,
            )
            history = int(forecaster.cfg.dyn_p)
            horizon = int(forecaster.cfg.horizon)
            y_true = y_tensor[t_end : t_end + horizon].numpy()
            y_pred = forecaster.predict(
                x_tensor[t_end - history : t_end],
                y_recent=y_tensor[t_end - history : t_end],
            )
            for horizon_index in range(horizon):
                absolute_t = t_end + horizon_index
                for target_index, target in enumerate(y_frame.columns):
                    rows.append(
                        {
                            "scenario": run.scenario,
                            "seed": int(run.seed),
                            "run_id": run.run_id,
                            "fold_id": fold_id,
                            "t_end": t_end,
                            "h": horizon_index + 1,
                            "abs_t": absolute_t,
                            "target": str(target),
                            "target_idx": int(target_index),
                            "y_true": float(y_true[horizon_index, target_index]),
                            "y_pred": float(y_pred[horizon_index, target_index]),
                        }
                    )
    return pd.DataFrame(rows)


def blend_component_predictions(predictions: pd.DataFrame, weight_s11: float) -> pd.DataFrame:
    if not 0.0 <= weight_s11 <= 1.0:
        raise ValueError(f"weight_s11 must be in [0, 1]. Got {weight_s11}.")
    columns = ["seed", "fold_id", "t_end", "h", "abs_t", "target_idx", "target", "y_true", "y_pred"]
    s11 = predictions.loc[predictions["scenario"] == "S11_LATENT16", columns].rename(
        columns={"y_true": "y_true_s11", "y_pred": "yhat_s11"},
    )
    s04 = predictions.loc[predictions["scenario"] == "S04_GRU_H32_L1", columns].rename(
        columns={"y_true": "y_true_s04", "y_pred": "yhat_s04"},
    )
    keys = ["seed", "fold_id", "t_end", "h", "abs_t", "target_idx", "target"]
    for scenario, frame in (("S11", s11), ("S04", s04)):
        if frame.empty:
            raise ValueError(f"No {scenario} component predictions were provided.")
        if frame.duplicated(keys).any():
            raise ValueError(f"{scenario} component predictions contain duplicate forecast keys.")
    merged = s11.merge(
        s04,
        on=keys,
        how="outer",
        validate="one_to_one",
        indicator=True,
    )
    unmatched = merged["_merge"] != "both"
    if unmatched.any():
        raise ValueError(
            f"S11 and S04 prediction keys do not align: {int(unmatched.sum())} unmatched rows."
        )
    merged = merged.drop(columns="_merge")
    if not np.allclose(merged["y_true_s11"].values, merged["y_true_s04"].values):
        raise ValueError("S11 and S04 ground-truth values do not match")
    merged["y_true"] = merged["y_true_s11"]
    merged["y_blend"] = weight_s11 * merged["yhat_s11"] + (1.0 - weight_s11) * merged["yhat_s04"]
    for column in ("yhat_s11", "yhat_s04", "y_blend"):
        merged[column] = np.clip(merged[column].astype(float), 0.0, np.inf)
    return merged


def load_naive_metrics(spec: BestModelSpec, paths: PackagePaths) -> pd.DataFrame:
    rows = []
    for seed in spec.seeds:
        run_id = f"RUN_S11_LATENT16_SEED{seed}"
        fold_path = paths.run_metadata(spec.hospital) / "runs" / run_id / "fold_metrics.csv"
        rows.append(pd.read_csv(fold_path)[["fold_id", "t_end", "naive_mse"]].assign(seed=seed))
    return pd.concat(rows, ignore_index=True)


def calculate_metric_tables(
    blended: pd.DataFrame,
    spec: BestModelSpec,
    paths: PackagePaths,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    naive = load_naive_metrics(spec, paths)
    blend_folds = compute_seed_fold_metrics(blended, "y_blend", naive)
    s11_folds = compute_seed_fold_metrics(blended, "yhat_s11", naive)
    s04_folds = compute_seed_fold_metrics(blended, "yhat_s04", naive)
    by_seed = pd.concat(
        [
            summarize_seed_metrics(s11_folds, "pure_S11"),
            summarize_seed_metrics(s04_folds, "pure_S04"),
            summarize_seed_metrics(blend_folds, f"blend_w{spec.ensemble_weight_s11:.2f}"),
        ],
        ignore_index=True,
    )
    overall = by_seed.groupby("label", as_index=False).agg(
        seeds=("seed", "count"),
        mean_of_mean_mse=("mean_mse", "mean"),
        std_of_mean_mse=("mean_mse", "std"),
        mean_of_mean_mae=("mean_mae", "mean"),
        mean_of_mean_skill=("mean_skill", "mean"),
        mean_of_p95_fold_mse=("p95_fold_mse", "mean"),
        mean_of_max_fold_mse=("max_fold_mse", "mean"),
    )
    return by_seed, overall


def calculate_confidence_intervals(blended: pd.DataFrame) -> pd.DataFrame:
    result = blended.groupby(
        ["fold_id", "t_end", "h", "abs_t", "target_idx", "target"],
        as_index=False,
    ).agg(
        y_true=("y_true", "mean"),
        y_pred_ens=("y_blend", "mean"),
        y_pred_q025=("y_blend", lambda values: float(np.quantile(values, 0.025))),
        y_pred_q975=("y_blend", lambda values: float(np.quantile(values, 0.975))),
    )
    for column in ("y_true", "y_pred_ens", "y_pred_q025", "y_pred_q975"):
        result[column] = np.clip(result[column].astype(float), 0.0, np.inf)
    result["y_pred_q025"] = np.minimum(result["y_pred_q025"], result["y_pred_q975"])
    return result


def plot_confidence_intervals(ci_frame: pd.DataFrame, output_path: Path, top_k: int = 12) -> None:
    targets = (
        ci_frame.groupby("target", as_index=False)["y_true"]
        .mean()
        .sort_values("y_true", ascending=False)["target"]
        .head(top_k)
        .tolist()
    )
    plot_data = ci_frame[ci_frame["target"].isin(targets)]
    columns = 3
    rows = int(np.ceil(len(targets) / columns))
    figure, axes = plt.subplots(rows, columns, figsize=(18, 4 * rows), sharex=False)
    axes = np.array(axes).reshape(-1)
    for index, target in enumerate(targets):
        axis = axes[index]
        data = plot_data[plot_data["target"] == target].sort_values("abs_t")
        x = data["abs_t"].to_numpy()
        axis.plot(x, data["y_true"], color="#111827", linewidth=1.5, label="True")
        axis.plot(x, data["y_pred_ens"], color="#2563eb", linewidth=1.5, label="Ensemble Pred")
        axis.fill_between(
            x, data["y_pred_q025"], data["y_pred_q975"], color="#93c5fd", alpha=0.35, label="95% CI"
        )
        axis.set_title(str(target))
        axis.set_ylim(bottom=0.0)
        axis.grid(alpha=0.25)
    for index in range(len(targets), len(axes)):
        axes[index].axis("off")
    handles, labels = axes[0].get_legend_handles_labels()
    figure.legend(handles, labels, loc="upper center", ncol=3, frameon=False)
    figure.tight_layout(rect=[0, 0, 1, 0.97])
    figure.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(figure)


def reproduce_ensemble_results(
    hospital: str,
    root: Path | None = None,
    reconstruct_predictions: bool = False,
    device: str = "cpu",
) -> dict[str, Path]:
    """Recreate metric, CI, figure, and summary artifacts for one hospital.

    Existing packaged seed predictions are reused by default. Set
    ``reconstruct_predictions=True`` to rebuild them from every rolling model
    checkpoint; this is computationally and memory intensive for Seosong.
    """
    paths = PackagePaths.from_root(root)
    spec = load_best_model_spec(hospital, paths.root)
    output_dir = paths.final_results(hospital)
    output_dir.mkdir(parents=True, exist_ok=True)
    prediction_path = output_dir / "ensemble_seed_predictions_per_drug.csv"

    if reconstruct_predictions or not prediction_path.is_file():
        components = reconstruct_component_predictions(spec, paths, device=device)
        blended = blend_component_predictions(components, spec.ensemble_weight_s11)
        blended.to_csv(prediction_path, index=False)
    else:
        blended = pd.read_csv(prediction_path)

    by_seed, overall = calculate_metric_tables(blended, spec, paths)
    by_seed_path = output_dir / "ensemble_metrics_by_seed.csv"
    overall_path = output_dir / "ensemble_metrics_overall.csv"
    by_seed.to_csv(by_seed_path, index=False)
    overall.to_csv(overall_path, index=False)

    ci = calculate_confidence_intervals(blended)
    ci_path = output_dir / "ensemble_ci_per_drug.csv"
    ci.to_csv(ci_path, index=False)
    figure_path = output_dir / "ensemble_true_vs_pred_ci_per_drug.png"
    plot_confidence_intervals(ci, figure_path)

    blend_label = f"blend_w{spec.ensemble_weight_s11:.2f}"
    blend = overall.loc[overall["label"] == blend_label].iloc[0]
    s11 = overall.loc[overall["label"] == "pure_S11"].iloc[0]
    s04 = overall.loc[overall["label"] == "pure_S04"].iloc[0]
    delta = float(blend["mean_of_mean_mse"] - s11["mean_of_mean_mse"])
    relative = delta / float(s11["mean_of_mean_mse"]) * 100.0
    s04_row = (
        f"| S04 | {float(s04['mean_of_mean_mse']):.4f} | "
        f"{float(s04['std_of_mean_mse']):.4f} | {float(s04['mean_of_mean_mae']):.4f} | "
        f"{float(s04['mean_of_mean_skill']):.4f} | "
        f"{float(s04['mean_of_p95_fold_mse']):.4f} | "
        f"{float(s04['mean_of_max_fold_mse']):.4f} |"
    )
    s11_row = (
        f"| S11 | {float(s11['mean_of_mean_mse']):.4f} | "
        f"{float(s11['std_of_mean_mse']):.4f} | {float(s11['mean_of_mean_mae']):.4f} | "
        f"{float(s11['mean_of_mean_skill']):.4f} | "
        f"{float(s11['mean_of_p95_fold_mse']):.4f} | "
        f"{float(s11['mean_of_max_fold_mse']):.4f} |"
    )
    blend_row = (
        f"| **Ensemble** | **{float(blend['mean_of_mean_mse']):.4f}** | "
        f"**{float(blend['std_of_mean_mse']):.4f}** | "
        f"**{float(blend['mean_of_mean_mae']):.4f}** | "
        f"**{float(blend['mean_of_mean_skill']):.4f}** | "
        f"{float(blend['mean_of_p95_fold_mse']):.4f} | "
        f"**{float(blend['mean_of_max_fold_mse']):.4f}** |"
    )
    summary_path = output_dir / "ensemble_summary.md"
    summary_path.write_text(
        "\n".join(
            [
                f"# Ensemble Result Summary — {hospital.title()}",
                "",
                "This summary covers the fixed S11/S04 ensemble evaluated in the paper package.",
                "",
                "## Setup",
                "",
                f"- Seeds: {list(spec.seeds)}",
                f"- Ensemble: `{spec.ensemble_weight_s11:.2f} × S11 + "
                f"{spec.ensemble_weight_s04:.2f} × S04`",
                f"- Horizon: {spec.horizon} days",
                "- Prediction constraint: nonnegative",
                "- Displayed interval: empirical 2.5%–97.5% quantiles across "
                "five seeded predictions",
                "",
                "## Cross-seed metrics",
                "",
                "| Model | Mean MSE | Seed SD | Mean MAE | Mean skill | "
                "Mean p95 fold MSE | Mean maximum fold MSE |",
                "|---|---:|---:|---:|---:|---:|---:|",
                s04_row,
                s11_row,
                blend_row,
                "",
                f"The ensemble mean-MSE change relative to S11 is {delta:.4f} ({relative:+.2f}%).",
                "Negative skill means the model remains worse than the last-value "
                "naive baseline on average.",
                "Seed quantiles describe initialization sensitivity; they are not "
                "calibrated prediction intervals.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return {
        "predictions": prediction_path,
        "metrics_by_seed": by_seed_path,
        "metrics_overall": overall_path,
        "confidence_intervals": ci_path,
        "figure": figure_path,
        "summary": summary_path,
    }
