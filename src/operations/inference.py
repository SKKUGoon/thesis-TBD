"""Load a hash-verified TimesFM checkpoint and test masked Linux inference."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import resource
import time
from pathlib import Path
from typing import Any

import numpy as np
import torch
from timesfm3.torch.timesfm3_forecaster import TimesFM3Forecaster

from src.methods.timesfm.adapter import MaskedTimesFM

CHECKPOINT_REVISION = "43046b85ec22d584a13f8098c2ed39c889e129c2"
CHECKPOINT_HASHES = {
    "config.json": "ff17bbc07b792c5a904cca265b8468579d736a4fe84981da25eb871b0a125bc6",
    "model.safetensors": "a7592b0a8432baee54483254e5647856911ce69e09d09a9bb65904b2d98f17da",
}


def verify_checkpoint_files(checkpoint: Path) -> dict[str, str]:
    """Read and hash checkpoint files; reject missing or changed files."""
    hashes: dict[str, str] = {}
    for name, expected in CHECKPOINT_HASHES.items():
        digest = hashlib.sha256()
        with (checkpoint / name).open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        actual = digest.hexdigest()
        if actual != expected:
            raise ValueError(f"CHECKPOINT_HASH_MISMATCH:{name}")
        hashes[name] = actual
    return hashes


def load_verified_model(checkpoint: Path, device: str = "cpu") -> MaskedTimesFM:
    """Verify local weights and load them without any network download."""
    verify_checkpoint_files(checkpoint)
    torch.set_num_threads(2)
    model = TimesFM3Forecaster.from_pretrained(
        str(checkpoint), device=device, local_files_only=True
    )
    return MaskedTimesFM(model, device)


def run_linux_preflight(checkpoint: Path, output: Path) -> dict[str, Any]:
    """Run actual inference and persist success or failure using synthetic inputs."""
    started = time.perf_counter()
    result: dict[str, Any] = {
        "status": "running",
        "platform": platform.platform(),
        "architecture": platform.machine(),
        "torch": torch.__version__,
        "numpy": np.__version__,
        "checkpoint_revision": CHECKPOINT_REVISION,
        "device": "cpu",
        "synthetic_only": True,
        "contexts": [],
    }
    try:
        result["checkpoint_hashes"] = verify_checkpoint_files(checkpoint)
        adapter = load_verified_model(checkpoint)
        rng = np.random.default_rng(20261021)
        for context in (112, 56):
            target = rng.uniform(1, 20, (1, 3, context)).astype("float32")
            patients = rng.uniform(1, 10, (1, 69, context)).astype("float32")
            known = rng.uniform(0, 1, (1, 16, context + 14)).astype("float32")
            target[:, :, 4] = np.nan
            inference_started = time.perf_counter()
            predicted = adapter.predict_masked(target, patients, known, 28)
            altered = patients.copy()
            altered[:, :, :-28] = 99999
            repeated = adapter.predict_masked(target, altered, known, 28)
            invariant = bool(np.allclose(predicted, repeated, atol=1e-5))
            if not invariant:
                raise ValueError("MASKED_PATIENT_INVARIANCE_FAILED")
            result["contexts"].append({
                "context": context,
                "shape": list(predicted.shape),
                "finite": bool(np.isfinite(predicted).all()),
                "masked_patient_invariant": invariant,
                "median_sum": float(np.maximum(0, predicted[..., 4]).sum()),
                "seconds_for_two_inferences": time.perf_counter() - inference_started,
            })
        result["status"] = "succeeded"
    except Exception as error:
        result.update(status="failed", error_type=type(error).__name__, error=str(error))
        raise
    finally:
        result["elapsed_seconds"] = time.perf_counter() - started
        result["max_rss_bytes"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (
            1024 if platform.system() == "Linux" else 1
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    print(json.dumps(run_linux_preflight(arguments.checkpoint, arguments.output)))
