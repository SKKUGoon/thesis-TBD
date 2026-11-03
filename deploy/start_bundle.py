"""Verify and start an exported server bundle with fresh local credentials."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys


def start(root: Path) -> None:
    manifest = json.loads((root / "bundle-manifest.json").read_text())
    architecture = subprocess.check_output(["docker", "info", "--format", "{{.Architecture}}"], text=True).strip()
    architecture = {"aarch64": "arm64", "x86_64": "amd64"}.get(architecture, architecture)
    if any(image["architecture"] != architecture for image in manifest["images"]):
        raise RuntimeError("컨테이너 이미지와 서버 CPU 구조가 다릅니다. 해당 서버에서 이미지를 빌드해 주십시오.")
    for relative, expected in manifest["files"].items():
        with (root / relative).open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != expected:
            raise RuntimeError(f"PACKAGE_HASH_MISMATCH:{relative}")
    subprocess.run(["docker", "load", "-i", str(root / "images.tar")], check=True)
    subprocess.run([sys.executable, "deploy/prepare.py", "--checkpoint", "model"], cwd=root, check=True)
    subprocess.run(["docker", "compose", "-f", "deploy/compose.yaml", "up", "-d", "--no-build"], cwd=root, check=True)
    subprocess.run([sys.executable, "deploy/mint_token.py"], cwd=root, check=True)
    print("실행 주소: http://127.0.0.1:8090")


if __name__ == "__main__":
    start(Path(__file__).resolve().parents[1])
