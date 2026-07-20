# LAVAR Technical Guide

Languages: [English](LAVAR_README.md) | [한국어](LAVAR_README_KOR.md) | [日本語](LAVAR_README_JPN.md)

LAVAR is the research model used in this repository for multi-step medication-demand forecasting. It combines an autoencoded latent representation, learned latent dynamics, and medication-specific forecasting heads. This guide describes the implementation contract used for the Seosong and Epurun study; it is not a claim of clinical effectiveness.

## Architecture

### Stage 1: shared latent dynamics

Given aligned covariates `X ∈ R^(T×Dx)`, Stage 1:

1. standardizes the covariates;
2. maps each observation through an MLP encoder;
3. learns temporal evolution in the latent space using VAR or GRU dynamics; and
4. reconstructs covariates through an MLP decoder.

Training combines reconstruction and latent-dynamics losses. Optional multi-step supervision compares rolled-out latent states with encodings of future observations.

### Stage 2: medication-demand heads

Stage 2 freezes the Stage 1 backbone. Medication targets are split by their nonzero rate in the training window:

- dense: nonzero rate at least `0.70`;
- ultra-sparse: nonzero rate at most `0.005`; and
- sparse: values between those thresholds.

Separate heads are fitted for the resulting target groups. The implementation supports deterministic delta heads and lower-level NB/ZINB heads. The two paper configurations use deterministic GRU delta heads. Delta predictions are integrated from the most recent observed demand, constrained to nonnegative values, passed through stored guardrails, and rounded to integers.

## Paper configurations

| Setting | S04_GRU_H32_L1 | S11_LATENT16 |
|---|---:|---:|
| Latent dimension | 8 | 16 |
| Encoder hidden layers | 32, 16 | 64, 32 |
| Decoder hidden layers | 16, 32 | 32, 64 |
| Latent dynamics | VAR | VAR |
| Stage 2 head | GRU | GRU |
| GRU hidden width/layers | 32 / 1 | 32 / 1 |
| Stage 1 epochs cap | 200 | 200 |
| Stage 2 epochs cap | 200 | 300 |

Shared study settings include a seven-day latent history, 14-day horizon, batch size 256, 730-day initial training requirement, early stopping, and no supply-history concatenation into the Stage 1 encoder.

## Public API

```python
from lavar import EvaluationResults, LAVARConfig, LAVARForecaster, RollingEvaluator
```

- `LAVARConfig`: validated model, training, and inference configuration.
- `LAVARForecaster`: fit, predict, save, and load interface.
- `RollingEvaluator`: rolling-origin evaluation utility.
- `EvaluationResults`: fold records, summary metrics, and plotting helper.

## Data contract

```text
X: (T, Dx) aligned daily covariates
y: (T, Dy) aligned nonnegative medication-demand targets
forecast: (H, Dy), where H is the configured horizon
```

`fit(X, y)` fits both stages. `fit_stage1_shared(X, y)` and `fit_stage2_private(X, y)` expose the two-stage workflow. `predict(X_recent, y_recent)` requires recent covariate history; the recent target history supplies the integration baseline for delta heads.

## Minimal example

```python
import numpy as np

from lavar import LAVARConfig, LAVARForecaster

config = LAVARConfig(
    device="cpu",
    dyn_p=7,
    horizon=14,
    latent_dim=8,
    stage2_head_type="gru",
    gru_hidden_dim=32,
    gru_num_layers=1,
    epochs_lavar=5,
    epochs_supply=5,
)

time_steps, covariates, targets = 800, 20, 6
X = np.random.randn(time_steps, covariates).astype("float32")
y = np.abs(np.random.randn(time_steps, targets)).astype("float32")

forecaster = LAVARForecaster(config)
forecaster.fit(X, y)
forecast = forecaster.predict(X[-7:], y_recent=y[-7:])
assert forecast.shape == (14, targets)
```

## Rolling evaluation

The paper workflow uses expanding-history rolling origins. At each origin, fitting and scaling use only observations before the forecast window. The forecast covers the next 14 days, and the next origin advances by 14 days. Stored checkpoints separate Stage 1 signatures from fold-specific Stage 2 states so reused backbones remain auditable.

Forecast guardrails replace non-finite target predictions with the last-value forecast and clip extreme values using bounds derived from training data. Guardrail use is recorded in fold metadata; it is part of the evaluated pipeline, not an unreported fallback.

## Interpretation constraints

- The model forecasts research targets, not patient outcomes or treatment decisions.
- Negative skill means the model’s fold MSE exceeds the last-value naive baseline on average.
- Quantiles across five random seeds describe seed sensitivity, not calibrated predictive uncertainty.
- A change to latent dimensions, input columns, history length, or bucket indices invalidates dependent checkpoints.
- Model comparisons are valid only under the same hospital data, rolling origins, target set, and metric aggregation.

## Package layout

```text
lavar/
├── __init__.py
├── config.py
├── forecaster.py
├── evaluation.py
├── losses.py
├── _core/       # encoder/decoder, latent dynamics, and heads
├── _data/       # rolling datasets and scalers
└── _training/   # Stage 1 and Stage 2 training
```

The study-specific orchestration is deliberately outside this package under `paper_code/`.
