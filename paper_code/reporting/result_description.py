from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from paper_code.config import BestModelSpec, load_best_model_spec
from paper_code.evaluation.metrics import select_targets_with_highest_mean_ground_truth
from paper_code.paths import PackagePaths

PREDICTION_COLUMNS = (
    "seed",
    "fold_id",
    "target_idx",
    "target",
    "y_true",
    "y_blend",
)


def calculate_drug_metrics_from_seed_predictions(
    prediction_path: Path,
    seeds: tuple[int, ...],
    *,
    chunksize: int = 500_000,
) -> pd.DataFrame:
    if chunksize < 1:
        raise ValueError(f"chunksize must be positive. Got {chunksize}.")
    if not seeds:
        raise ValueError("seeds must not be empty")

    metric_chunks: list[pd.DataFrame] = []
    observed_seeds: set[int] = set()
    for prediction_chunk in pd.read_csv(
        prediction_path,
        usecols=list(PREDICTION_COLUMNS),
        dtype={"target": str},
        keep_default_na=False,
        chunksize=chunksize,
    ):
        numeric_values = prediction_chunk[["y_true", "y_blend"]].to_numpy(dtype=float)
        if not np.isfinite(numeric_values).all():
            raise ValueError(f"Non-finite predictions or ground truth in {prediction_path}")
        observed_seeds.update(int(seed) for seed in prediction_chunk["seed"].unique())
        prediction_error = prediction_chunk["y_blend"] - prediction_chunk["y_true"]
        prediction_chunk["squared_error"] = prediction_error**2
        prediction_chunk["absolute_error"] = prediction_error.abs()
        prediction_chunk["ground_truth_sum"] = prediction_chunk["y_true"]
        metric_chunks.append(
            prediction_chunk.groupby(["target_idx", "target"], as_index=False).agg(
                seed_prediction_rows=("squared_error", "size"),
                sum_squared_error=("squared_error", "sum"),
                sum_absolute_error=("absolute_error", "sum"),
                sum_ground_truth=("ground_truth_sum", "sum"),
            )
        )

    if not metric_chunks:
        raise ValueError(f"No seed predictions found in {prediction_path}")
    if observed_seeds != set(seeds):
        raise ValueError(
            f"Seed mismatch in {prediction_path}: observed={sorted(observed_seeds)}, "
            f"expected={sorted(seeds)}"
        )

    drug_metrics = (
        pd.concat(metric_chunks, ignore_index=True)
        .groupby(["target_idx", "target"], as_index=False)
        .agg(
            seed_prediction_rows=("seed_prediction_rows", "sum"),
            sum_squared_error=("sum_squared_error", "sum"),
            sum_absolute_error=("sum_absolute_error", "sum"),
            sum_ground_truth=("sum_ground_truth", "sum"),
        )
        .sort_values("target_idx")
        .reset_index(drop=True)
    )
    if drug_metrics["target_idx"].duplicated().any() or drug_metrics["target"].duplicated().any():
        raise ValueError(f"Target identifiers are not one-to-one in {prediction_path}")

    seed_count = len(seeds)
    if (drug_metrics["seed_prediction_rows"] % seed_count != 0).any():
        raise ValueError(f"Prediction row counts are not divisible by {seed_count} seeds")
    drug_metrics["evaluation_points"] = drug_metrics["seed_prediction_rows"] // seed_count
    drug_metrics["mse"] = (
        drug_metrics["sum_squared_error"] / drug_metrics["seed_prediction_rows"]
    )
    drug_metrics["mae"] = (
        drug_metrics["sum_absolute_error"] / drug_metrics["seed_prediction_rows"]
    )
    drug_metrics["mean_ground_truth"] = (
        drug_metrics["sum_ground_truth"] / drug_metrics["seed_prediction_rows"]
    )
    return drug_metrics[
        [
            "target_idx",
            "target",
            "evaluation_points",
            "seed_prediction_rows",
            "mean_ground_truth",
            "mse",
            "mae",
        ]
    ]


def calculate_hospital_metrics_from_drug_metrics(drug_metrics: pd.DataFrame) -> dict[str, float]:
    required_columns = {"mse", "mae", "seed_prediction_rows"}
    missing_columns = sorted(required_columns - set(drug_metrics.columns))
    if missing_columns:
        raise ValueError(f"Drug metrics are missing columns: {missing_columns}")
    if drug_metrics.empty:
        raise ValueError("Drug metrics must not be empty")

    row_counts = drug_metrics["seed_prediction_rows"].to_numpy(dtype=float)
    return {
        "mse": float(np.average(drug_metrics["mse"], weights=row_counts)),
        "mae": float(np.average(drug_metrics["mae"], weights=row_counts)),
    }


