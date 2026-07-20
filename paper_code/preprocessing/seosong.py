from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

REQUIRED_COLUMNS = ["DatChtNum", "ByuDisCod", "ManResNum", "ManComDte", "DatOdrCod"]
AGE_BINS = [0, 10, 20, 30, 40, 50, 60, 70, 80, np.inf]
AGE_LABELS = [
    "age_0_10",
    "age_10_20",
    "age_20_30",
    "age_30_40",
    "age_40_50",
    "age_50_60",
    "age_60_70",
    "age_70_80",
    "age_80_plus",
]


def _kcd_labels(kcd_path: Path) -> list[str]:
    if not kcd_path.exists():
        raise FileNotFoundError(f"KCD classification table not found: {kcd_path}")
    return pd.read_parquet(kcd_path)["질병 분류코드"].map(lambda value: str(value).strip()[:1]).loc[
        lambda series: series.str.match(r"^[A-Z]$", na=False)
    ].drop_duplicates().tolist() + ["Other"]


def xlsx_files(path: Path) -> list[Path]:
    return sorted(item for item in path.glob("*.xlsx") if not item.name.startswith("~$"))


def read_seosong_workbook(path: Path, source_type: str) -> list[pd.DataFrame]:
    frames: list[pd.DataFrame] = []
    workbook = pd.ExcelFile(path, engine="openpyxl")
    for sheet_name in workbook.sheet_names:
        frame = pd.read_excel(
            workbook,
            sheet_name=sheet_name,
            header=1,
            usecols=lambda column: str(column).strip() in REQUIRED_COLUMNS,
        )
        if frame.empty:
            continue
        frame.columns = [str(column).strip() for column in frame.columns]
        missing_columns = sorted(set(REQUIRED_COLUMNS) - set(frame.columns))
        if missing_columns:
            raise ValueError(
                f"Missing required columns in {path}, sheet {sheet_name!r}: {missing_columns}"
            )
        frame = frame[REQUIRED_COLUMNS].copy()
        frame["source_type"] = source_type
        frame["source_file"] = path.name
        frame["source_sheet"] = str(sheet_name)
        frames.append(frame)
    return frames


def load_raw_records(raw_root: Path) -> pd.DataFrame:
    sources = [
        ("inpatient", raw_root / "inpatient"),
        ("outpatient", raw_root / "outpatient"),
        ("ward_2025", raw_root / "ward_2025"),
    ]
    frames: list[pd.DataFrame] = []
    for source_type, directory in sources:
        for path in xlsx_files(directory):
            frames.extend(read_seosong_workbook(path, source_type))
    if not frames:
        raise RuntimeError(f"No Seosong workbook rows were loaded from {raw_root}")
    return pd.concat(frames, ignore_index=True)


def clean_text(series: pd.Series) -> pd.Series:
    cleaned = series.astype("string").str.strip().str.replace(r"\.0$", "", regex=True)
    return cleaned.replace({"": pd.NA, "nan": pd.NA, "NaN": pd.NA, "None": pd.NA})


def parse_dates(series: pd.Series) -> pd.Series:
    parsed = pd.to_datetime(series, errors="coerce", format="mixed")
    numeric = pd.to_numeric(series, errors="coerce")
    serial_mask = numeric.between(20000, 60000) & (parsed.isna() | parsed.dt.year.lt(1990))
    if serial_mask.any():
        parsed.loc[serial_mask] = pd.to_datetime(
            numeric.loc[serial_mask],
            unit="D",
            origin="1899-12-30",
        )
    return parsed.dt.normalize()


def resident_sex_digit(series: pd.Series) -> pd.Series:
    return clean_text(series).str.extract(r"^\s*\d{6}-?(\d)", expand=False)


def sex_from_resident(series: pd.Series) -> pd.Series:
    digit = resident_sex_digit(series)
    sex = pd.Series(pd.NA, index=series.index, dtype="string")
    sex.loc[digit.isin(list("13579"))] = "Male"
    sex.loc[digit.isin(list("24680"))] = "Female"
    return sex


def birth_year_from_resident(resident_series: pd.Series, date_series: pd.Series) -> pd.Series:
    resident = clean_text(resident_series)
    yy = pd.to_numeric(resident.str.extract(r"^\s*(\d{2})", expand=False), errors="coerce")
    digit = resident_sex_digit(resident_series)
    visit_year = date_series.dt.year
    birth_year = pd.Series(np.nan, index=resident_series.index, dtype="float64")
    birth_year.loc[digit.isin(list("1256"))] = 1900 + yy.loc[digit.isin(list("1256"))]
    birth_year.loc[digit.isin(list("3478"))] = 2000 + yy.loc[digit.isin(list("3478"))]
    birth_year.loc[digit.isin(list("90"))] = 1800 + yy.loc[digit.isin(list("90"))]
    missing = birth_year.isna() & yy.notna() & visit_year.notna()
    for century in (2000, 1900, 1800):
        candidate = century + yy
        age = visit_year - candidate
        use = missing & age.ge(0) & age.lt(120)
        birth_year.loc[use] = candidate.loc[use]
        missing = birth_year.isna() & yy.notna() & visit_year.notna()
    return birth_year


