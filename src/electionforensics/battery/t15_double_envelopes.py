"""T15 — double envelopes (מעטפות כפולות / חיצוניות), the centrally counted vote.

Soldiers, diplomats, prisoners, hospital patients, polling-committee members away from home and
voters with disabilities vote in a double envelope that is opened and counted centrally after
election day and published under a pseudo-locality (code 0 / 875 / 99999 / 9999 over the years) as
batches of several hundred envelopes. It is 5–10 % of all votes (4.9 % in 2003, 9.7 % in 2022) and
the part of the count with the fewest observers. Three checks:

(a) composition — each list's share among the external envelopes minus its share at regular
    stations (the "gap", in points), and the gap's change since the previous election (the "gap
    shift"). The external electorate is stable in kind (young, mobile, few Arab or Haredi voters),
    so gaps are stable across elections; a list whose gap jumps is the signal.
(b) batches — leave-one-out binomial z of each batch's list shares and invalid share against the
    other batches; max |z| per batch. The batches are *not* exchangeable: the CEC sorts envelopes
    by source (soldiers, diplomats, prisoners, hospitals), and in 2013–2022 two thirds of the batches
    differ from the rest by more than 4 binomial sigmas. The z's therefore mean nothing on the
    textbook scale and are read only as percentiles of the 2013–2022 batch distribution.
(c) weight — the external share of all voters, the invalid share external vs regular, and the seats
    that move when the external envelopes are added (Bader–Ofer, as in T16).
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import numpy as np
import pandas as pd

from electionforensics.battery.base import TestResult, register
from electionforensics.battery.t09_siblings import _loo_z
from electionforensics.battery.t16_representation import allocate, load_agreements
from electionforensics.config import THRESHOLD_BY_KNESSET
from electionforensics.ingest.cec import ElectionData
from electionforensics.ingest.registry import ELECTIONS
from electionforensics.model.features import Features


def _shares(df: pd.DataFrame, parties: list[str]) -> pd.Series:
    tot = df[[f"v_{p}" for p in parties]].sum()
    tot.index = pd.Index(parties)
    return tot / tot.sum() if tot.sum() else tot * np.nan


def _seat_shift(data: ElectionData, reg: pd.DataFrame) -> dict[str, Any]:
    """Seats with the external envelopes minus seats from the regular stations alone."""
    k = data.knesset
    thr = THRESHOLD_BY_KNESSET.get(k)
    if thr is None:
        return {}
    agreements, _ = load_agreements(k)
    try:
        with_ext = allocate(data, thr, agreements).get("seats", {})
        without = allocate(replace(data, stations=reg), thr, agreements).get("seats", {})
    except (KeyError, ValueError, ZeroDivisionError):
        return {}
    changes = {p: int(with_ext.get(p, 0)) - int(without.get(p, 0)) for p in set(with_ext) | set(without)}
    changes = {p: d for p, d in changes.items() if d}
    return {"seats_moved": int(sum(abs(d) for d in changes.values()) // 2), "seat_changes": changes}


@register("T15")
def t15_double_envelopes(feat: Features, data: ElectionData | None = None, prev_data: ElectionData | None = None,
                         min_voters: int = 50) -> TestResult:
    k = feat.knesset
    if data is None:
        from electionforensics.ingest.cec import load_election
        data = load_election(k)
    st = data.stations
    ext = st[st["is_external"] & (st["voters"].fillna(0) > 0)].copy()
    reg = data.regular
    parties = data.parties
    if ext.empty:
        return TestResult("T15", "Double envelopes", k, "station", pd.DataFrame(),
                          {"skipped": "no external envelopes in the file", "external_share": 0.0, "n_batches": 0})

    # (a) composition and its shift against the previous election
    se, sr = _shares(ext, parties), _shares(reg, parties)
    tab = pd.DataFrame({"party": parties, "votes_ext": [int(ext[f"v_{p}"].sum()) for p in parties],
                        "votes_reg": [int(reg[f"v_{p}"].sum()) for p in parties],
                        "share_ext": se.to_numpy(), "share_reg": sr.to_numpy()})
    tab["gap_pp"] = 100 * (tab["share_ext"] - tab["share_reg"])
    tab["gap_prev_pp"] = np.nan
    prev_k: int | None = None
    if prev_data is None and (k - 1) in ELECTIONS:
        try:
            from electionforensics.ingest.cec import load_election
            prev_data = load_election(k - 1)
        except FileNotFoundError:
            prev_data = None
    if prev_data is not None:
        pst = prev_data.stations
        pext, preg = pst[pst["is_external"] & (pst["voters"].fillna(0) > 0)], prev_data.regular
        if not pext.empty:
            prev_k = prev_data.knesset
            pse, psr = _shares(pext, prev_data.parties), _shares(preg, prev_data.parties)
            pgap = 100 * (pse - psr)
            tab["gap_prev_pp"] = tab["party"].map(pgap.to_dict())
    tab["gap_shift_pp"] = tab["gap_pp"] - tab["gap_prev_pp"]
    tab = tab.sort_values("votes_ext", ascending=False).reset_index(drop=True)

    # (b) batches against each other
    major = [p for p in feat.major if f"v_{p}" in ext.columns]
    b = ext[ext["voters"] >= min_voters].copy()
    g = np.zeros(len(b), dtype=int)
    v = b["voters"].to_numpy(dtype=float)
    va = b["valid"].to_numpy(dtype=float)
    inv = b["invalid"].to_numpy(dtype=float)
    z_inv, q_inv, _ = _loo_z(inv, v, g)
    units = pd.DataFrame({
        "knesset": k, "locality_code": b["locality_code"].to_numpy(), "locality_name": b["locality_name"].to_numpy(),
        "station": b["station"].to_numpy(), "sector": "external", "voters": v.astype(int), "invalid": inv.astype(int),
        "invalid_share": inv / v, "other_invalid_share": q_inv, "z_invalid": z_inv,
    })
    zcols = ["z_invalid"]
    for p in major:
        zp, qp, _ = _loo_z(b[f"v_{p}"].to_numpy(dtype=float), va, g)
        units[f"share_{p}"] = b[f"v_{p}"].to_numpy(dtype=float) / va
        units[f"other_share_{p}"] = qp
        units[f"z_{p}"] = zp
        zcols.append(f"z_{p}")
    zmat = units[zcols].to_numpy(dtype=float)
    absz = np.abs(np.nan_to_num(zmat, nan=0.0))
    units["max_abs_z"] = absz.max(axis=1)
    arg = absz.argmax(axis=1)
    units["top_dimension"] = [zcols[i][2:] for i in arg]
    units["top_z"] = zmat[np.arange(len(units)), arg]
    units = units.sort_values("max_abs_z", ascending=False).reset_index(drop=True)

    # (c) weight
    voters_ext, voters_all = float(ext["voters"].sum()), float(st["voters"].sum())
    inv_ext = float(ext["invalid"].sum() / voters_ext) if voters_ext else float("nan")
    inv_reg = float(reg["invalid"].sum() / reg["voters"].sum()) if reg["voters"].sum() else float("nan")
    seats = _seat_shift(data, reg)

    shifts = tab["gap_shift_pp"].dropna()
    big_gap = tab.iloc[int(tab["gap_pp"].abs().to_numpy().argmax())]
    shift_party = str(tab["party"].iloc[int(tab["gap_shift_pp"].abs().fillna(-1.0).to_numpy().argmax())]) if len(shifts) else None
    mz = units["max_abs_z"]
    summary: dict[str, Any] = {
        "n_batches": len(units), "voters_external": int(voters_ext), "external_share": voters_ext / voters_all if voters_all else float("nan"),
        "invalid_external": inv_ext, "invalid_regular": inv_reg, "invalid_ratio": inv_ext / inv_reg if inv_reg else float("nan"),
        "previous_knesset": prev_k,
        "largest_gap": {"party": str(big_gap["party"]), "gap_pp": float(big_gap["gap_pp"])},
        "gap_shift_sd_pp": float(shifts.std(ddof=1)) if len(shifts) > 2 else float("nan"),
        "max_abs_gap_shift_pp": float(shifts.abs().max()) if len(shifts) else float("nan"),
        "max_abs_gap_shift_party": shift_party,
        "max_abs_z_quantiles": {q: float(mz.quantile(q)) for q in (0.5, 0.9, 0.99)} if len(mz) else {},
        "n_batches_above_4": int((mz > 4).sum()), "n_batches_above_6": int((mz > 6).sum()),
        "batch_voters_median": float(np.median(v)) if len(v) else float("nan"),
        **seats,
    }
    return TestResult("T15", "Double envelopes", k, "station", units, summary, {"min_voters": min_voters}, extra={"parties": tab})
