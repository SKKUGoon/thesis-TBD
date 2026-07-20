from __future__ import annotations

import ast
import json
from pathlib import Path

import pandas as pd
import torch

from paper_code.config import load_best_model_spec
from paper_code.paths import PackagePaths


def verify_package(root: Path | None = None) -> dict:
    paths = PackagePaths.from_root(root)
    notebooks = sorted(paths.root.rglob("*.ipynb"))
    expected_notebooks = {
        paths.reports / "notebooks" / "epurun_result_report.ipynb",
        paths.reports / "notebooks" / "seosong_result_report.ipynb",
    }
    if set(notebooks) != expected_notebooks:
        raise ValueError(f"Unexpected notebooks: {notebooks}")
    for notebook in notebooks:
        payload = json.loads(notebook.read_text(encoding="utf-8"))
        for index, cell in enumerate(payload["cells"]):
            if cell.get("cell_type") == "code":
                ast.parse("".join(cell.get("source", [])), filename=f"{notebook}:cell-{index}")

    summary = {"notebooks": len(notebooks), "hospitals": {}}
    for hospital in ("epurun", "seosong"):
        spec = load_best_model_spec(hospital, paths.root)
        processed = paths.processed(hospital)
        frames = {path.stem: pd.read_parquet(path) for path in processed.glob("*.parquet")}
        if set(frames) != {"data_patient", "data_prescription", "lavar_ready_x", "lavar_ready_y"}:
            raise ValueError(f"Unexpected processed files for {hospital}: {sorted(frames)}")
        if not frames["data_patient"].index.equals(frames["data_prescription"].index):
            raise ValueError(f"Intermediate data index mismatch for {hospital}")
        if not frames["lavar_ready_x"].index.equals(frames["lavar_ready_y"].index):
            raise ValueError(f"Model-ready data index mismatch for {hospital}")

        stage2_paths = sorted(paths.stage2(hospital).glob("*/*.pt"))
        signatures = set()
        for checkpoint in stage2_paths:
            payload = torch.load(checkpoint, map_location="cpu", weights_only=True)
            signature = payload["stage1_signature"]
            signatures.add(signature)
            if not (paths.stage1(hospital) / f"{signature}.pth").is_file():
                raise FileNotFoundError(f"Missing Stage 1 state for {checkpoint}")
        stage1_signatures = {path.stem for path in paths.stage1(hospital).glob("*.pth")}
        if signatures != stage1_signatures:
            raise ValueError(f"Unreferenced or missing Stage 1 states for {hospital}")

        overall = pd.read_csv(paths.final_results(hospital) / "ensemble_metrics_overall.csv")
        observed = float(
            overall.loc[
                overall["label"] == f"blend_w{spec.ensemble_weight_s11:.2f}",
                "mean_of_mean_mse",
            ].iloc[0]
        )
        if abs(observed - spec.expected_mean_mse) > 1e-12:
            raise ValueError(f"Metric mismatch for {hospital}: {observed}")
        summary["hospitals"][hospital] = {
            "stage1": len(stage1_signatures),
            "stage2": len(stage2_paths),
            "mean_mse": observed,
        }
    return summary
