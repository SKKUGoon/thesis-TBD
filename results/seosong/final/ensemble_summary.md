# Ensemble Result Summary — Seosong

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
| S04 | 2.0210 | 0.0520 | 0.2708 | −0.2591 | 4.6053 | 10.7055 |
| S11 | 1.8810 | 0.2986 | 0.2616 | −0.2100 | **4.4179** | 11.6117 |
| **Ensemble** | **1.8214** | **0.1641** | **0.2615** | **−0.1340** | 4.4676 | **9.8068** |

The ensemble mean-MSE change relative to S11 is −0.0596 (−3.17%). The stored selection decision does not promote the blend: it improves mean MSE in three of five seeds, but mean MSE plus p95 improve together in only two seeds and p95 is worse on average.

Negative skill means the model remains worse than the last-value naive baseline on average. Seed quantiles describe initialization sensitivity; they are not calibrated prediction intervals.
