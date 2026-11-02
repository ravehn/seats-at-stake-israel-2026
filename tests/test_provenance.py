"""EF-005/EF-010: stale caches, exact time bounds and typed station units."""

import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from electionforensics.battery.t14_count_dynamics import t14_count_dynamics
from electionforensics.calibrate.baseline import expand_history_units
from electionforensics.ingest.snapshots import Snapshot
from electionforensics.model.features import Features
from electionforensics.provenance import analysis_context, battery_source, input_manifest, require_calibrated


def _battery(root: Path) -> Path:
    raw = root / "raw.csv"
    raw.write_bytes(b"original evidence")
    d = root / "knesset26"
    d.mkdir()
    source = {"file": raw.name, "path": str(raw), "sha256": hashlib.sha256(raw.read_bytes()).hexdigest(),
              "snapshot_ts": "20261029T004731Z", "analysis_sha256": analysis_context()}
    for i in range(1, 18):
        (d / f"T{i:02}.json").write_text(json.dumps({"summary": {"_source": source}}), encoding="utf-8")
    return d


def test_battery_rejects_mixed_sources_and_changed_raw(tmp_path: Path) -> None:
    d = _battery(tmp_path)
    assert battery_source(26, tmp_path, complete=True)["file"] == "raw.csv"
    t16 = json.loads((d / "T16.json").read_text())
    t16["summary"]["_source"]["sha256"] = "different"
    (d / "T16.json").write_text(json.dumps(t16))
    with pytest.raises(ValueError, match="mixed"):
        battery_source(26, tmp_path)
    t16["summary"]["_source"] = json.loads((d / "T01.json").read_text())["summary"]["_source"]
    (d / "T16.json").write_text(json.dumps(t16))
    (tmp_path / "raw.csv").write_bytes(b"changed")
    with pytest.raises(ValueError, match="SHA-256"):
        battery_source(26, tmp_path)


def test_report_rejects_changed_battery_table(tmp_path: Path) -> None:
    d = _battery(tmp_path)
    table = d / "T09.parquet"
    table.write_bytes(b"first table")
    (d / "CAL.json").write_text(json.dumps({"params": {"inputs": input_manifest(26, tmp_path)}}))
    require_calibrated(26, tmp_path)
    table.write_bytes(b"new table")
    with pytest.raises(ValueError, match="stale calibration"):
        require_calibrated(26, tmp_path)


def test_collapsed_history_maps_only_to_real_boxes() -> None:
    universe = pd.DataFrame({"locality_code": [1, 1, 1], "station": ["14.1", "14.2", "15"], "station_num": [14, 14, 15]})
    history = pd.DataFrame({"locality_code": [1, 1], "station": ["14", "15"], "score": [8.0, 2.0], "detail": ["x", "y"]})
    result = expand_history_units(history, universe)
    assert list(result["station"]) == ["14.1", "14.2", "15"]
    assert list(result["score"]) == [8, 8, 2]
    assert "объединённых" in result.iloc[0]["detail"]


def test_dynamics_does_not_look_past_selected_snapshot(clean_features: Features) -> None:
    from electionforensics.ingest.cec import ElectionData

    feat = clean_features
    feat.knesset = 26
    st = feat.stations.drop(columns=["sector", "turnout", "leader_share"]).copy()
    st["is_external"] = False
    data = ElectionData(26, st, feat.parties, Path("x"), "sha", "utf-8", None, [])
    a, b = Snapshot("20261028T000000Z", data), Snapshot("20261029T000000Z", data)
    result = t14_count_dynamics(feat, [a, b], as_of=a.ts)
    assert result.summary["last"] == a.ts and result.summary["n_snapshots"] == 1
    assert t14_count_dynamics(feat, [], as_of=a.ts).summary["skipped"]


def test_code_fingerprint_covers_analysis_but_not_presentation() -> None:
    """Editing a template or the CLI must not invalidate every battery result (29.10.2026)."""
    from electionforensics.provenance import analysis_paths

    rel = {p.as_posix().split("src/electionforensics/", 1)[-1] for p in analysis_paths() if "electionforensics" in p.parts}
    assert any(r.startswith("battery/") for r in rel) and any(r.startswith("ingest/") for r in rel)
    assert any(r.startswith("calibrate/") for r in rel) and "config.py" in rel and "taxonomy.py" in rel
    assert not any(r.startswith(("present/", "incidents/", "link/", "influence/")) or r in {"cli.py", "provenance.py"} for r in rel)


def test_datagov_files_match_the_download_manifest() -> None:
    """EF-017: every data.gov.il file in git must be byte-identical to the download its manifest records."""
    from electionforensics.config import DATA_RAW

    manifest = DATA_RAW / "datagov" / "MANIFEST.json"
    if not manifest.exists():
        pytest.skip("no data.gov.il manifest on disk")

    def walk(o: object) -> list[dict[str, str]]:
        if isinstance(o, dict):
            own = [o] if "sha256" in o and "file" in o else []
            return own + [x for v in o.values() for x in walk(v)]
        if isinstance(o, list):
            return [x for v in o for x in walk(v)]
        return []

    items = walk(json.loads(manifest.read_text(encoding="utf-8")))
    present = [(it, manifest.parent / it["file"]) for it in items if (manifest.parent / it["file"]).exists()]
    assert present, "manifest names no file that is on disk"
    bad = [it["file"] for it, p in present if hashlib.sha256(p.read_bytes()).hexdigest() != it["sha256"]]
    assert bad == [], f"raw files differ from their recorded download (line endings?): {bad}"
