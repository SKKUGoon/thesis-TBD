from __future__ import annotations

import numpy as np
import torch
from torch import nn

from lavar._data.scaler import StandardScalerTorch
from lavar.config import LAVARConfig
from lavar.forecaster import LAVARForecaster


class ConstantHead(nn.Module):
    def __init__(self, value: float, horizon: int) -> None:
        super().__init__()
        self.value = value
        self.horizon = horizon

    def forward(self, x: torch.Tensor, y0: torch.Tensor | None = None) -> torch.Tensor:
        return torch.full(
            (x.shape[0], self.horizon, 1),
            self.value,
            dtype=x.dtype,
            device=x.device,
        )


def build_forecaster(head_value: float, upper_bound: float) -> LAVARForecaster:
    config = LAVARConfig(
        device="cpu",
        dyn_p=1,
        horizon=2,
        forecast_round_to_int=False,
        zero_gate_k=0,
    )
    forecaster = LAVARForecaster(config)
    forecaster._x_scaler = StandardScalerTorch().fit(torch.tensor([[0.0], [1.0]]))
    forecaster._supply_dim = 1
    forecaster._dense_idx = [0]
    forecaster._dense_model = ConstantHead(head_value, config.horizon)
    forecaster._upper_bounds = torch.tensor([upper_bound])
    return forecaster


def test_nonfinite_predictions_report_persistence_fallback() -> None:
    forecaster = build_forecaster(float("inf"), upper_bound=10.0)

    result = forecaster.predict_with_metadata(
        np.array([[1.0]], dtype=np.float32),
        y_recent=np.array([[3.0]], dtype=np.float32),
    )

    np.testing.assert_array_equal(result.values, np.array([[3.0], [3.0]]))
    assert result.guardrail_metadata == {
        "guardrail_triggered": True,
        "fallback_used": True,
        "fallback_targets": [0],
        "nonfinite_count": 2,
        "clipped_count": 0,
    }


def test_high_predictions_report_clipping() -> None:
    forecaster = build_forecaster(20.0, upper_bound=5.0)

    result = forecaster.predict_with_metadata(
        np.array([[1.0]], dtype=np.float32),
        y_recent=np.array([[3.0]], dtype=np.float32),
    )

    np.testing.assert_array_equal(result.values, np.array([[5.0], [5.0]]))
    assert result.guardrail_metadata["guardrail_triggered"] is True
    assert result.guardrail_metadata["fallback_used"] is False
    assert result.guardrail_metadata["clipped_count"] == 2
