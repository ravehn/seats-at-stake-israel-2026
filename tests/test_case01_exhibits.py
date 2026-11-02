"""Appendix H: the photographs of case file No. 1 published in full (docs/article/case-01/) and their manifest.

The twenty-three original files are published byte-exact under their own names with everything they contain; the
manifest repeats each file's metadata and SHA-256 with the number, site and content of the public summary's table;
`preview/sheet-N.jpg` are the contact sheets the PDF shows, nine photographs each. Checked: every file's hash equals the
manifest and the summary's list; the originals carry EXIF (nothing was stripped); a contact sheet exists for every
photograph; where the internal exhibits register is present (the project checkout, not the package), the manifest
equals it row for row.
"""

from __future__ import annotations

import csv
import hashlib
import re

import pytest
from PIL import Image

from electionforensics.config import ROOT

CASE = ROOT / "docs" / "article" / "case-01"
SUMMARY = ROOT / "docs" / "article" / "case-01-yokneam-public.md"
REGISTER_FIELDS = ("file", "sha256", "size", "taken_at", "gps_lat", "gps_lon", "device", "width", "height")


def _manifest() -> list[dict[str, str]]:
    with (CASE / "manifest.csv").open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def test_every_original_is_published_in_full_and_matches_the_manifest_and_the_summary() -> None:
    rows = _manifest()
    assert [int(r["no"]) for r in rows] == list(range(1, 24))
    assert sorted(p.name for p in CASE.glob("*.jpg")) == sorted(r["file"] for r in rows)  # no stray file, none missing
    for r in rows:
        path = CASE / r["file"]
        data = path.read_bytes()
        assert len(data) == int(r["size"]) and hashlib.sha256(data).hexdigest() == r["sha256"], r["no"]
        im = Image.open(path)
        assert im.getexif(), r["no"]  # the metadata is published with the file, nothing stripped
        assert (CASE / "preview" / f"sheet-{(int(r['no']) - 1) // 9 + 1}.jpg").exists(), r["no"]
        assert r["site"] and r["content"] and r["taken_at"] and r["kind"] in ("night", "context")
    assert sum(1 for r in rows if r["kind"] == "context") == 2
    assert sum(1 for r in rows if r["gps_lat"]) == 11  # eleven photographs carry GPS, as the summary says
    md = SUMMARY.read_text(encoding="utf-8")
    listed = {int(m.group(1)): (m.group(2), m.group(3))
              for m in re.finditer(r"^- No\. (\d+): \[`([^`]+)`\]\(case-01/\2\) — `([0-9a-f]{64})`$", md, re.M)}
    assert listed == {int(r["no"]): (r["file"], r["sha256"]) for r in rows}
    assert len(re.findall(r"^\| \d+ \| \d\d\.10 \d\d:\d\d \| ", md, re.M)) == 23  # the table itself lists all 23


def test_manifest_equals_the_internal_exhibits_register_when_present() -> None:
    register = [ROOT / "incidents" / "exhibits" / f for f in ("case-01_media.csv", "case-01_context.csv")]
    if not all(p.exists() for p in register):
        pytest.skip("the internal register is not in this checkout (the replication package does not carry it)")
    reg: dict[str, dict[str, str]] = {}
    for p in register:
        with p.open(encoding="utf-8-sig", newline="") as fh:
            reg.update({r["file"]: r for r in csv.DictReader(fh)})
    man = {r["file"]: r for r in _manifest()}
    assert set(man) == set(reg)
    for f, r in man.items():
        assert {k: r[k] for k in REGISTER_FIELDS} == {k: reg[f][k] for k in REGISTER_FIELDS}, f
