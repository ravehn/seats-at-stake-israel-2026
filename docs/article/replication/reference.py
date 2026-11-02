"""Reference implementation of every mathematical operation in «Seats at Stake» (draft reference article, 2026).

One file, Python 3.12, standard library only. It is written separately from the project package and does not import
it; the package's test suite runs this file on the published tables and asserts that the two implementations agree
on every number of the article (tests/test_reference_implementation.py). Appendix E of the article lists this file;
Appendix F lists the checks.

Inputs (the tables published with the article, under docs/article/tables/):
  factual_allocation.csv        national totals per list in the 2026 count, every row of the Committee's file
  factual_allocation_k25.csv    the same for the 2022 election (validation of the allocation against the official result)
  units_A.csv, units_B.csv, units_B_haredi.csv
                                the covered polling stations of readings A, B and B⁺: locality code, station number,
                                valid votes (the sum of all lists' votes at the station), Likud's votes and the votes
                                of the four change-bloc lists
  itinerary_k26.csv             the prime minister's confirmed campaign appearances (Appendix G) with the locality's
                                2022 Likud share; the threshold of reading I is the lowest share among the ordinary stops
Output: reference_results.json (next to the tables unless --out is given) and a printed summary.

    uv run python -X utf8 docs/article/replication/reference.py            # from the package root
    python -X utf8 reference.py --tables <dir> --out <file>

Operations, in the order of the article (§§ 4.3–4.5):
  (1) threshold          T = 0.0325 · V, V = all valid votes; a list qualifies iff v_p ≥ T
  (2) agreement units    two qualifying lists with a surplus-vote agreement form one unit with the sum of their votes
  (3) highest averages   120 times: the seat goes to the unit with the largest v_u / (s_u + 1); ties to the larger v_u
  (4) inside a pair      the pair's seats are divided between its two lists by the same rule
  (5) margin             (q_last − q_next) · (s_next + 1): the votes the runner-up unit lacked for the 120th seat
  (6) transfer           per covered unit i: r_i = min(round(δ·n_i/100), ℓ_i); r_i = 0 if the unit has no change votes;
                         the r_i votes leave Likud and are split among the change lists by largest remainders
  (7) grid cell          Δ seats of each bloc = seats(allocation of the counterfactual totals) − seats(factual)
  (8) step curve         δ_i = i/100, i = 0 … 1000; breakpoint b_k = the first δ_i at which k coalition seats have moved
  (9) integration over δ δ ~ log-normal fixed by its 5th and 95th percentiles — (0.4, 5.0) for the headline numbers,
                         (0.5, 4.5) for the premise's own reading, (0.15, 1.7) for minimal effects; exact probabilities
                         from the curve under each
  (10) reading I         τ = the lowest 2022 Likud share among the localities of the ordinary campaign stops (Appendix G)
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path

SEATS = 120
THRESHOLD = 0.0325
LIKUD = "מחל"
#: the four change-bloc lists in the order used for the proportional return (ties in the largest remainder go to the first)
CHANGE = ["דרך", "אמת", "ל", "רק"]
COALITION = ["מחל", "ט", "ב", "שס", "ג"]
ARAB = ["ודם", "עם"]
#: surplus-vote agreements (הסכמי עודפים) as pairs of ballot letters, 2026 from the press of 11–14 October 2026, 2022 as gazetted
AGREEMENTS = {26: [("רק", "ל"), ("דרך", "אמת"), ("ודם", "עם"), ("מחל", "ט"), ("שס", "ג")],
              25: [("פה", "כן"), ("אמת", "מרצ"), ("ג", "שס"), ("מחל", "ט")]}
OFFICIAL_25 = {"מחל": 32, "פה": 24, "ט": 14, "כן": 12, "שס": 11, "ג": 7, "ל": 6, "עם": 5, "ום": 5, "אמת": 4}
DELTAS = (0.4, 1.0, 1.2, 2.0, 3.4, 5.0)
VICTIMS = ("דרך", "אמת", "רק")
READINGS = {"A": "units_A.csv", "B": "units_B.csv", "B_haredi": "units_B_haredi.csv"}
CURVE_STEP, CURVE_MAX = 0.01, 10.0
Z95 = 1.6448536269514722
#: the distributions of δ of § 4.5 (Table 3b), each log-normal with these 5th and 95th percentiles in pp
PRIORS = {"literature": (0.4, 5.0), "premise": (0.5, 4.5), "minimal": (0.15, 1.7)}
MAIN_PRIOR = "literature"


def prior_params(low: float, high: float) -> tuple[float, float]:
    """median = √(low · high); σ = ln(high / low) / (2 · 1.645), so that the 5th and 95th percentiles are low and high."""
    return math.sqrt(low * high), math.log(high / low) / (2 * Z95)


MC_LOW, MC_HIGH = PRIORS[MAIN_PRIOR]
MC_MEDIAN, MC_SIGMA = prior_params(MC_LOW, MC_HIGH)           # 1.414…, 0.768…

Votes = dict[str, int]


# ---------------------------------------------------------------- (1)–(5) the allocation

def highest_averages(votes: Votes, seats: int) -> Votes:
    """(3) Jefferson–D'Hondt: `seats` times, the seat goes to the unit with the largest votes/(seats + 1); ties to the larger unit."""
    alloc = dict.fromkeys(votes, 0)
    for _ in range(seats):
        best = max(votes, key=lambda u: (votes[u] / (alloc[u] + 1), votes[u]))
        alloc[best] += 1
    return alloc


