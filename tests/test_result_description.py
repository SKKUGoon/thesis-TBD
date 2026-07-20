from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from paper_code.reporting.result_description import (
    calculate_drug_metrics_from_seed_predictions,
    calculate_hospital_metrics_from_drug_metrics,
    select_graphed_drug_metrics,
)


def write_seed_predictions(prediction_path: Path) -> None:
    pd.DataFrame(
        {
            "seed": [1, 2, 1, 2],
            "fold_id": [0, 0, 0, 0],
            "target_idx": [0, 0, 1, 1],
            "target": ["drug_a", "drug_a", "drug_b", "drug_b"],
            "y_true": [2.0, 2.0, 4.0, 4.0],
            "y_blend": [1.0, 3.0, 2.0, 4.0],
        }
    ).to_csv(prediction_path, index=False)


def test_drug_metrics_average_all_seed_prediction_errors(tmp_path: Path) -> None:
    prediction_path = tmp_path / "predictions.csv"
    write_seed_predictions(prediction_path)

    drug_metrics = calculate_drug_metrics_from_seed_predictions(
        prediction_path,
        (1, 2),
        chunksize=1,
    )

    assert drug_metrics["evaluation_points"].tolist() == [1, 1]
    assert drug_metrics["seed_prediction_rows"].tolist() == [2, 2]
    assert drug_metrics["mse"].tolist() == pytest.approx([1.0, 2.0])
    assert drug_metrics["mae"].tolist() == pytest.approx([1.0, 1.0])
    assert drug_metrics["mean_ground_truth"].tolist() == pytest.approx([2.0, 4.0])


def test_hospital_metrics_weight_drugs_by_prediction_rows(tmp_path: Path) -> None:
    prediction_path = tmp_path / "predictions.csv"
    write_seed_predictions(prediction_path)
    drug_metrics = calculate_drug_metrics_from_seed_predictions(prediction_path, (1, 2))

    hospital_metrics = calculate_hospital_metrics_from_drug_metrics(drug_metrics)

    assert hospital_metrics["mse"] == pytest.approx(1.5)
    assert hospital_metrics["mae"] == pytest.approx(1.0)


def test_graphed_drug_metrics_follow_mean_ground_truth_order(tmp_path: Path) -> None:
    prediction_path = tmp_path / "predictions.csv"
    write_seed_predictions(prediction_path)
    drug_metrics = calculate_drug_metrics_from_seed_predictions(prediction_path, (1, 2))

    selected_metrics = select_graphed_drug_metrics(drug_metrics, top_k=2)

    assert selected_metrics["figure_rank"].tolist() == [1, 2]
    assert selected_metrics["target"].tolist() == ["drug_b", "drug_a"]
