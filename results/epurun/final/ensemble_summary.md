# Ensemble Result Summary — Epurun

This summary covers the fixed S11/S04 ensemble evaluated in the paper package.

## Setup

- Seeds: `[42, 123, 456, 789, 1024]`
- Ensemble: `0.55 × S11 + 0.45 × S04`
- Horizon: 14 days
- Prediction constraint: nonnegative
- Displayed interval: empirical 2.5%–97.5% quantiles across five seeded predictions

## Cross-seed metrics

| Model | Mean MSE | Seed SD | Mean MAE | Mean skill | Mean p95 fold MSE | Mean maximum fold MSE |
|---|---:|---:|---:|---:|---:|---:|
| S04 | 0.9574 | 0.0090 | 0.4068 | −0.1726 | 1.2993 | 4.5746 |
| S11 | 0.9617 | 0.0112 | 0.4107 | −0.1706 | 1.3365 | 3.4540 |
| **Ensemble** | **0.9195** | **0.0083** | **0.4044** | **−0.1092** | 1.3070 | **2.4321** |

The ensemble mean-MSE change relative to S11 is −0.0421 (−4.38%). The stored selection decision promotes the blend: mean MSE improves in all five seeds, and mean MSE plus p95 improve together in four seeds.

Negative skill means the model remains worse than the last-value naive baseline on average. Seed quantiles describe initialization sensitivity; they are not calibrated prediction intervals.
