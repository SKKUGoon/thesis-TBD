from __future__ import annotations

import numpy as np
import pandas as pd


def select_targets_with_highest_mean_ground_truth(
    predictions: pd.DataFrame,
    top_k: int,
) -> list[str]:
    required_columns = {"target", "y_true"}
    missing_columns = sorted(required_columns - set(predictions.columns))
    if missing_columns:
        raise ValueError(f"Predictions are missing required columns: {missing_columns}")
    if top_k < 1:
        raise ValueError(f"top_k must be positive. Got {top_k}.")

    target_mean_ground_truth = predictions.groupby("target", as_index=False)["y_true"].mean()
    return (
        target_mean_ground_truth.sort_values(
            ["y_true", "target"],
            ascending=[False, True],
        )["target"]
        .head(top_k)
        .astype(str)
        .tolist()
    )


def skill(mse: float, naive_mse: float) -> float:
    if pd.isna(naive_mse) or float(naive_mse) <= 1e-12:
        return np.nan
    return 1.0 - float(mse) / float(naive_mse)


def compute_seed_fold_metrics(
    predictions: pd.DataFrame,
    prediction_column: str,
    naive_metrics: pd.DataFrame,
) -> pd.DataFrame:
    required_prediction_columns = {
        "fold_id",
        prediction_column,
        "seed",
        "t_end",
        "y_true",
    }
    missing_prediction_columns = sorted(required_prediction_columns - set(predictions.columns))
    if missing_prediction_columns:
        raise ValueError(f"Predictions are missing required columns: {missing_prediction_columns}")
    required_naive_columns = {"fold_id", "naive_mse", "seed", "t_end"}
    missing_naive_columns = sorted(required_naive_columns - set(naive_metrics.columns))
    if missing_naive_columns:
        raise ValueError(f"Naive metrics are missing required columns: {missing_naive_columns}")
    if predictions.empty:
        raise ValueError("Predictions must not be empty.")

    work = predictions[["seed", "fold_id", "t_end", "y_true", prediction_column]].copy()
    numeric_values = work[["y_true", prediction_column]].to_numpy(dtype=float)
    if not np.isfinite(numeric_values).all():
        raise ValueError("Predictions or ground-truth values contain non-finite values.")
    work["se"] = (work["y_true"] - work[prediction_column]) ** 2
    work["ae"] = np.abs(work["y_true"] - work[prediction_column])
    folds = work.groupby(["seed", "fold_id", "t_end"], as_index=False).agg(
        mse=("se", "mean"),
        mae=("ae", "mean"),
    )
    keys = ["seed", "fold_id", "t_end"]
    if naive_metrics.duplicated(keys).any():
        raise ValueError("Naive metrics contain duplicate fold keys.")
    folds = folds.merge(
        naive_metrics,
        on=keys,
        how="left",
        validate="one_to_one",
        indicator=True,
    )
    missing_naive_rows = folds["_merge"] != "both"
    if missing_naive_rows.any():
        raise ValueError(
            f"Naive metrics are missing {int(missing_naive_rows.sum())} prediction folds."
        )
    folds = folds.drop(columns="_merge")
    if not np.isfinite(folds["naive_mse"].to_numpy(dtype=float)).all():
        raise ValueError("Naive MSE values contain non-finite values.")
    folds["skill"] = folds.apply(lambda row: skill(row["mse"], row["naive_mse"]), axis=1)
    return folds


def summarize_seed_metrics(folds: pd.DataFrame, label: str) -> pd.DataFrame:
    if folds.empty:
        raise ValueError("Fold metrics must not be empty.")
    result = folds.groupby("seed", as_index=False).agg(
        n_folds=("fold_id", "count"),
        mean_mse=("mse", "mean"),
        median_mse=("mse", "median"),
        mean_mae=("mae", "mean"),
        median_mae=("mae", "median"),
        mean_skill=("skill", "mean"),
        median_skill=("skill", "median"),
        max_fold_mse=("mse", "max"),
        p95_fold_mse=("mse", lambda values: float(np.quantile(values, 0.95))),
    )
    result.insert(0, "label", label)
    return result
