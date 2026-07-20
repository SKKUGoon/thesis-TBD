from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

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
DATE_START = "2021-01-01"
DATE_END = "2024-12-31"


def preprocess_epurun(
    raw_csv: Path,
    atc_csv: Path,
    kcd_path: Path,
    output_dir: Path,
) -> tuple[Path, Path]:
    """Convert the original Epurun patient CSV into daily X/Y parquet files."""
    output_dir.mkdir(parents=True, exist_ok=True)
    atc = pd.read_csv(atc_csv, encoding="cp949")
    atc["제품코드"] = atc["제품코드"].map(str)

    df = pd.read_csv(raw_csv, low_memory=False)
    df = df.loc[df["단가"] > 0]
    df = df.loc[~df["청구코드"].str.strip().str.match(r"^[a-zA-Z]", na=False)]
    df = df.loc[~df["청구코드"].str.contains(r"앞치마|에어메트1|에어메트2|치매장갑", na=False)]
    df = df.loc[df["date"] != 20000000].copy()

    df["입원일"] = pd.to_datetime(df["입원일"], format="%m/%d/%Y")
    df["퇴원일"] = pd.to_datetime(df["퇴원일"], format="%m/%d/%Y")
    df["visit_date"] = pd.to_datetime(df["date"].map(str), format="%Y%m%d")
    df["date"] = df["입원일"]
    df = df.loc[~((df["입원일"] != df["입원일"]) & (df["퇴원일"] != df["입원일"]))]
    df = df.loc[~((df["입원일"] > df["date"]) | (df["퇴원일"] < df["date"]))].copy()
    df["sex"] = df["male"].apply(lambda value: 1 if value == 1 else 0)
    df = pd.merge(df, atc, left_on="청구코드", right_on="제품코드")
    df = df.rename(
        columns={
            "차트번호": "id",
            "kcd1_e": "primary_diagnosis",
            "kcd2_e": "secondary_diagnosis",
            "ATC코드 명칭": "atc_code_name",
            "ATC코드": "prescription_code",
            "q_per_c": "quantity",
            "제품명": "product_name",
            "제품코드": "product_code",
            "date": "start_date",
            "visit_date": "prescription_date",
        }
    )

    kcd_labels = pd.read_parquet(kcd_path)["질병 분류코드"].map(
        lambda value: value[0]
    ).drop_duplicates().tolist() + ["Other"]
    patient = df[["id", "prescription_date", "sex", "age", "kcd1", "kcd2"]].copy()
    patient["id"] = patient["id"].astype(int)
    patient["age"] = patient["age"].astype(int)
    patient["prescription_date"] = pd.to_datetime(patient["prescription_date"])
    patient["kcd1"] = patient["kcd1"].str.strip()
    patient["kcd2"] = patient["kcd2"].str.strip()
    patient["kcd1_big"] = patient["kcd1"].apply(
        lambda value: value[0] if isinstance(value, str) else "Other"
    )
    patient["kcd2_big"] = patient["kcd2"].apply(
        lambda value: value[0] if isinstance(value, str) else "Other"
    )
    patient["age_group"] = pd.cut(patient["age"], bins=AGE_BINS, labels=AGE_LABELS, right=False)

    daily_index = pd.date_range(DATE_START, DATE_END, freq="D")
    kcd1 = (
        patient.pivot_table(
            index="prescription_date",
            columns="kcd1_big",
            values="id",
            aggfunc="count",
            fill_value=0,
        )
        .reindex(columns=kcd_labels, fill_value=0)
        .reindex(daily_index, fill_value=0)
    )
    kcd2 = (
        patient.pivot_table(
            index="prescription_date",
            columns="kcd2_big",
            values="id",
            aggfunc="count",
            fill_value=0,
        )
        .reindex(columns=kcd_labels, fill_value=0)
        .reindex(daily_index, fill_value=0)
    )

    patient_sex = (
        patient[["sex", "id", "prescription_date"]]
        .drop_duplicates()
        .replace({"sex": {1: "Male", 0: "Female"}})
        .groupby(["prescription_date", "sex"])
        .size()
        .unstack(fill_value=0)
        .reindex(columns=["Female", "Male"], fill_value=0)
    )
    patient_age = (
        patient[["age_group", "id", "prescription_date"]]
        .drop_duplicates()
        .groupby(["prescription_date", "age_group"], observed=False)
        .size()
        .unstack(fill_value=0)
        .reindex(columns=AGE_LABELS, fill_value=0)
    )
    daily_patient = pd.concat([patient_sex, patient_age, kcd1 + kcd2], axis=1).fillna(0)
    daily_patient.index = pd.to_datetime(daily_patient.index)
    daily_patient = daily_patient.reindex(daily_index, fill_value=0).sort_index()

    prescription_df = df[["id", "prescription_date", "prescription_code"]]
    prescription = prescription_df.drop_duplicates().pivot_table(
        index="prescription_date",
        columns="prescription_code",
        values="id",
        aggfunc="count",
        fill_value=0,
    )
    prescription.index = pd.to_datetime(prescription.index)
    prescription = prescription.reindex(daily_index, fill_value=0).sort_index()

    patient_path = output_dir / "data_patient.parquet"
    prescription_path = output_dir / "data_prescription.parquet"
    daily_patient.to_parquet(patient_path)
    prescription.to_parquet(prescription_path)
    return patient_path, prescription_path
