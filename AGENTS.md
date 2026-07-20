# AGENTS.md

Root guide for agents working in the Seosong–Epurun paper repository.

## Scope

This repository supports one paper using Seosong and Epurun hospital data. Do not introduce results, prose, paths, or artifacts for other hospitals unless the user explicitly expands the study scope.

The main code areas are:

- `lavar/`: reusable LAVAR model, training, and evaluation implementation.
- `paper_code/`: paper-specific preprocessing, checkpoint reconstruction, metrics, ensemble logic, and reporting.
- `scripts/`: thin command-line entry modules.
- `configs/`: selected hospital/model specifications.
- `data/`: raw inputs and processed model matrices.
- `models/`: packaged rolling Stage 1 and Stage 2 checkpoints.
- `results/`: run metadata, blend-selection evidence, and final outputs.
- `reports/`: result notebooks, manuscripts, and compiled reports.

## Python coding convention

### 1. A function must be understandable from its name and signature alone

A reader at the call site must be able to tell what a function does without opening its body or following nested definitions. If understanding a call requires descending into implementations, the naming has failed.

- Function names state action and target: `process()`, `handle_data()`, and `do_stuff()` are too vague; prefer `build_model_ready_data()`, `reconstruct_component_predictions()`, or `calculate_confidence_intervals()`.
- Variable names describe their contents: `res`, `tmp`, `d`, and `arr` are too vague; prefer `fold_metrics`, `checkpoint_path`, `target_date`, or `seed_predictions`.
- Conventional mathematical names such as `X`, `y`, `T`, `H`, `Dx`, and `Dy` are acceptable inside numerical model code when their shapes and meanings are established by the interface.
- Boolean names are predicates: `selection`, `retry`, and `fallback` are too vague; prefer `has_selected_model`, `should_retrain`, or `was_fallback_used`.
- The name must match the behavior. Do not hide file writes, checkpoint creation, result regeneration, or other side effects behind neutral names. If a side effect exists, surface it in the name, signature, documentation, or structured return value.
- Comments carry only what names cannot: scientific constraints, tradeoffs, invariants, and reasons. A comment that merely restates the code is a signal that the name should improve.
- Never put an inline comment on trivial code.

Bad:

```python
def process(data):
    # get results
    res = calculate(data)

    # save results
    save(res)
```

Good:

```python
def calculate_and_write_seed_metrics(
    seed_predictions: pd.DataFrame,
    output_path: Path,
) -> Path:
    fold_metrics = calculate_seed_fold_metrics(seed_predictions)
    fold_metrics.to_csv(output_path, index=False)
    return output_path
```

### 2. Prefer typed, explicit Python interfaces

Keep boundaries clear between preprocessing, model training, checkpoint reconstruction, evaluation, and reporting.

- Add type annotations for new public functions and non-trivial helpers.
- Prefer dataclasses, pydantic models, typed dictionaries, or small domain objects over passing loosely shaped dictionaries through several layers.
- Return structured values instead of positional tuples whose meaning must be remembered at every call site.
- Pass configuration, paths, hospital names, seeds, dates, and devices explicitly. Avoid hidden mutable global state.
- Use `pathlib.Path` for filesystem paths.
- Keep command-line modules thin. Put reusable logic in `paper_code/` or `lavar/`.
- Do not add `*` to every function by default. Use ordinary positional-or-keyword parameters for short, required inputs whose meaning is clear from the function and parameter names. Use keyword-only parameters for options, flags, same-typed values that are easy to swap, or values whose meaning should be forced at the call site.
- Document array and tensor shapes at public numerical boundaries.
- Preserve dataframe index and column contracts explicitly when functions cross preprocessing or checkpoint boundaries.

Bad:

```python
def run(h, p, x, y, a=False):
    return train(x, y), evaluate(x, y)
```

Good:

```python
@dataclass(frozen=True)
class HospitalRunResult:
    hospital: str
    checkpoint_paths: tuple[Path, ...]
    fold_metrics: pd.DataFrame


def train_and_evaluate_hospital_model(
    hospital: str,
    config: LAVARConfig,
    covariates: pd.DataFrame,
    targets: pd.DataFrame,
    *,
    should_force_retraining: bool = False,
) -> HospitalRunResult:
    ...
```

### 3. No silent fallback to old or different behavior

When replacing an implementation, the new implementation replaces the old one. It must not quietly delegate back to a legacy path. Hidden exception handling or conditional fallback masks failures, keeps dead code alive, and makes scientific behavior impossible to audit.

- Replace fully. When a migration lands, remove the superseded path in the same change.
- Failures surface. Raise or propagate unexpected errors instead of returning plausible-looking output.
- Never substitute cached, legacy, naive, or partial results merely to make a pipeline complete successfully.
- If a fallback is scientifically or operationally required, it must be explicit and observable: gated by a named condition, recorded in logs or returned metadata, and documented in the result interpretation.
- A fallback must preserve enough context to identify which folds, targets, or predictions used it.

Bad:

```python
def reconstruct_predictions(checkpoint_path: Path) -> pd.DataFrame:
    try:
        return reconstruct_predictions_from_checkpoint(checkpoint_path)
    except Exception:
        return load_legacy_predictions()
```

