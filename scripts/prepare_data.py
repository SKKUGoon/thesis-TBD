#!/usr/bin/env python3
from __future__ import annotations

import argparse

from paper_code.paths import PackagePaths
from paper_code.preprocessing import build_model_ready_data, preprocess_epurun, preprocess_seosong


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild processed hospital parquet files.")
    parser.add_argument("--hospital", choices=["epurun", "seosong", "all"], default="all")
    parser.add_argument("--root", help="paper_package root; auto-detected by default")
    args = parser.parse_args()
    paths = PackagePaths.from_root(args.root)
    hospitals = ["epurun", "seosong"] if args.hospital == "all" else [args.hospital]
    for hospital in hospitals:
        output = paths.processed(hospital)
        if hospital == "epurun":
            preprocess_epurun(
                paths.raw_hospital("epurun") / "patient_epurun_20251009_011300.csv",
                paths.references / "ATC_20250912_110516.csv",
                paths.references / "kcd.parquet",
                output,
            )
        else:
            preprocess_seosong(
                paths.raw_hospital("seosong"), paths.references / "kcd.parquet", output
            )
        build_model_ready_data(hospital, output, paths.references / "data_calendar.parquet")
        print(f"Prepared {hospital}: {output}")


if __name__ == "__main__":
    main()
