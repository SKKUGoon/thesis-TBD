from __future__ import annotations

from pathlib import Path

import pandas as pd

from paper_code.reporting.data_description import (
    build_data_description_table,
    calculate_tukey_boxplot_values,
    render_data_description_markdown,
)


def write_processed_test_data(root: Path, hospital: str, calendar_covariates: int) -> None:
    processed_dir = root / "data" / "processed" / hospital
    processed_dir.mkdir(parents=True)
    daily_index = pd.date_range("2024-01-01", periods=3, freq="D")
    patient = pd.DataFrame(
        {"Female": [1, 2, 3], "Male": [2, 1, 0]},
        index=daily_index,
    )
    medication = pd.DataFrame(
        {"drug_a": [0, 1, 0], "drug_b": [1, 1, 1]},
        index=daily_index,
    )
    model = patient.copy()
    for number in range(calendar_covariates):
        model[f"calendar_{number}"] = 0

    patient.to_parquet(processed_dir / "data_patient.parquet")
    medication.to_parquet(processed_dir / "data_prescription.parquet")
    model.to_parquet(processed_dir / "lavar_ready_x.parquet")


def test_build_data_description_table_calculates_matrix_characteristics(
    tmp_path: Path,
) -> None:
    write_processed_test_data(tmp_path, "epurun", calendar_covariates=1)
    write_processed_test_data(tmp_path, "seosong", calendar_covariates=0)

    table = build_data_description_table(tmp_path).set_index("Characteristic")

    assert table.loc["Daily observations", "Epurun"] == 3
    assert table.loc["Calendar covariates used by the model", "Epurun"] == 1
    assert table.loc["Medication targets", "Seosong"] == 2
    assert table.loc["Zero-valued cells in the target matrix", "Epurun"] == "33.33%"
    assert table.loc["Median target active-day rate", "Seosong"] == "66.67%"


def test_render_data_description_markdown_contains_only_table() -> None:
    table = pd.DataFrame([{"Characteristic": "Daily observations", "Epurun": 3, "Seosong": 4}])

    markdown = render_data_description_markdown(table)

    assert markdown == (
        "| Characteristic | Epurun | Seosong |\n|---|---|---|\n| Daily observations | 3 | 4 |\n"
    )


def test_calculate_tukey_boxplot_values_excludes_outlier_from_whisker() -> None:
    daily_visits = pd.Series([1, 2, 3, 4, 100])

    values = calculate_tukey_boxplot_values(daily_visits)

    assert values == {
        "Days": 5,
        "Mean visits": 22.0,
        "Lower whisker": 1.0,
        "Q1": 2.0,
        "Median": 3.0,
        "Q3": 4.0,
        "Upper whisker": 4.0,
        "Outlier days": 1,
    }
