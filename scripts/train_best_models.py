#!/usr/bin/env python3
from __future__ import annotations

import argparse

from paper_code.modeling import train_best_components


def main() -> None:
    parser = argparse.ArgumentParser(description="Train the S04/S11 five-seed component models.")
    parser.add_argument("--hospital", choices=["epurun", "seosong"], required=True)
    parser.add_argument("--root", help="paper_package root; auto-detected by default")
    parser.add_argument("--device", choices=["cpu", "mps", "cuda"], default="cpu")
    parser.add_argument("--force", action="store_true", help="Retrain completed runs")
    args = parser.parse_args()
    summary = train_best_components(args.hospital, args.root, args.device, args.force)
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
