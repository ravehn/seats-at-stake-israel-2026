"""Import a seed YAML of cases (hand-structured harvest) into the store, with locality codes from CBS."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from electionforensics.incidents.store import Store
from electionforensics.ingest.cbs import load_cbs


def normalize_name(name: str) -> str:
    """Spelling-insensitive key for Hebrew locality names: no spaces/hyphens/quotes, קריית→קרית."""
    s = str(name).strip().replace("קריית", "קרית")
    return "".join(ch for ch in s if ch not in " -'\"’`־")


def import_seed(path: Path, store: Store, captured_at: str | None = None) -> dict[str, int]:
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cbs = load_cbs()
    by_name = {normalize_name(n): int(c) for n, c in zip(cbs["cbs_name"], cbs["locality_code"], strict=True)}
    n_cases = n_exhibits = 0
    unmatched: list[str] = []
    for c in doc.get("cases", []):
        name = c.get("locality_name")
        code = by_name.get(normalize_name(name)) if name else None
        if name and code is None:
            unmatched.append(str(name))
        case_id = store.upsert_case(
            c["slug"], c["category"], c["title_ru"], locality_code=code, locality_name=name,
            place_detail=c.get("place_detail"), happened_from=c.get("happened_from"), happened_to=c.get("happened_to"),
            actors_alleged=c.get("actors_alleged"), victims=c.get("victims"), status=c.get("status", "open"),
            attribution=c.get("attribution", "unknown"), summary_ru=c.get("official"),
            false_flag_considered=1 if (c.get("attribution") == "disputed" or c.get("false_flag_considered")) else 0,
        )
        if c.get("number") is not None:  # the editor's case numbering survives a rebuild of the base
            store.set_number(c["slug"], int(c["number"]))
        for d in c.get("decisions", []):  # editor's rulings on the case (append-only; re-import adds none twice)
            if not store.decision_exists(case_id, d["decision"]):
                store.decide(case_id, d.get("by", "editor"), d["decision"], d.get("note"))
        n_cases += 1
        for s in c.get("sources", []):
            sid = store.upsert_source(s["name"], s.get("kind", "media"), url=s.get("url"), lang=s.get("lang"))
            role = "official" if s.get("kind") in ("police", "court", "cec", "official") else "reports"
            if store.seed_exhibit_exists(sid, case_id, s.get("url")):  # re-import of a seed: no duplicate placeholders
                continue
            eid = store.add_exhibit(sid, s.get("url"), text_original=None, lang=s.get("lang"),
                                    captured_at=captured_at, note=f"seed {path.name}")
            store.link(eid, case_id, role)
            n_exhibits += 1
        store.grade_case(case_id)
    return {"cases": n_cases, "exhibits": n_exhibits, "unmatched_localities": len(unmatched), **_unmatched(unmatched)}


def _unmatched(names: list[str]) -> dict[str, Any]:
    return {"unmatched_names": sorted(set(names))} if names else {}
