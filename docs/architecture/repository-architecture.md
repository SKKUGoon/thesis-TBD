## Scope

This repository is an individual research-paper package carved out of a larger health-economics project. Keep it focused on the Seosong and Epurun analyses. Do not add pipelines or assumptions for unrelated hospitals.

The main code areas are:

- `lavar/`: reusable forecasting model, data utilities, training, and evaluation logic
- `paper_code/`: paper-specific data preparation, modeling, analysis, and reporting logic
- `scripts/`: thin command-line entry points for the paper workflow
- `configs/`: hospital-specific experiment configuration
- `notebooks/`: exploratory and reporting notebooks; reusable logic should live in Python modules
- `data/processed/`: canonical model-ready data
- `results/`: paper outputs and evaluation artifacts

## Scientific integrity

- Never fabricate data, metrics, checkpoints, or results.
- Preserve the meaning and definition of paper variables and evaluation metrics.
- Keep the Seosong and Epurun pipelines separate where their source schemas differ.
- Avoid changing archived or provenance-only files unless the task explicitly requires it.
- Do not claim a result is reproduced unless the code was run and the output was checked.
