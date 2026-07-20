## Model invariants

- Stage 1 trains the shared representation and base predictor.
- Stage 2 performs the rolling-window adaptation used for evaluation.
- `window_size`, forecast horizon, and feature/target alignment must remain explicit.
- Preserve the two-hospital scope: Seosong and Epurun only.
- Do not weaken prediction guardrails or silently bypass quality-triggered refits.
