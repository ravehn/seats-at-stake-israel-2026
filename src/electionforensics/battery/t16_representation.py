"""T16 — representation distortion: how votes became seats.

Israel allocates 120 seats among lists above the electoral threshold by the Bader–Ofer method,
which for the national total is the D'Hondt (Jefferson) highest-averages rule with surplus-vote
agreements treated as joint lists and then split by the same rule. This test computes the seat
allocation from the published totals, the votes that elected nobody ("wasted"), each list's
distance from the threshold, the sensitivity of the last seats to small vote shifts, and
counterfactual allocations (no agreements; threshold 2 % or 1 %; pure proportionality). Nothing
here alleges wrongdoing: it measures the lawful distortion so that it can be compared with any
alleged one.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from electionforensics.battery.base import TestResult, register
from electionforensics.config import SEATS, TAXONOMY, THRESHOLD_BY_KNESSET
from electionforensics.ingest.cec import ElectionData
from electionforensics.model.features import Features


def dhondt(votes: dict[str, int], seats: int) -> dict[str, int]:
    alloc = dict.fromkeys(votes, 0)
    for _ in range(seats):
        best = max(votes, key=lambda p: (votes[p] / (alloc[p] + 1), votes[p]))
        alloc[best] += 1
    return alloc


def bader_ofer(votes: dict[str, int], seats: int, agreements: list[tuple[str, str]]) -> dict[str, int]:
    """D'Hondt over blocs (agreement pairs merged), then D'Hondt inside each bloc."""
    bloc_of: dict[str, str] = {}
    for a, b in agreements:
        if a in votes and b in votes:
            bloc_of[a] = bloc_of[b] = f"{a}+{b}"
    bloc_votes: dict[str, int] = {}
    for p, v in votes.items():
        bloc_votes[bloc_of.get(p, p)] = bloc_votes.get(bloc_of.get(p, p), 0) + v
    bloc_alloc = dhondt(bloc_votes, seats)
    out: dict[str, int] = {}
    for bloc, s in bloc_alloc.items():
        members = [p for p in votes if bloc_of.get(p, p) == bloc]
        if len(members) == 1:
            out[members[0]] = s
        else:
            out.update(dhondt({p: votes[p] for p in members}, s))
    return out


def load_agreements(knesset: int) -> tuple[list[tuple[str, str]], bool]:
    p = Path(TAXONOMY) / "surplus_agreements.yaml"
    if not p.exists():
        return [], False
    doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    entry = doc.get(knesset) or {}
    pairs = [tuple(x) for x in entry.get("pairs", [])]
    return [(str(a), str(b)) for a, b in pairs], bool(entry.get("verified", False))


def allocate(data: ElectionData, threshold: float, agreements: list[tuple[str, str]], seats: int = SEATS) -> dict[str, Any]:
    cols = [f"v_{p}" for p in data.parties]
    totals = data.stations[cols].sum()
    votes = {str(c)[2:]: int(v) for c, v in totals.items()}
    valid = sum(votes.values())
    eligible_lists = {p: v for p, v in votes.items() if v >= threshold * valid}
    alloc = bader_ofer(eligible_lists, seats, agreements) if eligible_lists else {}
    wasted = sum(v for p, v in votes.items() if p not in eligible_lists)
    return {"votes": votes, "valid": valid, "seats": alloc, "wasted_votes": wasted,
            "wasted_share": wasted / valid if valid else float("nan"), "threshold_votes": threshold * valid}


@register("T16")
def t16_representation(feat: Features, data: ElectionData | None = None) -> TestResult:
    if data is None:
        from electionforensics.ingest.cec import load_election
        data = load_election(feat.knesset)
    k = feat.knesset
    thr = THRESHOLD_BY_KNESSET[k]
    agreements, verified = load_agreements(k)
    base = allocate(data, thr, agreements)
    no_agr = allocate(data, thr, [])
    thr2 = allocate(data, 0.02, agreements)
    thr1 = allocate(data, 0.01, agreements)
    thr0 = allocate(data, 0.0, agreements)
    votes, valid = base["votes"], base["valid"]
    rows = []
    for p, v in sorted(votes.items(), key=lambda x: -x[1]):
        s = base["seats"].get(p, 0)
        rows.append({
            "knesset": k, "party": p, "votes": v, "share": v / valid, "seats": s,
            "votes_per_seat": v / s if s else None, "above_threshold": v >= thr * valid,
            "margin_to_threshold_votes": v - thr * valid,
            "seats_no_agreements": no_agr["seats"].get(p, 0), "seats_thr2": thr2["seats"].get(p, 0),
            "seats_thr1": thr1["seats"].get(p, 0), "seats_thr0": thr0["seats"].get(p, 0),
            "proportional_seats": SEATS * v / valid,
        })
    units = pd.DataFrame(rows)
    seated = units[units["seats"] > 0]
    summary = {
        "threshold": thr, "threshold_votes": base["threshold_votes"], "valid_votes": valid,
        "agreements": agreements, "agreements_verified": verified,
        "wasted_votes": base["wasted_votes"], "wasted_share": base["wasted_share"],
        "lists_seated": len(seated), "lists_total": len(units),
        "seat_shift_vs_no_agreements": int((units["seats"] - units["seats_no_agreements"]).abs().sum() // 2),
        "seat_shift_vs_thr2": int((units["seats"] - units["seats_thr2"]).abs().sum() // 2),
        "max_votes_per_seat": float(seated["votes_per_seat"].max()) if len(seated) else None,
        "min_votes_per_seat": float(seated["votes_per_seat"].min()) if len(seated) else None,
        "closest_to_threshold": units.iloc[(units["margin_to_threshold_votes"]).abs().argsort()[:3]][["party", "votes", "share", "margin_to_threshold_votes"]].to_dict("records"),
    }
    notes = [] if verified else ["surplus agreements for this election are not verified; see taxonomy/surplus_agreements.yaml"]
    return TestResult("T16", "Representation: votes to seats", k, "party", units, summary, {"threshold": thr}, notes)
