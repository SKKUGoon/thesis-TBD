from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from numbers import Integral, Real
from pathlib import Path

import pandas as pd

from paper_code.config import SUPPORTED_HOSPITALS
from paper_code.paths import PackagePaths

HOSPITAL_ORDER = ("epurun", "seosong")
HOSPITAL_DATA_SOURCES = {
    "epurun": "Patient-prescription extract",
    "seosong": "Inpatient, outpatient, and ward workbooks",
}
HOSPITAL_MEDICATION_IDENTIFIERS = {
    "epurun": "ATC-mapped medication code",
    "seosong": "Hospital order code",
}
SEX_COLUMNS = ("Female", "Male")
AGE_GROUPS = {
    "age_0_10": "0-9",
    "age_10_20": "10-19",
    "age_20_30": "20-29",
    "age_30_40": "30-39",
    "age_40_50": "40-49",
    "age_50_60": "50-59",
    "age_60_70": "60-69",
    "age_70_80": "70-79",
    "age_80_plus": "80+",
}
DIAGNOSIS_GROUPS = tuple("ABCDEFGHIJKLMNOPQRSTUVWXYZ") + ("Other",)
WEEKDAYS = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)


@dataclass(frozen=True)
class HospitalDataDescription:
    hospital: str
    data_source: str
    observation_period: str
    unit_of_analysis: str
    daily_observations: int
    patient_feature_composition: str
    patient_side_covariates: int
    calendar_covariates: int
    total_model_covariates: int
    medication_target_identifier: str
    medication_targets: int
    target_value: str
    zero_valued_target_cells: str
    median_target_active_day_rate: str


@dataclass(frozen=True)
class DataDescriptionTables:
    overview: pd.DataFrame
    demographic_patient_days: pd.DataFrame
    diagnosis_counts: pd.DataFrame
    weekday_visit_boxplot: pd.DataFrame
    metric_definitions: pd.DataFrame


def _validate_daily_model_matrices(
    hospital: str,
    patient_covariates: pd.DataFrame,
    medication_targets: pd.DataFrame,
    model_covariates: pd.DataFrame,
) -> None:
    if not patient_covariates.index.equals(medication_targets.index):
        raise ValueError(f"{hospital}: patient and medication dates do not align")
    if not patient_covariates.index.equals(model_covariates.index):
        raise ValueError(f"{hospital}: patient and model-covariate dates do not align")
    expected_index = pd.date_range(
        patient_covariates.index.min(),
        patient_covariates.index.max(),
        freq="D",
    )
    if not patient_covariates.index.equals(expected_index):
        raise ValueError(f"{hospital}: processed data do not have a complete daily index")
    if patient_covariates.isna().any(axis=None):
        raise ValueError(f"{hospital}: patient covariates contain missing values")
    if medication_targets.isna().any(axis=None):
        raise ValueError(f"{hospital}: medication targets contain missing values")
    if not set(patient_covariates.columns).issubset(model_covariates.columns):
        raise ValueError(f"{hospital}: model covariates do not contain all patient features")


def load_hospital_data_description(
    hospital: str,
    root: Path | None = None,
) -> HospitalDataDescription:
    if hospital not in SUPPORTED_HOSPITALS:
        raise ValueError(
            f"Unsupported hospital {hospital!r}; expected one of {sorted(SUPPORTED_HOSPITALS)}."
        )

    processed_dir = PackagePaths.from_root(root).processed(hospital)
    patient_covariates = pd.read_parquet(processed_dir / "data_patient.parquet")
    medication_targets = pd.read_parquet(processed_dir / "data_prescription.parquet")
    model_covariates = pd.read_parquet(processed_dir / "lavar_ready_x.parquet")
    _validate_daily_model_matrices(
        hospital,
        patient_covariates,
        medication_targets,
        model_covariates,
    )

    zero_percent = (medication_targets == 0).to_numpy().mean() * 100
    median_active_percent = (medication_targets > 0).mean(axis=0).median() * 100
    calendar_covariate_count = len(set(model_covariates.columns) - set(patient_covariates.columns))

    return HospitalDataDescription(
        hospital=hospital.title(),
        data_source=HOSPITAL_DATA_SOURCES[hospital],
        observation_period=(
            f"{patient_covariates.index.min():%Y-%m-%d} to "
            f"{patient_covariates.index.max():%Y-%m-%d}"
        ),
        unit_of_analysis="Calendar day",
        daily_observations=len(patient_covariates),
        patient_feature_composition="2 sex + 9 age + 27 diagnosis counts",
        patient_side_covariates=patient_covariates.shape[1],
        calendar_covariates=calendar_covariate_count,
        total_model_covariates=model_covariates.shape[1],
        medication_target_identifier=HOSPITAL_MEDICATION_IDENTIFIERS[hospital],
        medication_targets=medication_targets.shape[1],
        target_value="Distinct patients per medication per day",
        zero_valued_target_cells=f"{zero_percent:.2f}%",
        median_target_active_day_rate=f"{median_active_percent:.2f}%",
    )


