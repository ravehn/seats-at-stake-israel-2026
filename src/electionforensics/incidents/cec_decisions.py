"""The public register of decisions of the election-committee chairs (CEC chair and the district chairs),
downloaded page by page by tools/fetch_cec_decisions.py into data/raw/knesset26/cec_decisions/.

One row per decision: case number and type (תב"כ central, תר"מ district, ...), petitioner, respondent, district,
local authority (set for district cases only), judge, status, decision date, the decision summary shown on the
site and the PDF link. Signage-related decisions are flagged by keywords in the decision summary and the PDF title:
`signage` (any propaganda signage matter) and `removal` (removal, destruction, defacement or theft of signs).
Local authorities are resolved to CBS locality codes by name; regional councils have no locality code. The
register spans the whole term of the 26th committee (from March 2023), so the district decisions (תר"מ) are
mostly about the municipal elections of February 2024: counts for the Knesset campaign need `since`.
Files are data: verified against MANIFEST.json, parsed, never executed.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pandas as pd

from electionforensics.config import DATA_RAW
from electionforensics.ingest.cbs import load_cbs

DECISIONS_DIR = DATA_RAW / "knesset26" / "cec_decisions"
SIGNAGE_RE = re.compile(r"שלט(?!ון)|שילוט|מודעה|מודעות|פלקט|(?<!ה)כרז|תליי|דגל")  # not שלטון (government)
CAMPAIGN_START = "2026-07-29"  # 90 days before 27.10.2026, § 2 of the propaganda law
REMOVAL_RE = re.compile(r"הסר|הסיר|הוסר|מסיר|השחת|הושחת|משחית|קריע|נקרע|קרע|תלש|נתלש|גניב|נגנב|גנב|הורד|הוריד|מוריד|עקיר|נעקר|עקר|שריפ|נשרף|שרף|הצת|הצית")
AUTHORITY_PREFIXES = ("עיריית", "עירית", "המועצה המקומית", "מועצה מקומית", "המועצה האזורית", "מועצה אזורית",
                      "מועצה מקומית תעשייתית", "מ.מ.", "מ.א.", "עיריה", "עירייה")
REGIONAL_RE = re.compile(r"מועצה ה?אזורית|^מ\.א\.")
LOCAL_RE = re.compile(r"מועצה ה?מקומית|^מ\.מ\.")
HEBREW_LETTERS = re.compile(r"[^א-ת]")


def _norm(name: str) -> str:
    """Hebrew letters only, doubled vav/yod collapsed, so spelling variants (הרצליה/הרצלייה, קרית/קריית) meet."""
    s = HEBREW_LETTERS.sub("", name)
    return s.replace("יי", "י").replace("וו", "ו")


def strip_authority(name: str) -> tuple[str, str]:
    """('bare name', 'municipality' | 'local_council' | 'regional' | 'other') for a local-authority string."""
    s = name.strip()
    kind = "other"
    if REGIONAL_RE.search(s):
        kind = "regional"
    elif s.startswith(("עיריית", "עירית", "עיריה", "עירייה")):
        kind = "municipality"
    elif LOCAL_RE.search(s):
        kind = "local_council"
    for p in sorted(AUTHORITY_PREFIXES, key=len, reverse=True):
        if s.startswith(p):
            s = s[len(p):].strip()
            break
    return s, kind


def resolve_authorities(names: pd.Series, cbs: pd.DataFrame | None = None) -> pd.DataFrame:
    """locality_code (Int64) and authority_kind for every local-authority string; regional councils stay unresolved."""
    cbs = load_cbs() if cbs is None else cbs
    lookup: dict[str, int] = {}
    for code, nm in zip(cbs["locality_code"], cbs["cbs_name"], strict=True):
        if pd.notna(code) and pd.notna(nm):
            lookup.setdefault(_norm(str(nm)), int(code))
    codes: list[int | None] = []
    kinds: list[str] = []
    for raw in names:
        if raw is None or (isinstance(raw, float) and pd.isna(raw)) or not str(raw).strip():
            codes.append(None)
            kinds.append("")
            continue
        bare, kind = strip_authority(str(raw))
        kinds.append(kind)
        codes.append(None if kind == "regional" else lookup.get(_norm(bare)))
    return pd.DataFrame({"locality_code": pd.array(codes, dtype="Int64"), "authority_kind": kinds}, index=names.index)


def verify_manifest(directory: Path) -> dict[str, Any]:
    manifest: dict[str, Any] = json.loads((directory / "MANIFEST.json").read_text(encoding="utf-8"))
    for name, digest in manifest["files"].items():
        actual = hashlib.sha256((directory / name).read_bytes()).hexdigest()
        if actual != digest:
            raise ValueError(f"{name}: SHA-256 {actual} differs from MANIFEST.json {digest}")
    return manifest


def load_decisions(directory: Path = DECISIONS_DIR, cbs: pd.DataFrame | None = None) -> pd.DataFrame:
    """All downloaded decisions as one table, duplicates by case number and date dropped, signage flags set."""
    manifest = verify_manifest(directory)
    rows: list[dict[str, Any]] = []
    for name in manifest["files"]:
        doc = json.loads((directory / name).read_text(encoding="utf-8"))
        for item in doc.get("Results") or []:
            d = item.get("Data") or {}
            files = d.get("lastdecfile") or []
            info = d.get("moreinfo") or {}
            rows.append({"casenum": d.get("casenum"), "casetype": d.get("casetype"), "knesset": d.get("knessetnum"),
                         "petitioner": d.get("requestman"), "respondent": d.get("responseman"), "district": d.get("district"),
                         "local_authority": d.get("localauthority"), "judge": d.get("judgename"), "status": d.get("applystatus"),
                         "decided": d.get("lastdecisiondate"), "summary": info.get("DescriptionBlankTextString") or "",
                         "pdf_name": files[0].get("DisplayName") if files else None,
                         "pdf_url": files[0].get("FileName") if files else None, "page": name})
    df = pd.DataFrame(rows)
    if df.empty:
        raise ValueError(f"no decisions under {directory}")
    df = df.drop_duplicates(subset=["casenum", "decided", "pdf_url"]).reset_index(drop=True)
    df["decided"] = pd.to_datetime(df["decided"], format="%d/%m/%Y", errors="coerce")
    text = df["summary"].fillna("") + " " + df["pdf_name"].fillna("")
    df["signage"] = text.str.contains(SIGNAGE_RE)
    df["removal"] = df["signage"] & text.str.contains(REMOVAL_RE)
    res = resolve_authorities(df["local_authority"], cbs)
    df["locality_code"] = res["locality_code"]
    df["authority_kind"] = res["authority_kind"]
    df.attrs["manifest"] = {k: v for k, v in manifest.items() if k != "files"}
    return df


def by_locality(df: pd.DataFrame, since: str | None = None) -> pd.DataFrame:
    """Decisions per named local authority: counts overall, signage-related and removal-related, with dates.
    `since` (ISO date) keeps decisions from that day on, e.g. CAMPAIGN_START for the Knesset campaign."""
    sub = df[df["local_authority"].notna() & (df["local_authority"].astype(str).str.strip() != "")].copy()
    if since is not None:
        sub = sub[sub["decided"] >= pd.Timestamp(since)]
    if sub.empty:
        return pd.DataFrame(columns=["local_authority", "locality_code", "authority_kind", "n_decisions", "n_signage",
                                     "n_removal", "first", "last", "petitioners", "cases"])
    g = sub.groupby("local_authority", sort=False).agg(
        locality_code=("locality_code", "first"), authority_kind=("authority_kind", "first"),
        n_decisions=("casenum", "size"), n_signage=("signage", "sum"), n_removal=("removal", "sum"),
        first=("decided", "min"), last=("decided", "max"),
        petitioners=("petitioner", lambda s: "; ".join(sorted({str(x) for x in s if pd.notna(x)}))),
        cases=("casenum", lambda s: "; ".join(str(x) for x in s)))
    g["n_signage"] = g["n_signage"].astype(int)
    g["n_removal"] = g["n_removal"].astype(int)
    return g.sort_values(["n_removal", "n_signage", "n_decisions"], ascending=False).reset_index()
