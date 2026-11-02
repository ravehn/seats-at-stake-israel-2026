"""Photographs and videos handed in by a witness: manifest (hash, EXIF time, GPS, device) and import as exhibits.

Files live under `inbox/case-NN/...`; the manifest CSV (`incidents/exhibits/<case>_media.csv`) is the
git-tracked description of what was handed in, with the SHA-256 of every original. Import is idempotent
on the hash: a file already in the base is linked, not duplicated.
"""

from __future__ import annotations

import csv
import hashlib
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

from electionforensics.config import ROOT
from electionforensics.incidents.store import Store

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".heic"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".3gp"}
DEFAULT_WITNESS = "editor (first-hand witness)"
_TAG_MAKE, _TAG_MODEL, _TAG_DATETIME = 271, 272, 306
_TAG_DT_ORIGINAL, _TAG_OFFSET_ORIGINAL = 36867, 36881
_IFD_EXIF, _IFD_GPS = 0x8769, 0x8825


@dataclass
class MediaRecord:
    file: str
    kind: str
    sha256: str
    size: int
    taken_at: str | None = None
    gps_lat: float | None = None
    gps_lon: float | None = None
    device: str | None = None
    width: int | None = None
    height: int | None = None


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _dms(value: Any, ref: Any) -> float | None:
    try:
        d, m, s = (float(x) for x in value)
    except (TypeError, ValueError):
        return None
    deg = d + m / 60 + s / 3600
    return -deg if str(ref).upper() in ("S", "W") else deg


def _iso(exif_dt: Any, offset: Any = None) -> str | None:
    s = str(exif_dt or "").strip()
    if len(s) < 19 or s.startswith("0000"):
        return None
    iso = s[:10].replace(":", "-") + "T" + s[11:19]
    return iso + str(offset).strip() if offset else iso


def exif_info(path: Path) -> dict[str, Any]:
    """EXIF time (original, with offset when present), GPS, device and pixel size; empty dict when unreadable."""
    try:
        from PIL import Image

        with Image.open(path) as img:
            width, height = img.size
            exif = img.getexif()
            sub = dict(exif.get_ifd(_IFD_EXIF)) if exif else {}
            gps = dict(exif.get_ifd(_IFD_GPS)) if exif else {}
    except Exception:  # any unreadable file is reported without EXIF, never skipped
        return {}
    taken = _iso(sub.get(_TAG_DT_ORIGINAL) or exif.get(_TAG_DT_ORIGINAL), sub.get(_TAG_OFFSET_ORIGINAL)) or _iso(exif.get(_TAG_DATETIME))
    make, model = str(exif.get(_TAG_MAKE) or "").strip(), str(exif.get(_TAG_MODEL) or "").strip()
    device = " ".join(x for x in (make, model) if x) or None
    lat = _dms(gps.get(2), gps.get(1)) if gps else None
    lon = _dms(gps.get(4), gps.get(3)) if gps else None
    return {"taken_at": taken, "gps_lat": lat, "gps_lon": lon, "device": device, "width": width, "height": height}


def manifest(directory: Path) -> list[MediaRecord]:
    """Every image/video under the directory (recursive, name order) with hash and EXIF fields."""
    out: list[MediaRecord] = []
    for p in sorted(directory.rglob("*")):
        suffix = p.suffix.lower()
        if not p.is_file() or suffix not in IMAGE_SUFFIXES | VIDEO_SUFFIXES:
            continue
        kind = "photo" if suffix in IMAGE_SUFFIXES else "video"
        info = exif_info(p) if kind == "photo" else {}
        out.append(MediaRecord(file=str(p.relative_to(directory)).replace("\\", "/"), kind=kind, sha256=sha256_of(p),
                               size=p.stat().st_size, **info))
    return out


def write_manifest(records: list[MediaRecord], out_csv: Path) -> Path:
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[fl.name for fl in fields(MediaRecord)])
        w.writeheader()
        for r in records:
            w.writerow(asdict(r))
    return out_csv


def import_media(directory: Path, store: Store, slug: str, witness: str = DEFAULT_WITNESS, role: str = "corroborates",
                 note: str | None = None) -> tuple[int, int, list[MediaRecord]]:
    """Link every file under the directory to the case as an exhibit; returns (new, total, records)."""
    case = store.conn.execute('SELECT id FROM "case" WHERE slug=?', (slug,)).fetchone()
    if not case:
        raise KeyError(f"no case with slug {slug}")
    sid = store.upsert_source(witness, "witness")
    records = manifest(directory)
    new = 0
    for r in records:
        eid = store.exhibit_by_sha(r.sha256)
        if eid is None:
            try:
                rel = str((directory / r.file).resolve().relative_to(ROOT.resolve()))
            except ValueError:
                rel = str(directory / r.file)
            meta = f"{r.kind} handed in by witness; device {r.device or '?'}; gps {r.gps_lat},{r.gps_lon}; {r.width}x{r.height}"
            eid = store.add_exhibit(sid, None, None, sha256=r.sha256, capture_path=rel, captured_at=r.taken_at,
                                    media_kind=r.kind, author=witness, note=(note + "; " if note else "") + meta)
            new += 1
        store.link(eid, int(case["id"]), role, note="witness media")
    store.grade_case(int(case["id"]))
    return new, len(records), records