def normalize_records(raw: pd.DataFrame, kcd_labels: list[str]) -> pd.DataFrame:
    frame = raw.copy()
    frame["date"] = parse_dates(frame["ManComDte"])
    invalid_dates = raw["ManComDte"].notna() & frame["date"].isna()
    if invalid_dates.any():
        raise ValueError(
            "Seosong records contain "
            f"{int(invalid_dates.sum())} non-empty, unparseable completion dates."
        )
    frame["patient_id"] = clean_text(frame["DatChtNum"])
    missing_key = pd.Series(
        "missing_patient_" + frame.index.astype("string"),
        index=frame.index,
        dtype="string",
    )
    frame["patient_key"] = frame["patient_id"].fillna(missing_key)
    frame["diagnosis_code"] = clean_text(frame["ByuDisCod"])
    first = frame["diagnosis_code"].str.upper().str.extract(r"^([A-Z])", expand=False)
    frame["diagnosis_code_big"] = first.where(first.isin(kcd_labels), "Other").fillna("Other")
    frame["prescription_code"] = clean_text(frame["DatOdrCod"])
    frame["sex"] = sex_from_resident(frame["ManResNum"])
    birth_year = birth_year_from_resident(frame["ManResNum"], frame["date"])
    frame["age"] = frame["date"].dt.year - birth_year
    frame.loc[~frame["age"].between(0, 120), "age"] = np.nan
    frame["age_group"] = pd.cut(frame["age"], bins=AGE_BINS, labels=AGE_LABELS, right=False)
    return frame.loc[frame["date"].notna()].copy()


def build_patient_parquet(
    frame: pd.DataFrame,
    daily_index: pd.DatetimeIndex,
    kcd_labels: list[str],
) -> pd.DataFrame:
    patient = frame[["date", "patient_key", "sex", "age_group", "diagnosis_code_big"]].copy()
    sex = (
        patient.loc[patient["sex"].notna(), ["date", "patient_key", "sex"]]
        .drop_duplicates()
        .groupby(["date", "sex"])
        .size()
        .unstack(fill_value=0)
        .reindex(columns=["Female", "Male"], fill_value=0)
    )
    age = (
        patient.loc[patient["age_group"].notna(), ["date", "patient_key", "age_group"]]
        .drop_duplicates()
        .groupby(["date", "age_group"], observed=False)
        .size()
        .unstack(fill_value=0)
        .reindex(columns=AGE_LABELS, fill_value=0)
    )
    diagnosis = (
        patient[["date", "patient_key", "diagnosis_code_big"]]
        .drop_duplicates()
        .groupby(["date", "diagnosis_code_big"])
        .size()
        .unstack(fill_value=0)
        .reindex(columns=kcd_labels, fill_value=0)
    )
    result = pd.concat([sex, age, diagnosis], axis=1).fillna(0)
    result = result.reindex(daily_index, fill_value=0).sort_index().astype("int64")
    result.index.name = None
    return result


def build_prescription_parquet(frame: pd.DataFrame, daily_index: pd.DatetimeIndex) -> pd.DataFrame:
    source = frame.loc[
        frame["prescription_code"].notna(),
        ["date", "patient_key", "prescription_code"],
    ]
    result = (
        source.drop_duplicates(["date", "patient_key", "prescription_code"])
        .groupby(["date", "prescription_code"])
        .size()
        .unstack(fill_value=0)
        .reindex(daily_index, fill_value=0)
        .sort_index()
    )
    result = result.reindex(sorted(result.columns), axis=1).astype("int64")
    result.index.name = None
    return result


def preprocess_seosong(raw_root: Path, kcd_path: Path, output_dir: Path) -> tuple[Path, Path]:
    """Convert all consumed Seosong XLSX workbooks into daily X/Y parquet files."""
    output_dir.mkdir(parents=True, exist_ok=True)
    labels = _kcd_labels(kcd_path)
    frame = normalize_records(load_raw_records(raw_root), labels)
    if frame.empty:
        raise RuntimeError("No normalized Seosong rows are available")
    daily_index = pd.date_range(frame["date"].min(), frame["date"].max(), freq="D")
    patient = build_patient_parquet(frame, daily_index, labels)
    prescription = build_prescription_parquet(frame, daily_index)
    patient_path = output_dir / "data_patient.parquet"
    prescription_path = output_dir / "data_prescription.parquet"
    patient.to_parquet(patient_path)
    prescription.to_parquet(prescription_path)
    return patient_path, prescription_path
