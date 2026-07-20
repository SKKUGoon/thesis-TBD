# Seed-Ensembled Latent-Dynamics Forecasting of 14-Day Medication Demand: A Rolling Evaluation at Seosong and Epurun Hospitals

**Working manuscript — not yet formatted for journal submission**

**Authors and affiliations:** To be supplied by the investigators.

## Abstract

Short-horizon medication-demand forecasts may support hospital inventory planning, but large, sparse target spaces and temporal persistence make evaluation difficult. We evaluated a two-stage latent autoencoder forecasting pipeline (LAVAR) using daily data from Seosong and Epurun hospitals. Stage 1 learns a shared latent representation and latent dynamics from patient-side and calendar covariates. Stage 2 freezes that representation and trains density-specific medication-demand heads. Two prespecified configurations were compared across five random seeds: a base latent model with a one-layer GRU supply head (`S04_GRU_H32_L1`) and a wider latent-16 model with the same head family (`S11_LATENT16`). Their predictions were combined as `0.55 × S11 + 0.45 × S04`.

Evaluation used expanding-history rolling origins, a 14-day horizon, a 14-day step, and 52 folds per seed for Epurun and 79 for Seosong. At Epurun, the ensemble achieved a cross-seed mean MSE of 0.9195, 4.38% below S11, and reduced mean p95 fold MSE from 1.3365 to 1.3070. It satisfied the stored promotion rule. At Seosong, ensemble mean MSE was 1.8214, 3.17% below S11, but p95 fold MSE increased from 4.4179 to 4.4676 and the promotion rule was not satisfied. Mean skill against a last-value naive forecast remained negative at both hospitals (−0.1092 and −0.1340). The ensemble therefore improved average error among the evaluated learned models, but did not outperform persistence overall. These findings support hospital-specific model governance: the blend is a defensible candidate at Epurun, whereas Seosong requires further validation or adaptive selection before promotion.

**Keywords:** medication demand; hospital forecasting; rolling-origin evaluation; latent dynamics; ensemble; seed stability

## 1. Introduction

Hospital medication demand is high-dimensional, intermittent, and heterogeneous. A forecasting system must represent shared clinical activity while retaining target-specific behavior across medications with very different frequencies. It must also be evaluated in temporal order and compared with a strong persistence baseline.

This study examines whether a shared latent-dynamics model and a fixed two-model ensemble provide stable 14-day forecasts across two hospitals. The contribution is an auditable rolling evaluation rather than a claim of universal superiority. We ask:

1. How stable are two selected LAVAR configurations across random seeds?
2. Does a fixed blend reduce average and tail error relative to its components?
3. Does the same operational decision follow at Seosong and Epurun?

Only Seosong and Epurun are analyzed in this paper.

## 2. Materials and methods

### 2.1 Data

The package contains the exact raw inputs consumed by preprocessing and the processed matrices used in the reported runs.

| Hospital | Date range | Daily observations | Model covariates | Medication targets | Folds per seed |
|---|---|---:|---:|---:|---:|
| Epurun | 2021-01-01 to 2024-12-31 | 1,461 | 45 | 248 | 52 |
| Seosong | 2020-12-14 to 2025-12-31 | 1,844 | 38 | 3,126 | 79 |

Epurun model covariates combine 38 processed patient-side features with seven calendar features. Seosong uses the 38 processed patient-side features without the calendar join used at Epurun. Medication targets are aligned daily series derived from the consumed hospital records. The two hospitals differ substantially in target count and scale; raw MSE values are therefore interpreted within hospital, not as a hospital ranking.

The repository may contain sensitive hospital data. Final publication and sharing require approved governance, de-identification, and institution-specific ethics language.

### 2.2 LAVAR model

LAVAR has two training stages. Stage 1 standardizes covariates, encodes them through an MLP, learns latent temporal dynamics, and reconstructs the input. Stage 2 freezes the shared backbone, assigns medication targets to dense, sparse, or ultra-sparse groups using nonzero rates calculated in the training window, and trains group-specific forecasting heads.

The paper compares two configurations:

| Component | S04_GRU_H32_L1 | S11_LATENT16 |
|---|---:|---:|
| Latent dimension | 8 | 16 |
| Encoder hidden layers | 32, 16 | 64, 32 |
| Decoder hidden layers | 16, 32 | 32, 64 |
| Latent dynamics | VAR | VAR |
| Supply head | one-layer GRU, width 32 | one-layer GRU, width 32 |
| Stage 1/Stage 2 epoch caps | 200 / 200 | 200 / 300 |

Both configurations use a seven-day latent history, 14-day output horizon, batch size 256, early stopping, and deterministic delta forecasts integrated from the most recent observed medication demand. Predictions are constrained to nonnegative values, guarded by training-derived upper bounds, and rounded to integers.

### 2.3 Rolling evaluation

Evaluation uses expanding-history rolling origins. The first forecast is made after at least 730 days. Each fold forecasts the next 14 days and advances by 14 days, so evaluation windows do not overlap. The nominal retraining cadence is 90 days; a recorded quality trigger may request earlier refitting after a poor fold. Scalers, target-density groups, and learned parameters use only data available before each forecast window.

Each configuration was evaluated with seeds 42, 123, 456, 789, and 1024. This produced ten component runs per hospital. Stored Stage 2 checkpoints reference immutable Stage 1 signatures so every reconstructed fold can be traced to its backbone.

### 2.4 Ensemble and metrics

For matched hospital, seed, fold, horizon, and medication target, the fixed ensemble is

```text
y_hat_ensemble = 0.55 × y_hat_S11 + 0.45 × y_hat_S04.
```

We report fold MSE, MAE, p95 fold MSE, maximum fold MSE, and skill against a last-value naive forecast:

```text
skill = 1 − MSE_model / MSE_naive.
```

Positive skill indicates improvement over persistence; negative skill indicates worse performance. Metrics are first calculated within seed and then summarized across five seeds. The 2.5% and 97.5% quantiles shown in prediction figures are empirical quantiles across the five seeded ensemble predictions. With only five fitted seeds, these bands describe seed variation and are not calibrated coverage intervals.

The stored promotion rule requires acceptable behavior across both mean MSE and p95 fold MSE, rather than mean error alone.

## 3. Results

### 3.1 Overall comparison

| Hospital/model | Mean MSE | SD of seed mean MSE | Mean MAE | Mean skill | Mean p95 fold MSE | Mean maximum fold MSE |
|---|---:|---:|---:|---:|---:|---:|
| Epurun S04 | 0.9574 | 0.0090 | 0.4068 | −0.1726 | 1.2993 | 4.5746 |
| Epurun S11 | 0.9617 | 0.0112 | 0.4107 | −0.1706 | 1.3365 | 3.4540 |
| **Epurun ensemble** | **0.9195** | **0.0083** | **0.4044** | **−0.1092** | 1.3070 | **2.4321** |
| Seosong S04 | 2.0210 | 0.0520 | 0.2708 | −0.2591 | 4.6053 | 10.7055 |
| Seosong S11 | 1.8810 | 0.2986 | 0.2616 | −0.2100 | **4.4179** | 11.6117 |
| **Seosong ensemble** | **1.8214** | **0.1641** | **0.2615** | **−0.1340** | 4.4676 | **9.8068** |

### 3.2 Epurun

At Epurun, the ensemble reduced mean MSE by 0.0421 (4.38%) relative to S11 and by 0.0379 relative to S04. It had lower MSE than S11 in all five seeds. Mean p95 fold MSE improved relative to S11, although it remained slightly above S04. The average maximum fold MSE was substantially lower than either component.

For weights 0.50 and 0.55, mean MSE improved in all five seeds, and both mean MSE and p95 improved together in four of five seeds. Neither weight increased maximum fold MSE under the stored decision table. The promotion rule was therefore satisfied. Per-seed MSE-optimal weights ranged from 0.45 to 0.55, indicating comparatively stable blend preference.

![Epurun seeded ensemble predictions](../../results/epurun/final/ensemble_true_vs_pred_ci_per_drug.png)

### 3.3 Seosong

At Seosong, the ensemble reduced cross-seed mean MSE by 0.0596 (3.17%) relative to S11 and also reduced the standard deviation of seed mean MSE. However, it beat S11 on mean MSE in only three of five seeds. Mean p95 fold MSE was 0.0497 higher than S11 at weight 0.55, and mean MSE and p95 improved together in only two seeds. Per-seed MSE-optimal weights ranged from 0.15 to 0.85.