def build_data_description_table(root: Path | None = None) -> pd.DataFrame:
    descriptions = [
        asdict(load_hospital_data_description(hospital, root)) for hospital in HOSPITAL_ORDER
    ]
    hospital_rows = pd.DataFrame(descriptions).set_index("hospital")
    table = hospital_rows.transpose().reset_index(names="Characteristic")
    table["Characteristic"] = [
        "Data source",
        "Observation period",
        "Unit of analysis",
        "Daily observations",
        "Patient-feature composition",
        "Patient-side covariates",
        "Calendar covariates used by the model",
        "Total model covariates",
        "Medication target identifier",
        "Medication targets",
        "Target value",
        "Zero-valued cells in the target matrix",
        "Median target active-day rate",
    ]
    return table


def _load_patient_covariates(hospital: str, root: Path | None) -> pd.DataFrame:
    processed_dir = PackagePaths.from_root(root).processed(hospital)
    return pd.read_parquet(processed_dir / "data_patient.parquet")


def build_demographic_patient_day_table(root: Path | None = None) -> pd.DataFrame:
    patient_covariates = {
        hospital: _load_patient_covariates(hospital, root) for hospital in HOSPITAL_ORDER
    }
    rows: list[dict[str, str | int]] = []
    for column in SEX_COLUMNS:
        rows.append(
            {
                "Measure": "Sex",
                "Category": column,
                **{
                    hospital.title(): int(patient_covariates[hospital][column].sum())
                    for hospital in HOSPITAL_ORDER
                },
            }
        )
    for column, label in AGE_GROUPS.items():
        rows.append(
            {
                "Measure": "Age group",
                "Category": label,
                **{
                    hospital.title(): int(patient_covariates[hospital][column].sum())
                    for hospital in HOSPITAL_ORDER
                },
            }
        )
    return pd.DataFrame(rows)


def build_diagnosis_count_table(root: Path | None = None) -> pd.DataFrame:
    patient_covariates = {
        hospital: _load_patient_covariates(hospital, root) for hospital in HOSPITAL_ORDER
    }
    return pd.DataFrame(
        [
            {
                "KCD group": diagnosis_group,
                "Epurun prescription-linked diagnosis records": int(
                    patient_covariates["epurun"][diagnosis_group].sum()
                ),
                "Seosong patient-day diagnosis-category counts": int(
                    patient_covariates["seosong"][diagnosis_group].sum()
                ),
            }
            for diagnosis_group in DIAGNOSIS_GROUPS
        ]
    )


def calculate_tukey_boxplot_values(daily_visits: pd.Series) -> dict[str, int | float]:
    first_quartile = float(daily_visits.quantile(0.25))
    third_quartile = float(daily_visits.quantile(0.75))
    interquartile_range = third_quartile - first_quartile
    lower_fence = first_quartile - 1.5 * interquartile_range
    upper_fence = third_quartile + 1.5 * interquartile_range
    lower_whisker = float(daily_visits.loc[daily_visits >= lower_fence].min())
    upper_whisker = float(daily_visits.loc[daily_visits <= upper_fence].max())
    outlier_count = int(((daily_visits < lower_whisker) | (daily_visits > upper_whisker)).sum())
    return {
        "Days": len(daily_visits),
        "Mean visits": float(daily_visits.mean()),
        "Lower whisker": lower_whisker,
        "Q1": first_quartile,
        "Median": float(daily_visits.median()),
        "Q3": third_quartile,
        "Upper whisker": upper_whisker,
        "Outlier days": outlier_count,
    }


def build_weekday_visit_boxplot_table(root: Path | None = None) -> pd.DataFrame:
    rows: list[dict[str, str | int | float]] = []
    for hospital in HOSPITAL_ORDER:
        patient_covariates = _load_patient_covariates(hospital, root)
        daily_visits = patient_covariates.loc[:, list(SEX_COLUMNS)].sum(axis=1)
        for weekday_number, weekday in enumerate(WEEKDAYS):
            weekday_visits = daily_visits.loc[daily_visits.index.dayofweek == weekday_number]
            rows.append(
                {
                    "Hospital": hospital.title(),
                    "Weekday": weekday,
                    **calculate_tukey_boxplot_values(weekday_visits),
                }
            )
    return pd.DataFrame(rows)


