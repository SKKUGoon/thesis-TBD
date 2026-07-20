## Python coding convention

### 1. Naming and signatures

- Use `snake_case` for functions, methods, modules, variables, and parameters.
- Use `PascalCase` for classes and type aliases.
- Use `UPPER_SNAKE_CASE` for true module-level constants.
- Prefer descriptive names over abbreviations. Domain-standard names such as `x`, `y`, `y_hat`, `df`, `mse`, and `pca` are acceptable only when the local meaning is obvious.
- Public functions and methods must have type hints for all parameters and return values.
- Avoid untyped `**kwargs` in public interfaces. If flexible options are necessary, define a `TypedDict`, dataclass, Pydantic model, protocol, or explicit keyword-only parameters.

```python
def calculate_skill_score(model_mse: float, baseline_mse: float) -> float:
    """Return model skill relative to a baseline MSE."""
```

### 2. Typed, explicit interfaces

- Use concrete types that communicate scientific meaning: `pd.DataFrame`, `pd.Series`, `np.ndarray`, `Path`, and project-specific dataclasses/config models.
- Prefer `Path` over raw path strings at file-system boundaries.
- Prefer structured return types over loosely shaped dictionaries when the result has a stable schema.
- Validate public inputs at boundaries: required columns, array dimensions, matching lengths, valid date order, non-empty training windows, and legal configuration values.
- Raise specific exceptions with actionable messages. Do not use `assert` for runtime validation because assertions can be disabled.
- Keep device, dtype, index, and column conversions explicit when moving between pandas, NumPy, and PyTorch.

```python
def load_feature_table(path: Path, required_columns: Sequence[str]) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Feature table not found: {path}")

    frame = pd.read_parquet(path)
    missing_columns = sorted(set(required_columns) - set(frame.columns))
    if missing_columns:
        raise ValueError(f"Missing required columns: {missing_columns}")
    return frame
```

### 3. No silent fallback

- Never silently substitute another file, sheet, hospital, feature set, checkpoint, device, or model configuration.
- If a scientifically equivalent fallback is intentionally supported, make it an explicit named option and record it in logs or result metadata.
- Do not catch broad exceptions around model training, checkpoint loading, metric computation, or data preparation.
- When re-raising, preserve the original exception context with `raise ... from error`.
- Guardrail actions such as clipping, non-finite replacement, or persistence fallback must be visible in returned metadata or logs.

```python
def load_checkpoint(path: Path, *, allow_missing: bool = False) -> dict[str, object] | None:
    if not path.exists():
        if allow_missing:
            return None
        raise FileNotFoundError(f"Checkpoint not found: {path}")
    return torch.load(path, map_location="cpu", weights_only=True)
```

### 4. Focused functions and clear boundaries

- Keep functions focused on one level of abstraction. Separate I/O, validation, transformation, fitting, prediction, evaluation, and persistence when practical.
- Keep CLI modules thin: parse arguments, call a typed library function, and return an exit code.
- Do not hide reusable research logic in notebooks or command-line scripts.
- Avoid mutable default arguments. Use `None` or `default_factory` and create values inside the function or model.
- Avoid mutating caller-owned DataFrames, arrays, dictionaries, or config objects unless the function name and documentation make that behavior explicit.
- Use context managers for files and external resources.
- Keep model construction separate from checkpoint loading and state mutation.

### 5. Scientific naming and numerical code

- Use comments to explain scientific assumptions, indexing choices, units, and non-obvious tensor shapes, not to narrate straightforward code.
- Include units or scales in names when ambiguity is possible, such as `window_days`, `learning_rate`, or `mse_per_target`.
- Do not rely on implicit broadcasting when it obscures the intended array shape.
- Check for empty arrays and non-finite values at scientific boundaries.
- Preserve time order in rolling evaluation. Never shuffle temporal evaluation data.
- Derive feature and target names from the canonical schema or configuration instead of repeating ad hoc lists throughout the codebase.

```python
if predictions.shape != targets.shape:
    raise ValueError(
        "Predictions and targets must have identical shapes: "
        f"{predictions.shape} != {targets.shape}"
    )

if not np.isfinite(predictions).all():
    raise ValueError("Predictions contain non-finite values")
```

### 6. Import groups and order

Every Python module must use the following import order:

1. `__future__` imports
2. Standard-library imports
3. Third-party library imports
4. Local application imports

Separate each group with exactly one blank line. Sort imports alphabetically within each group. Do not mix local imports into third-party blocks, and do not leave multiple blank lines between import groups.

```python
# 1. Future imports
from __future__ import annotations

# 2. Standard library imports (separated by a blank line, sorted alphabetically)
import json
import os
import sys

# 3. Third-party library imports (separated by a blank line, sorted alphabetically)
import numpy as np
import requests
from flask import Flask

# 4. Local application imports (separated by a blank line, sorted alphabetically)
from my_project.models import User
from .utils import calculate_score
```

Additional import rules:

- Use one import per line for standard-library and third-party module imports when practical.
- Prefer absolute imports across top-level packages, for example `from lavar.config import LAVARConfig`.
- Relative imports are acceptable within a tightly scoped subpackage when they make the dependency clearer.
- Do not use wildcard imports.
- Do not modify `sys.path` inside application modules. Package the code correctly or run entry points with `python -m ...`.
- Optional dependencies must be imported at the narrowest boundary that needs them and must fail with an actionable installation message.
- Use `if TYPE_CHECKING:` for imports used only by static type checking when importing them at runtime would create a cycle or unnecessary overhead.
- Import modules for side effects only when unavoidable, and document the reason inline.
