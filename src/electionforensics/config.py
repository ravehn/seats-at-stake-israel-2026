"""Paths and constants. Everything observable from the environment is discovered, not hardcoded."""

from __future__ import annotations

import os
from pathlib import Path

ROOT: Path = Path(os.environ.get("EF_ROOT", Path(__file__).resolve().parents[2]))
DATA_RAW: Path = ROOT / "data" / "raw"
DATA_INTERIM: Path = ROOT / "data" / "interim"
DATA_PROCESSED: Path = ROOT / "data" / "processed"
TAXONOMY: Path = ROOT / "taxonomy"
REPORTS: Path = ROOT / "reports"
LOGS: Path = ROOT / "logs"

#: Electoral threshold as a fraction of valid votes (since the 20th Knesset, 2015).
THRESHOLD_BY_KNESSET: dict[int, float] = {
    16: 0.015,
    17: 0.02,
    18: 0.02,
    19: 0.02,
    20: 0.0325,
    21: 0.0325,
    22: 0.0325,
    23: 0.0325,
    24: 0.0325,
    25: 0.0325,
    26: 0.0325,
}
SEATS: int = 120

#: Browser user agent: CEC hosts answer 403/302 to non-browser agents (verified 2026-10-28).
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)


def ensure_dirs() -> None:
    for p in (DATA_INTERIM, DATA_PROCESSED, REPORTS, LOGS):
        p.mkdir(parents=True, exist_ok=True)