def build_metric_definition_table(root: Path | None = None) -> pd.DataFrame:
    target_matrices = {
        hospital: pd.read_parquet(
            PackagePaths.from_root(root).processed(hospital) / "data_prescription.parquet"
        )
        for hospital in HOSPITAL_ORDER
    }
    zero_cell_values: dict[str, str] = {}
    active_day_values: dict[str, str] = {}
    for hospital, medication_targets in target_matrices.items():
        zero_cells = int((medication_targets == 0).to_numpy().sum())
        zero_percent = zero_cells / medication_targets.size * 100
        median_active_days = float((medication_targets > 0).sum(axis=0).median())
        median_active_rate = median_active_days / len(medication_targets) * 100
        zero_cell_values[hospital.title()] = (
            f"{zero_cells:,} / {medication_targets.size:,} ({zero_percent:.2f}%)"
        )
        active_day_values[hospital.title()] = (
            f"{median_active_days:,.0f} / {len(medication_targets):,} days "
            f"({median_active_rate:.2f}%)"
        )

    return pd.DataFrame(
        [
            {
                "Metric": "Visit count used in weekday table",
                "Definition": (
                    "Female + Male daily counts; counts sex-classifiable patient-days, "
                    "not period-unique patients"
                ),
                "Epurun": "Same definition",
                "Seosong": "May exclude records whose sex could not be derived",
            },
            {
                "Metric": "Boxplot whiskers",
                "Definition": (
                    "Smallest and largest observations within Q1 - 1.5 x IQR and Q3 + 1.5 x IQR"
                ),
                "Epurun": "Tukey rule",
                "Seosong": "Tukey rule",
            },
            {
                "Metric": "Zero-valued cells in target matrix",
                "Definition": (
                    "Day-medication combinations with zero distinct patients; zero is "
                    "observed no activity, not missing data"
                ),
                "Epurun": zero_cell_values["Epurun"],
                "Seosong": zero_cell_values["Seosong"],
            },
            {
                "Metric": "Median target active-day rate",
                "Definition": (
                    "For each medication, active days divided by study days; reported "
                    "value is the median across medications"
                ),
                "Epurun": active_day_values["Epurun"],
                "Seosong": active_day_values["Seosong"],
            },
            {
                "Metric": "Diagnosis count basis",
                "Definition": "Hospital-specific executed preprocessing contract",
                "Epurun": "Prescription-linked primary + secondary diagnosis records",
                "Seosong": "Distinct patient-day-diagnosis-category counts",
            },
        ]
    )


def build_all_data_description_tables(root: Path | None = None) -> DataDescriptionTables:
    return DataDescriptionTables(
        overview=build_data_description_table(root),
        demographic_patient_days=build_demographic_patient_day_table(root),
        diagnosis_counts=build_diagnosis_count_table(root),
        weekday_visit_boxplot=build_weekday_visit_boxplot_table(root),
        metric_definitions=build_metric_definition_table(root),
    )


def render_data_description_markdown(table: pd.DataFrame) -> str:
    def format_value(value: object) -> str:
        if isinstance(value, Integral):
            return f"{value:,}"
        if isinstance(value, Real):
            return f"{value:,.2f}"
        return str(value)

    columns = [str(column) for column in table.columns]
    header = "| " + " | ".join(columns) + " |"
    separator = "|" + "|".join("---" for _ in columns) + "|"
    rows = [
        "| " + " | ".join(format_value(value) for value in row) + " |"
        for row in table.itertuples(index=False, name=None)
    ]
    return "\n".join([header, separator, *rows]) + "\n"


def render_all_data_description_tables_markdown(tables: DataDescriptionTables) -> str:
    sections = (
        ("Dataset overview", tables.overview),
        ("Sex and age-group patient-day counts", tables.demographic_patient_days),
        ("Diagnosis-group counts", tables.diagnosis_counts),
        ("Weekday visit distribution and Tukey boxplot values", tables.weekday_visit_boxplot),
        ("Metric definitions and count bases", tables.metric_definitions),
    )
    return "\n".join(
        f"## {heading}\n\n{render_data_description_markdown(table).rstrip()}\n"
        for heading, table in sections
    )


def write_data_description_markdown(
    output_path: Path,
    root: Path | None = None,
) -> Path:
    tables = build_all_data_description_tables(root)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        render_all_data_description_tables_markdown(tables),
        encoding="utf-8",
    )
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate the Seosong-Epurun data-description table."
    )
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    paths = PackagePaths.from_root(args.root)
    output_path = args.output or paths.reports / "paper" / "DATA_DESCRIPTION.md"
    written_path = write_data_description_markdown(output_path, paths.root)
    print(written_path)


if __name__ == "__main__":
    main()
