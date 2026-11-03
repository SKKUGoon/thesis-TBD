"""Export a self-contained private server package with images, code and verified weights."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess


def export(output: Path) -> None:
    """Write a new package; exclude databases, credentials, research data and run outputs."""
    root = Path(__file__).resolve().parents[1]
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    for directory in ("src", "architect", "deploy", "frontend"):
        shutil.copytree(root / directory, output / directory,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".state", ".env", "node_modules", "dist", "test-results", "playwright-report"))
    shutil.copytree(root / "tests/operations", output / "tests/operations", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copyfile(root / ".dockerignore", output / ".dockerignore")
    shutil.copytree(root / "deploy/.state/model", output / "model")
    images = ["paper-operations:eval-v1", "paper-operations-ml:eval-v1", "paper-operations-web:eval-v1", "postgres:18", "rabbitmq:4.1.4-management"]
    metadata = json.loads(subprocess.check_output(["docker", "image", "inspect", *images], text=True))
    subprocess.run(["docker", "save", "-o", str(output / "images.tar"), *images], check=True)
    manifest = {"synthetic_evaluation_only": True, "credentials_included": False,
                "images": [{"tag": tag, "id": image["Id"], "architecture": image["Architecture"]} for tag, image in zip(images, metadata)],
                "files": {}}
    for file in sorted(output.rglob("*")):
        if file.is_file():
            with file.open("rb") as stream:
                manifest["files"][str(file.relative_to(output))] = hashlib.file_digest(stream, "sha256").hexdigest()
    (output / "bundle-manifest.json").write_text(json.dumps(manifest, indent=2)+"\n")
    print(str(output))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    export(parser.parse_args().output)
