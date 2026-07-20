# Fixed-Blend Robustness — Seosong

The decision compares fixed S11 weights of 0.50 and 0.55 with pure S11 across five matched seeds.

| S11 weight | Mean-MSE wins | p95 wins | Wins on both | Worse maximum folds | Mean MSE change | Mean p95 change | Promote |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 0.50 | 3/5 | 2/5 | 2/5 | 2 | −0.0538 | +0.0599 | No |
| 0.55 | 3/5 | 2/5 | 2/5 | 2 | −0.0596 | +0.0497 | No |

Per-seed MSE-optimal S11 weights range from 0.15 to 0.85. Composite-objective weights range from 0.05 to 1.00. The average MSE benefit is not accompanied by stable p95 improvement, so the stored decision rule does not support promotion at Seosong.