def load_hospital_model_comparison(
    spec: BestModelSpec,
    paths: PackagePaths,
) -> pd.DataFrame:
    metrics_path = paths.final_results(spec.hospital) / "ensemble_metrics_overall.csv"
    comparison = pd.read_csv(metrics_path)
    required_labels = {
        "pure_S04": "S04",
        "pure_S11": "S11",
        f"blend_w{spec.ensemble_weight_s11:.2f}": "Ensemble",
    }
    selected = comparison[comparison["label"].isin(required_labels)].copy()
    if set(selected["label"]) != set(required_labels):
        raise ValueError(f"Missing model result rows in {metrics_path}")
    selected["model"] = selected["label"].map(required_labels)
    model_order = {"S04": 0, "S11": 1, "Ensemble": 2}
    selected["model_order"] = selected["model"].map(model_order)
    return selected.sort_values("model_order").reset_index(drop=True)


def format_markdown_table_value(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_model_comparison_rows(hospital: str, comparison: pd.DataFrame) -> list[str]:
    return [
        "| "
        + " | ".join(
            [
                hospital.title(),
                str(row.model),
                str(int(row.seeds)),
                f"{float(row.mean_of_mean_mse):.6f}",
                f"{float(row.mean_of_mean_mae):.6f}",
            ]
        )
        + " |"
        for row in comparison.itertuples(index=False)
    ]


def render_drug_metric_rows(drug_metrics: pd.DataFrame) -> list[str]:
    rows = []
    for row in drug_metrics.itertuples(index=False):
        rows.append(
            "| "
            + " | ".join(
                [
                    str(int(row.target_idx)),
                    format_markdown_table_value(row.target),
                    f"{int(row.evaluation_points):,}",
                    f"{int(row.seed_prediction_rows):,}",
                    f"{float(row.mse):.6f}",
                    f"{float(row.mae):.6f}",
                ]
            )
            + " |"
        )
    return rows


def select_graphed_drug_metrics(
    drug_metrics: pd.DataFrame,
    top_k: int = 12,
) -> pd.DataFrame:
    selection_frame = drug_metrics[["target", "mean_ground_truth"]].rename(
        columns={"mean_ground_truth": "y_true"}
    )
    selected_targets = select_targets_with_highest_mean_ground_truth(selection_frame, top_k)
    indexed_metrics = drug_metrics.set_index("target", drop=False)
    selected_metrics = indexed_metrics.loc[selected_targets].reset_index(drop=True)
    selected_metrics.insert(0, "figure_rank", range(1, len(selected_metrics) + 1))
    return selected_metrics


def render_graphed_drug_metric_rows(drug_metrics: pd.DataFrame) -> list[str]:
    return [
        "| "
        + " | ".join(
            [
                str(int(row.figure_rank)),
                str(int(row.target_idx)),
                format_markdown_table_value(row.target),
                f"{float(row.mean_ground_truth):.6f}",
                f"{float(row.mse):.6f}",
                f"{float(row.mae):.6f}",
            ]
        )
        + " |"
        for row in drug_metrics.itertuples(index=False)
    ]


def render_result_description_markdown(root: Path | None = None) -> str:
    paths = PackagePaths.from_root(root)
    hospitals = ("epurun", "seosong")
    specs = {hospital: load_best_model_spec(hospital, paths.root) for hospital in hospitals}
    comparisons = {
        hospital: load_hospital_model_comparison(specs[hospital], paths)
        for hospital in hospitals
    }
    drug_metrics = {
        hospital: calculate_drug_metrics_from_seed_predictions(
            paths.final_results(hospital) / "ensemble_seed_predictions_per_drug.csv",
            specs[hospital].seeds,
        )
        for hospital in hospitals
    }

    for hospital in hospitals:
        overall = calculate_hospital_metrics_from_drug_metrics(drug_metrics[hospital])
        ensemble_row = comparisons[hospital].loc[
            comparisons[hospital]["model"] == "Ensemble"
        ].iloc[0]
        if not np.isclose(
            overall["mse"],
            ensemble_row["mean_of_mean_mse"],
            rtol=0.0,
            atol=1e-12,
        ):
            raise ValueError(f"Drug-level MSE does not reproduce {hospital} overall MSE")
        if not np.isclose(
            overall["mae"],
            ensemble_row["mean_of_mean_mae"],
            rtol=0.0,
            atol=1e-12,
        ):
            raise ValueError(f"Drug-level MAE does not reproduce {hospital} overall MAE")

    lines = [
        "# Result Description",
        "",
        "This document describes the reported prediction errors for the fixed ensemble and its "
        "two component models at Epurun and Seosong hospitals. It is generated from the final "
        "seed-level prediction and metric CSV artifacts.",
        "",
        "## Evaluation setup",
        "",
        "- Ensemble: `0.55 × S11_LATENT16 + 0.45 × S04_GRU_H32_L1`",
        "- Seeds: `42, 123, 456, 789, 1024`",
        "- Forecast horizon and rolling step: 14 days",
        "- Epurun: 52 folds per seed and 248 medication targets",
        "- Seosong: 79 folds per seed and 3,126 medication targets",
        "",
        "## Metric definitions",
        "",
        "For ground truth $y_i$ and prediction $\\hat{y}_i$ over $N$ evaluated "
        "seed–fold–horizon–medication observations:",
        "",
        "$$\\mathrm{MSE}=\\frac{1}{N}\\sum_{i=1}^{N}(y_i-\\hat{y}_i)^2$$",
        "",
        "$$\\mathrm{MAE}=\\frac{1}{N}\\sum_{i=1}^{N}|y_i-\\hat{y}_i|$$",
        "",
        "MSE gives larger errors disproportionate influence because errors are squared. MAE is "
        "the average absolute miss in the same daily medication-demand unit as the target. Lower "
        "values are better for both metrics.",
        "",
        "The reported ensemble MSE and MAE average the error of each of the five seeded ensemble "
        "predictions. They are not the error of the cross-seed mean prediction. Each hospital-wide "
        "value weights every seed–fold–horizon–medication observation equally. Because demand "
        "scales and target definitions differ between hospitals, raw MSE and MAE should be "
        "interpreted within each hospital rather than as a hospital ranking.",
        "",
        "## Hospital-wide results",
        "",
        "| Hospital | Model | Seeds | MSE | MAE |",
        "|---|---|---:|---:|---:|",
    ]
    for hospital in hospitals:
        lines.extend(render_model_comparison_rows(hospital, comparisons[hospital]))

    lines.extend(
        [
            "",
            "The fixed ensemble has the lowest hospital-wide MSE at both hospitals. At Epurun it "
            "also has the lowest MAE. At Seosong the ensemble MAE is only marginally lower than "
            "S11, so the practical difference in absolute error is small. These comparisons do "
            "not change the reported negative average skill relative to the last-value naive "
            "baseline.",
        ]
    )

    lines.extend(
        [
            "",
            "## Medications displayed in the prediction figures",
            "",
            "Each hospital prediction figure displays 12 medications, one graph per row. The "
            "displayed medications are selected by the highest mean ground truth (`y_true`) over "
            "the rolling evaluation period. This is a demand-volume display rule, not a ranking "
            "of model quality. The medication-level MSE and MAE use all five seeded ensemble "
            "predictions, consistent with the hospital-wide results.",
        ]
    )
    for hospital in hospitals:
        graphed_metrics = select_graphed_drug_metrics(drug_metrics[hospital])
        relative_figure_path = (
            f"../../results/{hospital}/final/ensemble_true_vs_pred_ci_per_drug.png"
        )
        lines.extend(
            [
                "",
                f"### {hospital.title()}: 12 displayed medications",
                "",
                f"[Open the {hospital.title()} prediction figure]({relative_figure_path}).",
                "",
                "| Figure order | Target index | Medication target | "
                "Mean ground truth | MSE | MAE |",
                "|---:|---:|---|---:|---:|---:|",
            ]
        )
        lines.extend(render_graphed_drug_metric_rows(graphed_metrics))

    for hospital in hospitals:
        spec = specs[hospital]
        metrics = drug_metrics[hospital]
        overall = calculate_hospital_metrics_from_drug_metrics(metrics)
        evaluation_points = metrics["evaluation_points"].unique()
        seed_prediction_rows = metrics["seed_prediction_rows"].unique()
        if len(evaluation_points) != 1 or len(seed_prediction_rows) != 1:
            raise ValueError(f"Unequal per-drug evaluation coverage for {hospital}")
        lines.extend(
            [
                "",
                f"## {hospital.title()} ensemble results by medication",
                "",
                f"Hospital-wide ensemble MSE is **{overall['mse']:.6f}** and MAE is "
                f"**{overall['mae']:.6f}** across {len(metrics):,} medications. Each medication "
                f"has {int(evaluation_points[0]):,} distinct fold–horizon evaluation points and "
                f"{int(seed_prediction_rows[0]):,} seeded prediction rows across "
                f"{len(spec.seeds)} seeds.",
                "",
                "| Target index | Medication target | Evaluation points | Seed-prediction rows | "
                "MSE | MAE |",
                "|---:|---|---:|---:|---:|---:|",
            ]
        )
        lines.extend(render_drug_metric_rows(metrics))

    lines.extend(
        [
            "",
            "## Interpretation limits",
            "",
            "Medication-level MSE and MAE reveal heterogeneity hidden by the hospital-wide "
            "average, but they do not encode medication importance, shortage cost, or clinical "
            "severity. Targets with larger numerical demand can dominate squared-error summaries. "
            "The values are retrospective rolling-evaluation results and should not be interpreted "
            "as calibrated clinical risk estimates.",
            "",
        ]
    )
    return "\n".join(lines)


def write_result_description_markdown(
    root: Path | None = None,
    output_path: Path | None = None,
) -> Path:
    paths = PackagePaths.from_root(root)
    resolved_output_path = output_path or paths.reports / "paper" / "RESULT_DESCRIPTION.md"
    resolved_output_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_output_path.write_text(
        render_result_description_markdown(paths.root),
        encoding="utf-8",
    )
    return resolved_output_path