def pair_units(votes: Votes, pairs: list[tuple[str, str]]) -> tuple[dict[str, str], Votes]:
    """(2) Map each list to its unit (itself, or «a+b» for an agreement pair) and sum the unit votes."""
    unit_of = {p: p for p in votes}
    for a, b in pairs:
        if a in votes and b in votes:
            unit_of[a] = unit_of[b] = f"{a}+{b}"
    unit_votes: Votes = {}
    for p, v in votes.items():
        unit_votes[unit_of[p]] = unit_votes.get(unit_of[p], 0) + v
    return unit_of, unit_votes


def bader_ofer(votes: Votes, seats: int, pairs: list[tuple[str, str]]) -> Votes:
    """(2)–(4) Highest averages over the agreement units, then inside each pair."""
    unit_of, unit_votes = pair_units(votes, pairs)
    by_unit = highest_averages(unit_votes, seats)
    out: Votes = {}
    for unit, s in by_unit.items():
        members = [p for p in votes if unit_of[p] == unit]
        if len(members) == 1:
            out[members[0]] = s
        else:
            out.update(highest_averages({p: votes[p] for p in members}, s))
    return out


def last_and_runner_up(votes: Votes, seats: int, pairs: list[tuple[str, str]]) -> tuple[tuple[str, float, int], tuple[str, float, int]]:
    """(5) Replay the unit-level rule: the quotient that won the last seat and the best quotient that won none."""
    _, unit_votes = pair_units(votes, pairs)
    alloc = dict.fromkeys(unit_votes, 0)
    last = ("", 0.0, 0)
    for _ in range(seats):
        best = max(unit_votes, key=lambda u: (unit_votes[u] / (alloc[u] + 1), unit_votes[u]))
        last = (best, unit_votes[best] / (alloc[best] + 1), alloc[best])
        alloc[best] += 1
    runner = max(unit_votes, key=lambda u: (unit_votes[u] / (alloc[u] + 1), unit_votes[u]))
    return last, (runner, unit_votes[runner] / (alloc[runner] + 1), alloc[runner])


