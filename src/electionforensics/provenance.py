"""Bind derived tables and calibration to immutable inputs; refuse silent cache mixing."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from electionforensics.config import DATA_PROCESSED, DATA_RAW, ROOT, TAXONOMY
from electionforensics.ingest.cec import ElectionData, load_election
from electionforensics.ingest.registry import stations_path


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


#: The code that determines derived tables: ingest, features, the battery, calibration and their shared
#: constants. Presentation (`present/`), the CLI, the evidence base, the influence layer and this module
#: cannot change a battery result, so editing them does not invalidate an issue (29.10.2026, after EF-005).
ANALYSIS_MODULES = ("config.py", "taxonomy.py", "ingest", "model", "battery", "calibrate")


def analysis_paths() -> list[Path]:
    pkg = Path(__file__).parent
    paths: list[Path] = []
    for name in ANALYSIS_MODULES:
        target = pkg / name
        paths.extend(target.rglob("*.py") if target.is_dir() else [target])
    paths.extend(TAXONOMY.rglob("*.yaml"))
    for directory in (DATA_RAW / "datagov", DATA_RAW / "cbs"):
        paths.extend(p for p in directory.rglob("*") if p.is_file())
    return sorted(p for p in paths if "__pycache__" not in p.parts)


def analysis_context() -> str:
    """Analysis code, taxonomy and static references. New snapshots and presentation changes do not invalidate an old issue."""
    entries = {str(p.relative_to(ROOT)): sha256(p) for p in analysis_paths()}
    return hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest()


def source_record(data: ElectionData, context: str) -> dict[str, Any]:
    return {"file": data.source.name, "path": str(data.source.resolve()), "sha256": data.sha256,
            "snapshot_ts": data.snapshot_ts, "analysis_sha256": context}


def source_path(knesset: int, source: dict[str, Any]) -> Path:
    if source.get("path"):
        return Path(source["path"])
    return DATA_RAW / "knesset26" / "snapshots" / source["file"] if knesset == 26 else stations_path(knesset)


def battery_source(knesset: int, root: Path = DATA_PROCESSED, complete: bool = False) -> dict[str, Any]:
    paths = sorted((root / f"knesset{knesset}").glob("T[0-9][0-9].json"))
    if not paths or (complete and {p.stem for p in paths} != {f"T{i:02}" for i in range(1, 18)}):
        raise ValueError("incomplete battery; run ef battery for this election")
    sources = [json.loads(p.read_text(encoding="utf-8"))["summary"].get("_source", {}) for p in paths]
    source = sources[0]
    if not source.get("analysis_sha256") or any(s != source for s in sources):
        raise ValueError("mixed or legacy battery inputs; rebuild the complete battery")
    if source["analysis_sha256"] != analysis_context():
        raise ValueError("analysis code/reference data changed; rebuild the complete battery")
    p = source_path(knesset, source)
    if not p.is_file() or sha256(p) != source.get("sha256"):
        raise ValueError("raw input missing or SHA-256 mismatch")
    return dict(source)


def pinned_election(knesset: int, source: dict[str, Any]) -> ElectionData:
    data = load_election(knesset, source_path(knesset, source))
    if data.sha256 != source.get("sha256"):
        raise ValueError("pinned raw input SHA-256 mismatch")
    return data


def input_manifest(knesset: int, root: Path = DATA_PROCESSED) -> dict[str, Any]:
    source = battery_source(knesset, root)
    paths = [p for d in root.glob("knesset*") for p in d.glob("T*") if p.suffix in {".json", ".parquet"}]
    return {"source": source, "artifacts": {str(p.relative_to(root)): sha256(p) for p in sorted(paths)}}


def require_calibrated(knesset: int, root: Path = DATA_PROCESSED) -> dict[str, Any]:
    source = battery_source(knesset, root, complete=True)
    p = root / f"knesset{knesset}" / "CAL.json"
    if not p.exists():
        raise ValueError("missing calibration; run ef calibrate")
    cal = json.loads(p.read_text(encoding="utf-8"))
    if cal.get("params", {}).get("inputs") != input_manifest(knesset, root):
        raise ValueError("stale calibration; run ef calibrate after the battery")
    for name, expected in cal.get("calibration_tables", {}).items():
        if sha256(p.parent / name) != expected:
            raise ValueError("calibration table SHA-256 mismatch")
    return source