Good:

```python
def reconstruct_predictions(
    checkpoint_path: Path,
    *,
    allow_recorded_naive_fallback: bool = False,
) -> PredictionResult:
    try:
        return reconstruct_predictions_from_checkpoint(checkpoint_path)
    except NonFinitePredictionError:
        if not allow_recorded_naive_fallback:
            raise

        fallback_result = build_naive_fallback_prediction(checkpoint_path)
        return replace(fallback_result, was_fallback_used=True)
```

### 4. Keep functions focused and boundaries visible

- Separate reading, transformation, computation, validation, and writing when they are independently useful or independently testable.
- A function may coordinate several steps, but its name and return type must make that orchestration clear.
- Prefer pure helpers for metric and dataframe transformations.
- Keep model construction separate from checkpoint loading and state mutation.
- Validate assumptions at boundaries: aligned indexes, expected columns, supported hospitals, matching ground truth, valid tensor shapes, and resolvable checkpoint signatures.
- Avoid deep nesting. Use early validation and focused helpers when branches represent distinct scientific cases.

### 5. Preserve scientific meaning in names and types

- Distinguish `prediction`, `ground_truth`, `naive_prediction`, and `ensemble_prediction` explicitly.
- Distinguish within-seed, across-seed, within-fold, and across-fold summaries.
- Use `p95_fold_mse` for the 95th percentile of fold MSE; do not shorten it to an ambiguous `p95` at public boundaries.
- Do not call cross-seed empirical quantiles calibrated prediction intervals.
- A metric name must reveal its aggregation level when confusion is possible, such as `mean_of_seed_mean_mse` rather than a generic `average_error`.
- Do not use “best,” “robust,” “validated,” or “production” in an API or artifact name unless the corresponding decision rule is defined and satisfied.

### 6. Keep imports grouped and ordered

Organize imports into four groups in this order. Separate each non-empty group with exactly one blank line, and sort imports alphabetically within each group.

1. Future imports
2. Standard-library imports
3. Third-party library imports
4. Local application imports, including package-relative imports

```python
# 1. Future imports
from __future__ import annotations

# 2. Standard library imports
import json
import os
import sys

# 3. Third-party library imports
from flask import Flask
import numpy as np
import requests

# 4. Local application imports
from my_project.models import User
from .utils import calculate_score
```

The numbered comments above demonstrate the grouping and are not required in source files. Do not mix imports from different groups, place ordinary imports before a future import, or insert unrelated executable code between imports. Avoid wildcard imports. Keep imports at module scope unless a local import is required to prevent a documented circular dependency, defer an optional dependency, or avoid a measured startup cost.

## Scientific integrity

- Treat `data/raw/` as immutable. Preprocessing writes only to `data/processed/`.
- Never modify checkpoints or reported metrics merely to make validation pass.
- Preserve temporal order. Training data for a rolling fold must end strictly before its forecast window.
- Fit scalers, density buckets, and learned parameters only on information available at the fold training boundary.
- Keep hospital definitions, horizon, fold step, seeds, and ensemble weights synchronized with `configs/` and the executed artifacts.
- Report negative skill against the naive baseline plainly.
- Distinguish model-selection evidence from final rolling evaluation.
- Do not fabricate citations, author metadata, ethics approvals, institutional statements, or clinical interpretations.

## Model invariants

- Changing `latent_dim`, encoder/decoder dimensions, Stage 1 input columns, or `dyn_p` requires refitting Stage 1 and dependent Stage 2 heads.
- Do not unfreeze or independently replace the shared Stage 1 backbone during Stage 2 training.
- Density-bucket target indices are part of the Stage 2 checkpoint contract and must remain aligned with target columns and head weights.
- Every Stage 2 checkpoint must resolve to its recorded Stage 1 signature.
- S04/S11 predictions must match on hospital, seed, fold, horizon, target, and ground truth before blending.

## Paper and documentation synchronization

When changing substantive claims, keep these files synchronized in the same change:

- `README.md`
- `MODEL_EXPLANATION.md`
- `lavar/LAVAR_README.md`
- `lavar/LAVAR_README_KOR.md`
- `lavar/LAVAR_README_JPN.md`

Hospital summaries under `results/` must be generated from or checked against their CSV artifacts. Do not hand-edit result numbers without verification.

## Validation expectations

Use checks proportional to the change.

- For Python changes, compile the affected modules and run the package validator.
- For documentation changes, search for out-of-scope hospital names, broken relative links, stale experiment numbering, and inconsistencies with final metric CSVs.
- For preprocessing changes, compare shapes, date indexes, column order, missingness, and values with the previous committed artifacts.
- For model changes, exercise a small or resumed path before starting an expensive reconstruction.
- Add or update focused tests when behavior changes. Do not rely on successful imports alone.

## Privacy

Assume raw hospital data is sensitive. Do not print patient-level rows, copy raw inputs outside approved storage, upload them to external services, or include identifiable values in reports. Prefer aggregate diagnostics and synthetic examples.