def allocate(votes: Votes, agreements: list[tuple[str, str]], threshold: float = THRESHOLD, seats: int = SEATS) -> dict:
    """(1)–(5) The allocation of `votes` (ballot letters → votes, every list) with the threshold and the agreements."""
    votes = dict(sorted(votes.items()))                      # a fixed order of the lists, for reproducible tie-breaking
    valid = sum(votes.values())
    thr_votes = threshold * valid
    eligible = {p: v for p, v in votes.items() if v >= thr_votes and v > 0}
    pairs = [(a, b) for a, b in agreements if a in eligible and b in eligible]
    seats_by_list = bader_ofer(eligible, seats, pairs)
    last, runner = last_and_runner_up(eligible, seats, pairs)
    margin = (last[1] - runner[1]) * (runner[2] + 1)
    return {"seats": seats_by_list, "eligible": sorted(eligible, key=lambda p: -eligible[p]), "valid": valid,
            "threshold_votes": thr_votes, "wasted_votes": valid - sum(eligible.values()), "pairs": pairs,
            "last_awarded": list(last), "runner_up": list(runner), "margin_votes": margin}


def bloc_seats(seats: Votes) -> dict[str, int]:
    out = {"coalition": sum(seats.get(p, 0) for p in COALITION), "change": sum(seats.get(p, 0) for p in CHANGE),
           "arab": sum(seats.get(p, 0) for p in ARAB)}
    out["other"] = SEATS - sum(out.values())
    return out


# ---------------------------------------------------------------- (6) the transfer

def largest_remainder(total: int, weights: list[int]) -> list[int]:
    """Split the integer `total` across the weights proportionally, integer-exact: floors first, then one more each to the
    largest remainders (ties to the earlier position)."""
    row_sum = sum(weights)
    if row_sum == 0:
        return [0] * len(weights)
    quota = [(w / row_sum) * total for w in weights]
    base = [math.floor(q) for q in quota]
    rem = [q - b for q, b in zip(quota, base, strict=True)]
    need = total - sum(base)
    for j in sorted(range(len(weights)), key=lambda j: (-rem[j], j))[:need]:
        base[j] += 1
    return base


def transfer(units: list[dict], delta_pp: float, recipients: str) -> dict:
    """(6) For each unit (valid votes n, Likud votes ℓ, change-list votes c_1..c_4) remove r = min(round(δ·n/100), ℓ) votes
    from Likud. `recipients`: "proportional" — r is split among the change lists by largest remainders and units without
    any change votes are skipped; a ballot letter — all r go to that list; "none" — the votes vanish (turnout channel)."""
    moved = capped = n_without = 0
    gains = defaultdict(int)
    for u in units:
        asked = round(delta_pp / 100.0 * u["valid"])
        removed = min(asked, u["likud"])
        capped += asked - removed
        if recipients == "proportional":
            if sum(u["change"]) == 0:
                n_without += removed > 0
                continue
            for p, g in zip(CHANGE, largest_remainder(removed, u["change"]), strict=True):
                gains[p] += g
        elif recipients != "none":
            gains[recipients] += removed
        moved += removed
    return {"moved": moved, "capped": capped, "gains": dict(gains), "n_units": len(units), "n_units_without_recipients": n_without}


def counterfactual_totals(base: Votes, t: dict) -> Votes:
    totals = dict(base)
    totals[LIKUD] -= t["moved"]
    for p, g in t["gains"].items():
        totals[p] = totals.get(p, 0) + g
    return totals


def by_locality(stations: list[dict]) -> list[dict]:
    """Locality units: the covered stations of a locality summed (in a mixed city, only its covered stations)."""
    acc: dict[int, dict] = {}
    for s in stations:
        u = acc.setdefault(s["locality_code"], {"valid": 0, "likud": 0, "change": [0] * len(CHANGE)})
        u["valid"] += s["valid"]
        u["likud"] += s["likud"]
        u["change"] = [a + b for a, b in zip(u["change"], s["change"], strict=True)]
    return [acc[k] for k in sorted(acc)]


# ---------------------------------------------------------------- (7)–(9) grid, curve, integration

