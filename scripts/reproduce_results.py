#!/usr/bin/env python3
from __future__ import annotations

import argparse

from paper_code.modeling import reproduce_ensemble_results


def main() -> None:
    parser = argparse.ArgumentParser(description="Reproduce final ensemble result artifacts.")
    parser.add_argument("--hospital", choices=["epurun", "seosong"], required=True)
    parser.add_argument("--root", help="paper_package root; auto-detected by default")
    parser.add_argument("--device", choices=["cpu", "mps", "cuda"], default="cpu")
    parser.add_argument(
        "--reconstruct-predictions",
        action="store_true",
        help="Rebuild per-drug predictions from rolling checkpoints (expensive)",
    )
    args = parser.parse_args()
    outputs = reproduce_ensemble_results(
        args.hospital,
        args.root,
        args.reconstruct_predictions,
        args.device,
    )
    for name, path in outputs.items():
        print(f"{name}: {path}")


if __name__ == "__main__":
    main()
