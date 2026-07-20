#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys

from paper_code.verify import verify_package

sys.dont_write_bytecode = True


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate the standalone paper package.")
    parser.add_argument("--root", help="paper_package root; auto-detected by default")
    args = parser.parse_args()
    print(json.dumps(verify_package(args.root), indent=2))


if __name__ == "__main__":
    main()
