from __future__ import annotations

from pathlib import Path

import pandas as pd

from paper_code.config import load_best_model_spec
from paper_code.paths import PackagePaths


def load_result_tables(
    hospital: str,
    root: Path | None = None,
) -> dict[str, pd.DataFrame]:
    paths = PackagePaths.from_root(root)
    final = paths.final_results(hospital)
    return {
        "overall": pd.read_csv(final / "ensemble_metrics_overall.csv"),
        "by_seed": pd.read_csv(final / "ensemble_metrics_by_seed.csv"),
        "confidence_intervals": pd.read_csv(final / "ensemble_ci_per_drug.csv"),
    }


def result_overview(hospital: str, root: Path | None = None) -> pd.DataFrame:
    spec = load_best_model_spec(hospital, root)
    overall = load_result_tables(hospital, root)["overall"]
    selected = overall.loc[overall["label"] == f"blend_w{spec.ensemble_weight_s11:.2f}"].copy()
    selected.insert(0, "hospital", hospital)
    selected["weight_s11"] = spec.ensemble_weight_s11
    selected["weight_s04"] = spec.ensemble_weight_s04
    return selected
