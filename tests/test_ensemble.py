from __future__ import annotations

import pandas as pd
import pytest

from paper_code.evaluation.metrics import (
    compute_seed_fold_metrics,
    select_targets_with_highest_mean_ground_truth,
)
from paper_code.modeling.ensemble import (
    add_forecast_dates_from_model_index,
    blend_component_predictions,
)


def component_row(scenario: str, *, abs_t: int = 10, y_true: float = 2.0) -> dict[str, object]:
    return {
        "scenario": scenario,
        "seed": 42,
        "fold_id": 0,
        "t_end": 9,
        "h": 1,
        "abs_t": abs_t,
        "target_idx": 0,
        "target": "drug_a",
        "y_true": y_true,
        "y_pred": 4.0 if scenario == "S11_LATENT16" else 2.0,
    }


def test_blend_requires_aligned_component_keys() -> None:
    predictions = pd.DataFrame(
        [
            component_row("S11_LATENT16"),
            component_row("S04_GRU_H32_L1", abs_t=11),
        ]
    )

    with pytest.raises(ValueError, match="do not align"):
        blend_component_predictions(predictions, 0.5)


def test_blend_requires_matching_ground_truth() -> None:
    predictions = pd.DataFrame(
        [
            component_row("S11_LATENT16"),
            component_row("S04_GRU_H32_L1", y_true=3.0),
        ]
    )

    with pytest.raises(ValueError, match="ground-truth"):
        blend_component_predictions(predictions, 0.5)


def test_blend_uses_explicit_weight() -> None:
    predictions = pd.DataFrame([component_row("S11_LATENT16"), component_row("S04_GRU_H32_L1")])

    blended = blend_component_predictions(predictions, 0.25)

    assert blended.loc[0, "y_blend"] == pytest.approx(2.5)


def test_seed_metrics_require_a_naive_baseline_for_every_fold() -> None:
    predictions = pd.DataFrame(
        {
            "seed": [42],
            "fold_id": [0],
            "t_end": [9],
            "y_true": [2.0],
            "y_pred": [2.5],
        }
    )
    naive_metrics = pd.DataFrame(columns=["seed", "fold_id", "t_end", "naive_mse"])

    with pytest.raises(ValueError, match="missing 1 prediction folds"):
        compute_seed_fold_metrics(predictions, "y_pred", naive_metrics)


def test_forecast_dates_use_absolute_model_positions() -> None:
    confidence_intervals = pd.DataFrame({"fold_id": [0, 0], "abs_t": [2, 3]})
    model_date_index = pd.date_range("2024-01-01", periods=5, freq="D")

    dated_intervals = add_forecast_dates_from_model_index(
        confidence_intervals,
        model_date_index,
    )

    assert dated_intervals["forecast_date"].tolist() == ["2024-01-03", "2024-01-04"]


def test_forecast_dates_reject_positions_outside_model_index() -> None:
    confidence_intervals = pd.DataFrame({"fold_id": [0], "abs_t": [3]})
    model_date_index = pd.date_range("2024-01-01", periods=3, freq="D")

    with pytest.raises(ValueError, match="outside model_date_index"):
        add_forecast_dates_from_model_index(confidence_intervals, model_date_index)


def test_graphed_targets_use_highest_mean_ground_truth_with_stable_ties() -> None:
    predictions = pd.DataFrame(
        {
            "target": ["drug_b", "drug_b", "drug_a", "drug_a", "drug_c"],
            "y_true": [3.0, 1.0, 2.0, 2.0, 1.0],
        }
    )

    selected_targets = select_targets_with_highest_mean_ground_truth(predictions, 2)

    assert selected_targets == ["drug_a", "drug_b"]
