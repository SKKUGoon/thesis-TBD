# Fixed-Blend Robustness — Epurun

The decision compares fixed S11 weights of 0.50 and 0.55 with pure S11 across five matched seeds.

| S11 weight | Mean-MSE wins | p95 wins | Wins on both | Worse maximum folds | Mean MSE change | Mean p95 change | Promote |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 0.50 | 5/5 | 4/5 | 4/5 | 0 | −0.0428 | −0.0317 | Yes |
| 0.55 | 5/5 | 4/5 | 4/5 | 0 | −0.0421 | −0.0295 | Yes |

Per-seed MSE-optimal S11 weights range from 0.45 to 0.55. Composite-objective weights range from 0.35 to 0.60. Within this stored decision rule, the blend is supported for promotion at Epurun.
