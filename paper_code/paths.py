from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PackagePaths:
    root: Path

    @classmethod
    def from_root(cls, root: Path | str | None = None) -> "PackagePaths":
        resolved = (
            Path(root).expanduser().resolve() if root else Path(__file__).resolve().parents[1]
        )
        return cls(resolved)

    @property
    def raw(self) -> Path:
        return self.root / "data" / "raw"

    @property
    def references(self) -> Path:
        return self.raw / "references"

    def raw_hospital(self, hospital: str) -> Path:
        return self.raw / hospital

    def processed(self, hospital: str) -> Path:
        return self.root / "data" / "processed" / hospital

    def stage1(self, hospital: str) -> Path:
        return self.root / "models" / hospital / "stage1"

    def stage2(self, hospital: str) -> Path:
        return self.root / "models" / hospital / "stage2"

    def results(self, hospital: str) -> Path:
        return self.root / "results" / hospital

    def run_metadata(self, hospital: str) -> Path:
        return self.results(hospital) / "run_metadata"

    def selection(self, hospital: str) -> Path:
        return self.results(hospital) / "selection"

    def final_results(self, hospital: str) -> Path:
        return self.results(hospital) / "final"

    @property
    def reports(self) -> Path:
        return self.root / "reports"