def variants() -> list[tuple[str, str, str]]:
    """(key, unit, recipients) as in the article: main, station units, three single-recipient variants, turnout channel."""
    return [("main", "locality", "proportional"), ("station", "station", "proportional"),
            *[(f"victim_{v}", "locality", v) for v in VICTIMS], ("turnout", "locality", "none")]


def normal_cdf(z: float) -> float:
    return 0.5 * math.erfc(-z / math.sqrt(2))


def integrate_curve(lost: list[int], median: float = MC_MEDIAN, sigma: float = MC_SIGMA, step: float = CURVE_STEP) -> dict:
    """(9) Exact integration of the step curve over the log-normal: a draw in [δ_i, δ_{i+1}) takes the curve's value at δ_i,
    a draw beyond the last point takes the last value. P(lost = k) = Σ_i 1[lost_i = k] · (Φ(z_{i+1}) − Φ(z_i)),
    z_i = (ln δ_i − ln median) / σ, z_0 = −∞, z_{n} = +∞."""
    mu = math.log(median)
    n = len(lost)
    edges = [-math.inf] + [(math.log(i * step) - mu) / sigma for i in range(1, n)] + [math.inf]
    pmf: dict[int, float] = defaultdict(float)
    for i in range(n):
        pmf[lost[i]] += normal_cdf(edges[i + 1]) - normal_cdf(edges[i])
    ks = sorted(pmf)
    return {"pmf_seats_lost": {str(k): pmf[k] for k in ks},
            "p_ge": {str(k): sum(v for kk, v in pmf.items() if kk >= k) for k in range(1, 7)},
            "mean_seats_lost": sum(k * v for k, v in pmf.items()),
            "share_above_curve_max": 1 - normal_cdf(edges[n - 1])}


# ---------------------------------------------------------------- input and output

def read_totals(path: Path) -> Votes:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return {row["list"]: int(row["votes"]) for row in csv.DictReader(fh)}


