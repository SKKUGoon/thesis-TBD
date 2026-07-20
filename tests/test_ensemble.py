from __future__ import annotations

import pandas as pd
import pytest

from paper_code.evaluation.metrics import compute_seed_fold_metrics
from paper_code.modeling.ensemble import blend_component_predictions


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