The ensemble reduced the average maximum fold MSE relative to both components, but that benefit did not satisfy the stored promotion rule because p95 and seed-consistency criteria were not met. Seosong therefore provides evidence of average-error reduction, not a stable promotion decision.

![Seosong seeded ensemble predictions](../../results/seosong/final/ensemble_true_vs_pred_ci_per_drug.png)

### 3.4 Naive-baseline comparison

Mean skill was negative for every hospital-level model summary. The ensemble was less negative than either component, but it did not beat the last-value baseline overall. This is an important boundary on the result: blending improves the selected learned models; it does not establish superiority to persistence. High short-term persistence, target sparsity, rounding, and the large number of low-activity targets may all contribute, but the present analysis does not identify a causal mechanism.

## 4. Discussion

The same fixed ensemble produced lower average MSE at both hospitals, yet the decision consequences differed. Epurun showed consistent mean-error gains, a narrower useful weight range, and acceptable p95 behavior. Seosong showed stronger seed sensitivity and a tradeoff between average and upper-tail fold error. A single cross-hospital headline would hide this difference.

The reduction in seed variability at Seosong suggests that averaging partially stabilizes S11, whose seed mean MSE varied substantially. That stabilization is useful, but it is insufficient for promotion when tail behavior and per-seed consistency are governance criteria. Further Seosong work should evaluate validation-based or regime-dependent weighting, target segmentation, and direct comparison with stronger persistence and seasonal baselines.

At Epurun, the fixed blend is supported as a candidate within the evaluated protocol. Nevertheless, negative skill means any operational use should retain the naive forecast as a benchmark and possibly as a fallback or component. Prospective monitoring is required before inventory decisions are delegated to the learned model.

## 5. Limitations

1. The study is retrospective and includes only two hospitals.
2. The two hospitals have different feature construction, target counts, date ranges, and demand scales.
3. The two component families were selected before this packaged comparison; this is not a new exhaustive model search.
4. Hyperparameter and blend-weight selection evidence is reused from the executed workflow and is not an independently registered protocol.
5. Five seeds are too few for calibrated uncertainty estimation.
6. The reported seed bands omit observation noise, data drift, and model misspecification.
7. Aggregate MSE weights every target-time observation equally and may obscure medication-specific operational costs.
8. Mean skill is negative against last-value persistence at both hospitals.
9. No prospective inventory, shortage, waste, cost, or patient-outcome endpoint is evaluated.
10. Ethics, consent, and data-governance statements must be completed by the responsible institutions before submission.

## 6. Conclusion

A fixed ensemble of latent-16 and base-GRU LAVAR configurations reduced cross-seed mean MSE relative to either selected component at Seosong and Epurun. The evidence supports different operational conclusions: Epurun met the stored mean-and-tail promotion rule, whereas Seosong did not because p95 performance and preferred weights were unstable across seeds. Neither hospital achieved positive mean skill against a last-value naive forecast. The main implication is methodological: hospital forecasting models should be governed using temporal evaluation, seed stability, tail metrics, and explicit naive baselines rather than average learned-model error alone.

## Declarations to complete before submission

- **Ethics approval and consent:** Institution-approved wording required.
- **Data availability:** Access conditions and de-identification statement required.
- **Code availability:** Final repository location and release identifier required.
- **Funding:** Investigator statement required.
- **Conflicts of interest:** Investigator statement required.
- **Author contributions:** CRediT roles required.
- **References:** Literature review and target-journal reference list required; none are fabricated in this working draft.

## Reproducibility artifacts

- [Epurun overall metrics](../../results/epurun/final/ensemble_metrics_overall.csv)
- [Epurun seed metrics](../../results/epurun/final/ensemble_metrics_by_seed.csv)
- [Epurun blend decision](../../results/epurun/selection/blend_seed_robustness_decision.csv)
- [Seosong overall metrics](../../results/seosong/final/ensemble_metrics_overall.csv)
- [Seosong seed metrics](../../results/seosong/final/ensemble_metrics_by_seed.csv)
- [Seosong blend decision](../../results/seosong/selection/blend_seed_robustness_decision.csv)
