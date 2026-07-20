#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from paper_code.reporting.result_description import write_result_description_markdown


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the paper result-description tables.")
    parser.add_argument("--root", help="paper_package root; auto-detected by default")
    parser.add_argument("--output", help="Markdown output path")
    args = parser.parse_args()
    output_path = write_result_description_markdown(
        Path(args.root) if args.root else None,
        Path(args.output) if args.output else None,
    )
    print(output_path)


if __name__ == "__main__":
    main()