def read_units(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return [{"locality_code": int(row["locality_code"]), "station": row["station"], "valid": int(row["valid"]),
                 "likud": int(row[f"v_{LIKUD}"]), "change": [int(row[f"v_{p}"]) for p in CHANGE]} for row in csv.DictReader(fh)]


def read_itinerary(path: Path) -> list[dict]:
    """The itinerary table (Appendix G): one row per confirmed appearance, with the shares the package joined."""
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return [{"appearance": row["appearance"], "locality_code": int(row["locality_code"]) if row["locality_code"] else None,
                 "in_rule": row["in_rule"].strip().lower() in ("true", "yes"),
                 "share_prev": float(row["likud_share_prev"]) if row["likud_share_prev"] else None}
                for row in csv.DictReader(fh)]


def itinerary_threshold(rows: list[dict]) -> dict:
    """τ of reading I: the lowest 2022 Likud share among the localities of the ordinary stops (one value per locality),
    rounded down to a tenth of a percentage point, so that the threshold printed is the threshold applied."""
    by_code: dict[int, float] = {}
    for r in rows:
        if r["in_rule"] and r["locality_code"] is not None and r["share_prev"] is not None:
            by_code.setdefault(r["locality_code"], r["share_prev"])
    shares = sorted(by_code.values())
    n = len(shares)
    median = shares[n // 2] if n % 2 else (shares[n // 2 - 1] + shares[n // 2]) / 2
    return {"tau": math.floor(1000 * shares[0] + 1e-9) / 1000, "min_share": shares[0], "max": shares[-1], "median": median,
            "n_localities": n, "n_ordinary_appearances": sum(r["in_rule"] for r in rows), "n_appearances": len(rows)}


def compute(tables: Path) -> dict:
    base = read_totals(tables / "factual_allocation.csv")
    factual = allocate(base, AGREEMENTS[26])
    f_blocs = bloc_seats(factual["seats"])
    out: dict = {"factual": factual | {"blocs": f_blocs},
                 "no_agreements_blocs": bloc_seats(allocate(base, [])["seats"]),
                 "grid": [], "curves": {}, "breakpoints": {}, "integration": {}}
    k25 = tables / "factual_allocation_k25.csv"
    if k25.exists():
        got = {p: s for p, s in allocate(read_totals(k25), AGREEMENTS[25])["seats"].items() if s > 0}
        out["k25"] = {"computed": got, "official": OFFICIAL_25, "match": got == OFFICIAL_25}
    itinerary = tables / "itinerary_k26.csv"
    if itinerary.exists():
        out["itinerary"] = itinerary_threshold(read_itinerary(itinerary))
    for key, name in READINGS.items():
        path = tables / name
        if not path.exists():
            continue
        stations = read_units(path)
        localities = by_locality(stations)
        for vkey, unit, recipients in variants():
            units = stations if unit == "station" else localities
            for delta in DELTAS:
                t = transfer(units, delta, recipients)
                a = allocate(counterfactual_totals(base, t), AGREEMENTS[26])
                b = bloc_seats(a["seats"])
                out["grid"].append({"coverage": key, "delta": delta, "variant": vkey, "seats": a["seats"], "blocs": b,
                                    "d_blocs": {k: b[k] - f_blocs[k] for k in b}, "margin_votes": a["margin_votes"],
                                    "eligible_changed": sorted(a["eligible"]) != sorted(factual["eligible"]), **t})
        lost: list[int] = []
        for i in range(round(CURVE_MAX / CURVE_STEP) + 1):
            t = transfer(localities, round(i * CURVE_STEP, 4), "proportional")
            lost.append(f_blocs["coalition"] - bloc_seats(allocate(counterfactual_totals(base, t), AGREEMENTS[26])["seats"])["coalition"])
        out["curves"][key] = lost
        out["breakpoints"][key] = {str(k): next((round(i * CURVE_STEP, 4) for i, v in enumerate(lost) if v >= k), None)
                                   for k in range(1, 7)}
        out["integration"][key] = integrate_curve(lost)
        for pk, (lo, hi) in PRIORS.items():
            out.setdefault("integration_priors", {}).setdefault(pk, {})[key] = integrate_curve(lost, *prior_params(lo, hi))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tables", type=Path, default=Path(__file__).resolve().parents[1] / "tables")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    res = compute(args.tables)
    out = args.out or args.tables / "reference_results.json"
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    f = res["factual"]
    print("factual:", " ".join(f"{p} {s}" for p, s in sorted(f["seats"].items(), key=lambda x: -x[1])), "| blocs", f["blocs"])
    print(f"last seat: {f['last_awarded'][0]} at {f['last_awarded'][1]:.1f}; runner-up {f['runner_up'][0]} at "
          f"{f['runner_up'][1]:.1f}; margin {f['margin_votes']:.0f} votes")
    if "k25" in res:
        print("2022 allocation reproduces the official result:", res["k25"]["match"])
    if "itinerary" in res:
        i = res["itinerary"]
        print(f"itinerary: {i['n_localities']} localities of ordinary stops ({i['n_ordinary_appearances']} of "
              f"{i['n_appearances']} appearances); τ = {100 * i['tau']:.1f} %, median {100 * i['median']:.1f} %, max {100 * i['max']:.1f} %")
    for key in res["curves"]:
        cells = [-r["d_blocs"]["coalition"] for r in res["grid"] if r["coverage"] == key and r["variant"] == "main"]
        it = res["integration"][key]
        print(f"{key:9s} coalition seats moved at δ = {DELTAS}: {cells}; breakpoints {res['breakpoints'][key]}; "
              f"P(≥1) {it['p_ge']['1']:.3f} P(≥2) {it['p_ge']['2']:.3f} mean {it['mean_seats_lost']:.3f}")
    print("written:", out)


if __name__ == "__main__":
    main()
