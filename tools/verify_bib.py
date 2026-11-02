"""Check every DOI in a BibTeX file against Crossref and write a JSON report next to the file.

Run from the repository root:  uv run python -X utf8 tools/verify_bib.py docs/article/references.bib
For each entry with a `doi` field the script asks https://api.crossref.org/works/<doi>, compares the first words of the
title and the year, and records found / mismatch / error; entries without a DOI are listed as `no_doi` so that the
reader can see which references rest on a URL alone. Network access only to api.crossref.org; nothing is executed.
"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

UA = "ElectionForensics/verify_bib (https://github.com/ravehn/seats-at-stake-israel-2026)"


def parse_bib(text: str) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for m in re.finditer(r"@(\w+)\s*\{\s*([^,\s]+)\s*,(.*?)\n\}", text, flags=re.S):
        kind, key, body = m.group(1), m.group(2), m.group(3)
        fields = {"_type": kind, "_key": key}
        for fm in re.finditer(r"(\w+)\s*=\s*(\{(?:[^{}]|\{[^{}]*\})*\}|\"[^\"]*\"|[^,\n]+)\s*,?", body):
            val = fm.group(2).strip().strip(",").strip()
            if val[:1] in "{\"":
                val = val[1:-1]
            fields[fm.group(1).lower()] = re.sub(r"[{}]", "", val).strip()
        entries.append(fields)
    return entries


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def check(entries: list[dict[str, str]]) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    with httpx.Client(timeout=30, headers={"User-Agent": UA}, follow_redirects=True) as client:
        for e in entries:
            doi = e.get("doi", "").replace("https://doi.org/", "").strip()
            rec: dict[str, object] = {"key": e["_key"], "doi": doi or None, "status": "no_doi"}
            if doi:
                try:
                    r = client.get(f"https://api.crossref.org/works/{doi}")
                    if r.status_code == 200:
                        msg = r.json()["message"]
                        title = (msg.get("title") or [""])[0]
                        issued = (msg.get("issued", {}).get("date-parts") or [[None]])[0][0]
                        container = (msg.get("container-title") or [""])[0]
                        want = _norm(e.get("title", ""))[:40]
                        got = _norm(title)
                        n = min(len(want), len(got), 25)
                        same_title = bool(want) and n >= 12 and got[:n] == want[:n]
                        same_year = str(issued) == str(e.get("year", "")) or issued is None
                        rec.update({"status": "found" if same_title else "title_mismatch", "crossref_title": title,
                                    "crossref_year": issued, "crossref_container": container, "year_match": same_year})
                    else:
                        rec.update({"status": f"http_{r.status_code}"})
                except httpx.HTTPError as exc:
                    rec.update({"status": "error", "error": str(exc)[:200]})
                time.sleep(0.3)
            out.append(rec)
    return out


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else Path("docs/article/references.bib")
    entries = parse_bib(path.read_text(encoding="utf-8"))
    report = check(entries)
    summary = {"checked_at": datetime.now(UTC).isoformat(timespec="seconds"), "file": path.name, "entries": len(entries),
               "found": sum(1 for r in report if r["status"] == "found"),
               "title_mismatch": sum(1 for r in report if r["status"] == "title_mismatch"),
               "no_doi": sum(1 for r in report if r["status"] == "no_doi"),
               "errors": sum(1 for r in report if str(r["status"]).startswith(("http_", "error"))), "items": report}
    out = path.with_name("references_check.json")
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    for r in report:
        print(f"{r['status']:<15} {r['key']:<28} {r.get('doi') or ''}")
    print(f"{summary['found']} found, {summary['title_mismatch']} title mismatches, {summary['no_doi']} without DOI, {summary['errors']} errors -> {out}")
    return 1 if summary["title_mismatch"] or summary["errors"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
