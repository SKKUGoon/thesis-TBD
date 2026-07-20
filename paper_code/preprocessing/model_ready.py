from __future__ import annotations

from pathlib import Path

import pandas as pd


def build_model_ready_data(
    hospital: str,
    processed_dir: Path,
    calendar_path: Path,
) -> tuple[Path, Path]:
    """Create the exact X/Y parquet inputs used by the selected experiments."""
    patient = pd.read_parquet(processed_dir / "data_patient.parquet")
    prescription = pd.read_parquet(processed_dir / "data_prescription.parquet")

    if not patient.index.equals(prescription.index):
        raise ValueError(f"{hospital}: patient and prescription dates do not align")

    # The executed Epurun workflow included calendar covariates. The executed
    # Seosong workflow retained the patient features as-is.
    if hospital == "epurun":
        calendar = pd.read_parquet(calendar_path)
        model_x = patient.join(calendar)
    elif hospital == "seosong":
        model_x = patient
    else:
        raise ValueError(f"Unsupported hospital: {hospital}")

    model_y = prescription
    x_path = processed_dir / "lavar_ready_x.parquet"
    y_path = processed_dir / "lavar_ready_y.parquet"
    model_x.to_parquet(x_path)
    model_y.to_parquet(y_path)
    return x_path, y_path
