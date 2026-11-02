"""Bader–Ofer seat allocation with a traceable margin.

Israeli law (Knesset Elections Law [Consolidated Version] 5729-1969, §§ 81–82) distributes the 120 seats among the
lists that reached the threshold (3.25 % of the valid votes since the 20th Knesset) by the Bader–Ofer method: each
list first receives the integer part of its votes divided by the quota, the remaining seats go one by one to the list
with the highest votes/(seats + 1), and two lists that signed a surplus-vote agreement (הסכם עודפים) are treated as one
list for that step and then split their joint seats by the same rule. The whole procedure is equivalent to the
D'Hondt / Jefferson highest-averages rule applied to agreement pairs and then inside each pair. The implementation is
the one in `battery/t16_representation.py`, which reproduces the official 2022 allocation exactly (test in
`tests/test_battery_more.py`); this module adds the record of the last seat awarded and of the runner-up, the bloc
sums and the vote distance to the next seat change, which the article needs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd
import yaml

from electionforensics.battery.t16_representation import bader_ofer, dhondt, load_agreements
from electionforensics.config import SEATS, TAXONOMY, THRESHOLD_BY_KNESSET

BLOCS_FILE = TAXONOMY / "blocs.yaml"


@dataclass
class Allocation:
    """One Bader–Ofer allocation and what it took to get there."""

    seats: dict[str, int]
    votes: dict[str, int]
    valid: int
    threshold: float
    threshold_votes: float
    eligible: list[str]
    wasted_votes: int
    agreements: list[tuple[str, str]]
    #: the quotient that won the 120th seat and the best losing quotient, both as (unit, quotient, seats_of_unit)
    last_awarded: tuple[str, float, int] | None = None
    runner_up: tuple[str, float, int] | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def margin_votes(self) -> float | None:
        """Votes the runner-up unit lacked for the last seat (its quotient gap × (seats + 1))."""
        if not self.last_awarded or not self.runner_up:
            return None
        _, q_last, _ = self.last_awarded
        _, q_next, s_next = self.runner_up
        return (q_last - q_next) * (s_next + 1)

    def bloc_seats(self, blocs: dict[str, list[str]]) -> dict[str, int]:
        out = {b: sum(self.seats.get(p, 0) for p in members) for b, members in blocs.items()}
        out["other"] = SEATS - sum(out.values())
        return out


def _quotient_trace(votes: dict[str, int], seats: int, agreements: list[tuple[str, str]]) -> tuple[Any, Any]:
    """Replay the pair-level D'Hondt to find the last quotient awarded and the best quotient not awarded."""
    bloc_of: dict[str, str] = {}
    for a, b in agreements:
        if a in votes and b in votes:
            bloc_of[a] = bloc_of[b] = f"{a}+{b}"
    bloc_votes: dict[str, int] = {}
    for p, v in votes.items():
        key = bloc_of.get(p, p)
        bloc_votes[key] = bloc_votes.get(key, 0) + v
    alloc = dict.fromkeys(bloc_votes, 0)
    last: tuple[str, float, int] | None = None
    for _ in range(seats):
        best = max(bloc_votes, key=lambda u: (bloc_votes[u] / (alloc[u] + 1), bloc_votes[u]))
        last = (best, bloc_votes[best] / (alloc[best] + 1), alloc[best])
        alloc[best] += 1
    runner = max(bloc_votes, key=lambda u: (bloc_votes[u] / (alloc[u] + 1), bloc_votes[u]))
    return last, (runner, bloc_votes[runner] / (alloc[runner] + 1), alloc[runner])


def allocate_totals(votes: dict[str, int], threshold: float, agreements: list[tuple[str, str]],
                    seats: int = SEATS) -> Allocation:
    """Allocate `seats` among `votes` (ballot letters → valid votes) with the threshold and the agreement pairs."""
    valid = int(sum(votes.values()))
    thr_votes = threshold * valid
    eligible = {p: int(v) for p, v in votes.items() if v >= thr_votes and v > 0}
    notes: list[str] = []
    if not eligible:
        return Allocation({}, dict(votes), valid, threshold, thr_votes, [], valid, list(agreements), notes=["no list above the threshold"])
    pairs = [(a, b) for a, b in agreements if a in eligible and b in eligible]
    dropped = [(a, b) for a, b in agreements if (a, b) not in pairs]
    if dropped:
        notes.append("agreement pairs with a partner below the threshold are void: " + ", ".join(f"{a}+{b}" for a, b in dropped))
    alloc = bader_ofer(eligible, seats, pairs)
    last, runner = _quotient_trace(eligible, seats, pairs)
    wasted = valid - sum(eligible.values())
    return Allocation({p: int(alloc.get(p, 0)) for p in eligible}, dict(votes), valid, threshold, thr_votes,
                      sorted(eligible, key=lambda p: -eligible[p]), int(wasted), pairs, last, runner, notes)


def totals_from_stations(stations: pd.DataFrame, parties: list[str]) -> dict[str, int]:
    """National totals from a station table (every row, external envelopes included)."""
    cols = [f"v_{p}" for p in parties]
    s = stations[cols].sum()
    return {str(c)[2:]: int(v) for c, v in s.items()}


def load_blocs(knesset: int, bennett_as_change: bool = True) -> dict[str, list[str]]:
    """Bloc membership from `taxonomy/blocs.yaml`: coalition, change, arab (the main run adds `main_run_change_extra`)."""
    doc = yaml.safe_load(BLOCS_FILE.read_text(encoding="utf-8")) or {}
    entry = doc.get(knesset) or {}
    change = list(entry.get("change", []))
    if bennett_as_change:
        change += [p for p in entry.get("main_run_change_extra", []) if p not in change]
    return {"coalition": list(entry.get("coalition", [])), "change": change, "arab": list(entry.get("arab", []))}


def load_frames(knesset: int) -> dict[str, list[str]]:
    """Alternative bloc frames from `taxonomy/blocs.yaml` (`frames:`), name → ballot letters; empty where none is defined."""
    doc = yaml.safe_load(BLOCS_FILE.read_text(encoding="utf-8")) or {}
    entry = doc.get(knesset) or {}
    return {str(name): list(members) for name, members in (entry.get("frames") or {}).items()}


def frame_seats(seats: dict[str, int], frames: dict[str, list[str]]) -> dict[str, int]:
    return {name: sum(seats.get(p, 0) for p in members) for name, members in frames.items()}


def election_allocation(knesset: int, stations: pd.DataFrame, parties: list[str]) -> Allocation:
    """Allocation of an election from its station table with the threshold and agreements of that Knesset."""
    agreements, _verified = load_agreements(knesset)
    return allocate_totals(totals_from_stations(stations, parties), THRESHOLD_BY_KNESSET[knesset], agreements)


__all__ = ["Allocation", "allocate_totals", "bader_ofer", "dhondt", "election_allocation", "frame_seats", "load_agreements",
           "load_blocs", "load_frames", "totals_from_stations"]
