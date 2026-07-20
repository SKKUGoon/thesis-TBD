## Paper and documentation synchronization

When behavior changes, update the relevant paper-facing material in the same change:

- preprocessing or cohort logic → `docs/DATA_PROVENANCE.md`, `docs/REPRODUCIBILITY.md`, and method text
- model or refit logic → `docs/MODEL_CARD.md`, `docs/REPRODUCIBILITY.md`, and method text
- metrics or reported numbers → `results/`, `docs/RESULTS_SUMMARY.md`, figures/tables, and paper results text
- dependency or workflow changes → `README.md`, `pyproject.toml`, and `uv.lock`

## Validation expectations

Run the narrowest checks that cover the change, then expand as needed. Typical checks include:

```bash
uv sync --frozen
uv run python -m compileall lavar paper_code scripts
uv run python -m scripts.prepare_data --hospital epurun
uv run python -m scripts.prepare_data --hospital seosong
uv run python -m scripts.train_models --hospital epurun
uv run python -m scripts.train_models --hospital seosong
uv run python -m scripts.verify_package
```

For small changes, run the affected command and targeted tests first. Before declaring a result reproduced, verify the generated metrics or artifacts against the canonical paper outputs.

## Privacy

- Treat raw hospital data as sensitive.
- Do not print, log, or commit patient identifiers or raw record-level excerpts.
- Keep examples synthetic and aggregate diagnostics whenever possible.
