"""Command-line interface: `ef`."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pandas as pd
import typer

from electionforensics import __version__
from electionforensics.config import DATA_INTERIM, DATA_PROCESSED, ensure_dirs
from electionforensics.ingest.registry import ELECTIONS, snapshot_files

app = typer.Typer(no_args_is_help=True, add_completion=False, help="ElectionForensics — analysis of election distortions from public data.")


@app.callback()
def _setup() -> None:
    # Windows consoles default to cp1252; every table here carries Hebrew.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


@app.command()
def version() -> None:
    """Print the version."""
    typer.echo(__version__)


@app.command()
def ingest(knesset: list[int] = typer.Option(None, "--knesset", "-k", help="Knesset numbers (default: all)")) -> None:
    """Load the raw CEC files, harmonize, write parquet to data/interim and print integrity facts."""
    from electionforensics.ingest.cec import load_election

    ensure_dirs()
    ks = knesset or sorted(ELECTIONS)
    for k in ks:
        t0 = time.time()
        e = load_election(k)
        out = DATA_INTERIM / f"stations_k{k}.parquet"
        e.stations.to_parquet(out, index=False)
        meta = {"knesset": k, "source": str(e.source), "sha256": e.sha256, "encoding": e.encoding,
                "snapshot_ts": e.snapshot_ts, "rows": len(e.stations), "regular": len(e.regular),
                "parties": e.parties, "warnings": e.warnings}
        (DATA_INTERIM / f"stations_k{k}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        typer.echo(f"K{k}: {len(e.stations)} rows ({len(e.regular)} regular), {len(e.parties)} lists, "
                   f"{e.encoding}, sha256={e.sha256[:12]}{' snapshot ' + e.snapshot_ts if e.snapshot_ts else ''} "
                   f"[{time.time() - t0:.1f}s] -> {out.name}")
        for w in e.warnings:
            typer.echo(f"      note: {w}")


@app.command()
def battery(
    knesset: list[int] = typer.Option(None, "--knesset", "-k", help="Knesset numbers (default: all)"),
    tests: str = typer.Option("", "--tests", "-t", help="Comma-separated test ids, e.g. T01,T05 (default: all registered)"),
    snapshot: Path | None = typer.Option(None, "--snapshot", help="Explicit 2026 snapshot file (default: latest)"),
    n_draws: int = typer.Option(2000, help="Monte-Carlo draws for T05 and T06"),
) -> None:
    """Run the test battery and write per-test results to data/processed/knesset<N>/."""
    from electionforensics.battery import run_battery
    from electionforensics.ingest.cbs import load_cbs
    from electionforensics.ingest.cec import load_election
    from electionforensics.model.features import build_features
    from electionforensics.provenance import analysis_context, source_record

    ensure_dirs()
    cbs = load_cbs()
    ids = [t.strip() for t in tests.split(",") if t.strip()] or None
    context = analysis_context()
    for k in knesset or sorted(ELECTIONS):
        t0 = time.time()
        data = load_election(k, snapshot if k == 26 else None)
        feat = build_features(data, cbs)
        results = run_battery(feat, ids, T05={"n_draws": n_draws}, T06={"n_draws": n_draws},
                              T15={"data": data}, T16={"data": data}, T14={"as_of": data.snapshot_ts})
        for r in results.values():
            r.summary["_source"] = source_record(data, context)
            r.save()
        typer.echo(f"K{k} ({data.source.name}): " + "; ".join(f"{tid} {len(r.units)} units" for tid, r in results.items())
                   + f" [{time.time() - t0:.1f}s]")


@app.command()
def calibrate(knesset: int = typer.Option(26, "--knesset", "-k")) -> None:
    """Place the election's scores inside the 2019–2022 baseline and write tiers to data/processed."""
    from electionforensics.calibrate.baseline import calibrate as _cal
    from electionforensics.calibrate.baseline import save

    cal = _cal(knesset)
    p = save(cal)
    typer.echo(f"K{knesset}: tiers {cal.stations['tier'].value_counts().to_dict()} -> {p}")
    for name, v in cal.election_level.items():
        typer.echo(f"  {name}: z={v['z']:.2f} baseline {v['baseline_min']:.2f}..{v['baseline_max']:.2f} (baseline_z={v['baseline_z']:.1f})")


@app.command()
def power(
    base: int = typer.Option(25, help="Baseline election to inject into"),
    kinds: str = typer.Option("stuffing,transfer,slip_theft,rounding,protocol_swap,spoiling", help="Comma-separated injection types"),
    fraction: float = typer.Option(0.01, help="Share of stations altered"),
    intensities: str = typer.Option("0.3,0.6,1.0", help="Comma-separated intensity parameters"),
    target: str | None = typer.Option(None, help="Ballot letters of the list that gains / loses (default: leader)"),
    seeds: int = typer.Option(1, help="Repetitions with different seeds"),
) -> None:
    """Measure detection power and induced false alarms of the station-level tests on synthetic distortions."""
    from electionforensics.calibrate.power import bench, save
    from electionforensics.ingest.cbs import load_cbs
    from electionforensics.ingest.cec import load_election
    from electionforensics.model.features import build_features

    cbs = load_cbs()
    feat = build_features(load_election(base), cbs)
    prev = build_features(load_election(base - 1), cbs) if (base - 1) in ELECTIONS else None
    results = []
    for kind in [k.strip() for k in kinds.split(",") if k.strip()]:
        for intensity in [float(x) for x in intensities.split(",") if x.strip()]:
            for seed in range(seeds):
                t0 = time.time()
                r = bench(feat, prev, kind, fraction, intensity, target, seed=seed)
                results.append(r)
                cells = []
                for tid, v in r["tests"].items():
                    if tid == "T05":
                        cells.append(f"T05:{v['shift']:+.1f}")
                    else:
                        cells.append(f"{tid}:{v['p0.999']['power']:.2f}/{v['p0.99']['power']:.2f}")
                typer.echo(f"{kind:<14} i={intensity:<4} seed={seed} n={r['n_altered']:>4} | {' '.join(cells)} [{time.time() - t0:.0f}s]")
    typer.echo("cells: power at the flag budget (99.9th pctl) / at the watch budget (99th pctl); T05: shift of z")
    typer.echo(f"saved -> {save(results)}")


@app.command("city-effect")
def city_effect_cmd(
    locality: int = typer.Option(..., "--locality", "-l", help="CBS locality code, e.g. 240 for Yokneam Illit"),
    pairs: str = typer.Option("25:26", help="Comma-separated prev:curr election pairs"),
    k: int = typer.Option(12, help="Number of matched donor cities"),
    bennett_as_change: bool = typer.Option(True, help="Count Beyachad (רק) in the change bloc for 2026"),
    snapshot: Path | None = typer.Option(None, help="Explicit current-election source snapshot"),
    party: list[str] = typer.Option([], "--party", help="Ballot letters of single lists to add as outcomes, e.g. מחל"),
) -> None:
    """Deviation of a city's change between two elections from matched twin cities, with placebo bands."""
    from electionforensics.link.city_effect import city_effect, save

    for pair in [p for p in pairs.split(",") if p.strip()]:
        prev, curr = (int(x) for x in pair.split(":"))
        ce = city_effect(locality, prev, curr, k=k, bennett_as_change=bennett_as_change, snapshot=snapshot, extra_parties=party)
        typer.echo(f"{ce.locality_name} {prev}->{curr}: donors {ce.params['donor_pool']}, completeness {ce.params['target_completeness']:.2f}")
        for n, r in ce.outcomes.items():
            typer.echo(f"  {n:<12} city {r['target_delta'] * 100:+.1f} pp, twins {r['counterfactual_delta'] * 100:+.1f} pp, "
                       f"gap {r['effect'] * 100:+.1f} pp, placebo 95% [{r['band_lo'] * 100:+.1f}, {r['band_hi'] * 100:+.1f}], p={r['p_placebo']:.2f}")
        typer.echo("  twins: " + ", ".join(f"{r.locality_name_p} ({r.weight:.2f})" for _, r in ce.donors.iterrows()))
        typer.echo(f"  descriptive comparison, not a causal estimate; saved -> {save(ce, DATA_PROCESSED / 'city' / f'{locality}_{prev}_{curr}')}")


@app.command("exposure-compare")
def exposure_compare_cmd(
    register: Path = typer.Option(Path("incidents") / "exposure" / "signs_k26.csv", help="Exposure register CSV (one row per documented incident)"),
    tiers: str = typer.Option("A", help="Comma-separated register tiers to pool, e.g. A,B"),
    pairs: str = typer.Option("25:26", help="Comma-separated prev:curr election pairs"),
    k: int = typer.Option(12, help="Number of matched donor cities per registered city"),
    party: list[str] = typer.Option(["מחל"], "--party", help="Ballot letters of single lists to add as outcomes"),
    n_perm: int = typer.Option(4000, help="Permutation draws for the pooled reference"),
    snapshot: Path | None = typer.Option(None, help="Explicit current-election source snapshot"),
) -> None:
    """Registered cities (documented sign clearing and related incidents) against matched twins, pooled per tier."""
    from electionforensics.link.exposure import exposure_comparison, load_register, save

    reg = load_register(register)
    tier_list = [t.strip() for t in tiers.split(",") if t.strip()]
    for pair in [p for p in pairs.split(",") if p.strip()]:
        prev, curr = (int(x) for x in pair.split(":"))
        res = exposure_comparison(reg, prev, curr, tiers=tier_list, k=k, extra_parties=party, n_perm=n_perm, snapshot=snapshot)
        for _, r in res.pooled.iterrows():
            typer.echo(f"tier {r.tier} {r.outcome:<12} n={r.n_cities:>2} mean gap {r.mean_gap * 100:+.2f} pp (median {r.median_gap * 100:+.2f}), "
                       f"permutation 95% [{r.perm_lo * 100:+.2f}, {r.perm_hi * 100:+.2f}], p={r.p_perm:.2f}, reference {r.n_reference}")
        for _, r in res.cities.iterrows():
            typer.echo(f"  {r.tier} {r.locality_name:<16} {r.outcome:<12} gap {r.gap * 100:+.1f} pp, placebo [{r.band_lo * 100:+.1f}, {r.band_hi * 100:+.1f}], "
                       f"p={r.p_placebo:.2f}, registered twins {r.twins_registered}/{r.n_twins}")
        for _, r in res.skipped.iterrows():
            typer.echo(f"  skipped {r.tier} {r.locality_code}: {r.reason}")
        out = save(res, DATA_PROCESSED / "exposure" / f"{register.stem}_{prev}_{curr}", register)
        typer.echo(f"  descriptive comparison, controls = no documented incident; saved -> {out}")


design_app = typer.Typer(help="Design of the signage-effect measurement: power simulated on the real building panel.")
app.add_typer(design_app, name="design")


def _floats(text: str) -> list[float]:
    return [float(x) for x in text.split(",") if x.strip()]


def _ints(text: str) -> list[int]:
    return [int(x) for x in text.split(",") if x.strip()]


@design_app.command("power")
def design_power(
    ns: str = typer.Option("24,48,96,144,200,300,400", help="Comma-separated sample sizes (buildings, even)"),
    deltas: str = typer.Option("0,0.5,1,1.5,2,3", help="Comma-separated effects of one arm, pp of valid votes"),
    compliances: str = typer.Option("1,0.8,0.6", help="Comma-separated compliance rates"),
    designs: str = typer.Option("two_arm,one_arm", help="two_arm (symmetric arms) and/or one_arm (against control)"),
    strata: str = typer.Option("locality_pairs", help="pairs | bins | locality | locality_pairs"),
    heterogeneity: str = typer.Option("none", help="none | lognormal | gradient (dose varies across buildings)"),
    mechanism: str = typer.Option("switch", help="switch (votes change bloc) | abstain (losing side's voters stay home)"),
    outcome: str = typer.Option("likud", help="likud | coalition | change"),
    per_locality: int = typer.Option(4, help="Buildings per sampled locality at most (locality schemes)"),
    inference: str = typer.Option("t", help="t (robust t-test) | randomization (re-randomisation p-value)"),
    reps: int = typer.Option(1000, help="Replicates per scenario"),
    seed: int = typer.Option(20261101, help="Base seed; every scenario derives its own from it"),
    snapshot: Path | None = typer.Option(None, help="Pinned 2026 snapshot (default: latest)"),
    sectors: str = typer.Option("jewish", help="Comma-separated locality sectors of the panel"),
    variants: bool = typer.Option(False, help="Also run the analysis variants (strata, heterogeneity, mechanism, covariates, inference)"),
    out: Path = typer.Option(Path("docs") / "design" / "figures", help="Output directory"),
    prefix: str = typer.Option("power-2026-11", help="File name prefix"),
    png: Path | None = typer.Option(None, help="Also write PNG copies of the figures here (outside the repository)"),
) -> None:
    """Simulate the power of the proposed field experiments on the real 2022→2026 building panel; write tables and figures."""
    from dataclasses import replace

    from electionforensics.design.panel import load_panel, noise_summary
    from electionforensics.design.report import write_results
    from electionforensics.design.simulate import Scenario, grid, simulate_grid

    panel = load_panel(25, 26, snapshot, tuple(sectors.split(",")))
    noise = noise_summary(panel)
    typer.echo(f"panel: {panel.n_buildings} buildings, {len(panel.stations)} stations, {panel.source['n_localities']} localities; "
               f"2026 {panel.source['curr']['file']}; within-locality SD of the Likud swing "
               f"{float(noise.set_index('outcome')['sd_within_locality']['likud']):.2f} pp")
    base = Scenario(strata=strata, heterogeneity=heterogeneity, mechanism=mechanism, outcome=outcome, per_locality=per_locality,
                    inference=inference)
    scenarios = grid(designs.split(","), _ints(ns), _floats(deltas), _floats(compliances), base)
    t0 = time.time()

    def progress(label: str, row: dict[str, object]) -> None:
        typer.echo(f"  {label:<64} power ITT {row['power_itt']:.3f}  SE {row['mean_se_itt']:.3f}  [{time.time() - t0:.0f}s]")

    results = simulate_grid(panel, scenarios, reps, seed, progress=progress)
    var_df = None
    if variants:
        var_scen = []
        for d in designs.split(","):
            n0 = 96 if d == "two_arm" else 400
            for st in ("pairs", "bins", "locality", "locality_pairs"):
                var_scen.append(replace(base, design=d, n=n0, delta=1.0, strata=st))
            for h in ("lognormal", "gradient"):
                var_scen.append(replace(base, design=d, n=n0, delta=1.0, heterogeneity=h))
            var_scen.append(replace(base, design=d, n=n0, delta=1.0, mechanism="abstain"))
            var_scen.append(replace(base, design=d, n=n0, delta=1.0, covariates=False))
            var_scen.append(replace(base, design=d, n=n0, delta=1.0, min_distance=500.0))
            var_scen.append(replace(base, design=d, n=n0, delta=1.0, inference="randomization"))
            var_scen.append(replace(base, design=d, n=n0, delta=0.0, inference="randomization"))
            var_scen.append(replace(base, design=d, n=24, delta=0.0))
            var_scen.append(replace(base, design=d, n=24, delta=0.0, inference="randomization"))
        var_df = simulate_grid(panel, var_scen, reps, seed, progress=progress)
    params = {"ns": _ints(ns), "deltas": _floats(deltas), "compliances": _floats(compliances), "designs": designs.split(","),
              "base": base.__dict__, "reps": reps, "seed": seed, "seconds": round(time.time() - t0)}
    for path in write_results(results, panel, out, prefix, params, noise, var_df, png):
        typer.echo(f"wrote {path}")


@design_app.command("retro")
def design_retro(
    ms: str = typer.Option("6,12,24", help="Comma-separated numbers of treated cities"),
    deltas: str = typer.Option("0,1,2,3", help="Comma-separated city-wide effects, pp of the Likud share"),
    reps: int = typer.Option(20, help="Replicates per (m, δ)"),
    n_perm: int = typer.Option(1000, help="Permutation draws of the pooled reference"),
    seed: int = typer.Option(20261101),
    snapshot: Path | None = typer.Option(None, help="Pinned 2026 snapshot (default: latest)"),
    out: Path = typer.Option(Path("docs") / "design" / "figures"),
    prefix: str = typer.Option("power-2026-11"),
    png: Path | None = typer.Option(None, help="Also write PNG copies here"),
) -> None:
    """Sensitivity of the retrospective twin-cities design (link.exposure) to an injected city-wide effect."""
    from electionforensics.design.panel import load_panel
    from electionforensics.design.report import data_stamp, fig_retro
    from electionforensics.design.retro import retro_power, summarise_retro
    from electionforensics.ingest.cbs import load_cbs
    from electionforensics.ingest.cec import load_election
    from electionforensics.model.features import build_features

    cbs = load_cbs()
    feats = {25: build_features(load_election(25), cbs), 26: build_features(load_election(26, snapshot), cbs)}
    t0 = time.time()
    df = retro_power(feats, _ints(ms), _floats(deltas), reps, n_perm, seed)
    summary = summarise_retro(df)
    out.mkdir(parents=True, exist_ok=True)
    df.to_csv(out / f"{prefix}_retro_runs.csv", index=False, encoding="utf-8", lineterminator="\n")
    summary.to_csv(out / f"{prefix}_retro.csv", index=False, encoding="utf-8", lineterminator="\n")
    for r in summary[summary["outcome"] == "s_מחל"].itertuples():
        typer.echo(f"m={r.m:>3} δ={r.delta:<4} power {r.power:.2f}  mean gap {r.mean_gap_pp:+.2f} pp  band [{r.band_lo_pp:+.2f}, {r.band_hi_pp:+.2f}]")
    panel = load_panel(25, 26, snapshot, with_sites=False)
    path = fig_retro(summary, out / f"{prefix}_fig6_retro.svg", data_stamp(panel.source), png_dir=png)
    typer.echo(f"wrote {path} [{time.time() - t0:.0f}s]")


@design_app.command("figures")
def design_figures(
    out: Path = typer.Option(Path("docs") / "design" / "figures"),
    prefix: str = typer.Option("power-2026-11"),
    snapshot: Path | None = typer.Option(None, help="Pinned 2026 snapshot (default: latest)"),
    png: Path | None = typer.Option(None, help="Also write PNG copies here"),
) -> None:
    """Re-render the tables and figures from saved simulation results without simulating again."""
    from electionforensics.design.panel import load_panel, noise_summary
    from electionforensics.design.report import data_stamp, fig_retro, write_results

    panel = load_panel(25, 26, snapshot)
    results = pd.read_csv(out / f"{prefix}_grid.csv")
    vpath = out / f"{prefix}_variants.csv"
    var_df = pd.read_csv(vpath) if vpath.exists() else None
    params = json.loads((out / f"{prefix}_params.json").read_text(encoding="utf-8"))
    params.pop("source", None)
    params.pop("version", None)
    for path in write_results(results, panel, out, prefix, params, noise_summary(panel), var_df, png):
        typer.echo(f"wrote {path}")
    rpath = out / f"{prefix}_retro.csv"
    if rpath.exists():
        typer.echo(f"wrote {fig_retro(pd.read_csv(rpath), out / f'{prefix}_fig6_retro.svg', data_stamp(panel.source), png_dir=png)}")


@app.command()
def figpack(knesset: int = typer.Option(26, "--knesset", "-k")) -> None:
    """Render the social-media figure pack (PNG, 1:1 and 4:5, Russian and Hebrew) from processed results."""
    from electionforensics.present.figpack import build

    paths = build(knesset)
    typer.echo(f"{len(paths)} figures -> {paths[0].parent}")


@app.command()
def report(knesset: int = typer.Option(26, "--knesset", "-k")) -> None:
    """Build the HTML report from processed results (run `battery` and `calibrate` first)."""
    from electionforensics.present.report import build

    out = build(knesset)
    typer.echo(f"report -> {out}")


@app.command("locality-report")
def locality_report(
    locality: int = typer.Argument(..., help="CEC locality code, e.g. 240 for Yokneam Illit"),
    knesset: int = typer.Option(26, "--knesset", "-k"),
) -> None:
    """Build the standalone HTML report of one locality from the pinned snapshot (battery and calibration first)."""
    from electionforensics.present.locality import build

    out = build(locality, knesset)
    typer.echo(f"locality report -> {out}")


@app.command("build")
def build_issue(
    snapshot: Path | None = typer.Option(None, help="Pinned K26 snapshot (default: latest usable archived file)"),
    baseline: bool = typer.Option(False, help="Recompute all ten historical elections as well"),
    figures: bool = typer.Option(True, help="Include the bilingual social-media figure pack"),
    bundle: bool = typer.Option(True, help="Include reproducibility code and public statistical inputs"),
) -> None:
    """Rebuild pinned statistics, calibration, reader issue, appendix and figures in one command."""
    from electionforensics.ingest.snapshots import load_snapshot
    from electionforensics.present.figpack import build as build_figures
    from electionforensics.present.publication import build as build_publication
    from electionforensics.present.publication import seal_issue

    if snapshot is None:
        for candidate in reversed(snapshot_files("expb")):
            parsed, _ = load_snapshot(candidate)
            if parsed is not None:
                snapshot = candidate
                break
    if snapshot is None:
        raise typer.BadParameter("no usable K26 source snapshot")
    ks = sorted(ELECTIONS) if baseline else [26]
    battery(knesset=ks, tests="", snapshot=snapshot, n_draws=2000)
    calibrate(knesset=26)
    output = build_publication(include_bundle=bundle)
    if figures:
        paths = build_figures(26, output.parent / "figures")
        typer.echo(f"figures: {len(paths)}")
    seal_issue(output.parent)
    typer.echo(f"reader issue -> {output}")


seats_app = typer.Typer(help="Seat arithmetic: Bader–Ofer allocation and the counterfactual seat estimate of the article.")
app.add_typer(seats_app, name="seats")


@seats_app.command("allocate")
def seats_allocate(
    knesset: int = typer.Option(26, "--knesset", "-k"),
    snapshot: Path | None = typer.Option(None, help="Explicit 2026 snapshot file (default: latest)"),
) -> None:
    """Allocate the 120 seats from a CEC station file (threshold, surplus agreements) and show the margin of the last seat."""
    from electionforensics.battery.t16_representation import load_agreements
    from electionforensics.ingest.cec import load_election
    from electionforensics.seats.allocation import election_allocation, load_blocs

    data = load_election(knesset, snapshot if knesset == 26 else None)
    a = election_allocation(knesset, data.stations, data.parties)
    _pairs, verified = load_agreements(knesset)
    typer.echo(f"K{knesset} {data.source.name} sha256={data.sha256[:12]} valid={a.valid:,} threshold={a.threshold_votes:,.0f} "
               f"wasted={a.wasted_votes:,} agreements={'verified' if verified else 'UNVERIFIED (press)'}")
    for p in a.eligible:
        typer.echo(f"  {p:<5} {a.votes[p]:>10,} {100 * a.votes[p] / a.valid:6.2f} %  seats {a.seats[p]:>3}")
    typer.echo(f"  blocs: {a.bloc_seats(load_blocs(knesset))}")
    if a.last_awarded and a.runner_up:
        typer.echo(f"  last seat: {a.last_awarded[0]} at quotient {a.last_awarded[1]:,.1f}; runner-up {a.runner_up[0]} at "
                   f"{a.runner_up[1]:,.1f}; margin {a.margin_votes:,.0f} votes")
    for n in a.notes:
        typer.echo(f"  note: {n}")


@seats_app.command("build")
def seats_build(
    snapshot: Path | None = typer.Option(None, help="Pinned 2026 station snapshot (default: latest on disk)"),
    out: Path = typer.Option(Path("docs") / "article", help="Article directory (results.json, tables/, figures/, the Markdown)"),
    deltas: str = typer.Option("0.4,1,1.2,2,3.4,5", help="Comma-separated δ grid in percentage points"),
    n_draws: int = typer.Option(200000, help="Monte-Carlo draws of δ"),
    seed: int = typer.Option(20261031, help="Monte-Carlo seed"),
    mc_median: float | None = typer.Option(None, help="Median of the log-normal δ prior (default √(0.4·5))"),
    mc_sigma: float | None = typer.Option(None, help="σ of the log-normal δ prior (default ln(5/0.4)/(2·1.645))"),
    figures: bool = typer.Option(True, help="Draw the figures"),
    article: bool = typer.Option(True, help="Render the Markdown article from its template"),
    pdf: bool = typer.Option(False, help="Compile the PDF with the external publisher script named by EF_PUBLISHER (needs pandoc and the typst wheel)"),
) -> None:
    """Recompute the article: factual allocation, coverage sets, the δ grid, Monte-Carlo, tables, figures, text."""
    from electionforensics.seats import article as art
    from electionforensics.seats.estimate import MC_MEDIAN, MC_SIGMA, run_estimate, save_results
    from electionforensics.seats.figures import build_figures

    t0 = time.time()
    ds = tuple(float(x) for x in deltas.split(",") if x.strip())
    res = run_estimate(snapshot, ds, n_draws, seed, mc_median or MC_MEDIAN, mc_sigma or MC_SIGMA)
    paths = save_results(res, out)
    d = res["data"]["current"]
    typer.echo(f"data: {d['file']} (snapshot {d['snapshot_ts']}) sha256={d['sha256']}  code={res['code_sha256'][:12]}  "
               f"agreements {'verified' if res['agreements']['verified'] else 'UNVERIFIED'}")
    f = res["factual"]
    typer.echo(f"factual: {f['blocs']}  margin of the last seat {f['margin_votes']:,.0f} votes")
    for key in res["coverage_order"]:
        c = res["coverage"][key]
        cells = []
        for dd in res["deltas"]:
            rec = next(r for r in res["grid"] if r["coverage"] == key and r["variant"] == "main" and r["delta"] == dd["delta"])
            cells.append(f"δ={dd['delta']:g}:{-rec['d_blocs']['coalition']}")
        typer.echo(f"  {key:<9} {100 * c['share_regular']:5.1f} % of the vote  coalition seats moved  " + "  ".join(cells))
    for key, m in res["mc"].items():
        typer.echo(f"  MC {key:<9} P(≥1)={m['p_ge']['1']:.2f} P(≥2)={m['p_ge']['2']:.2f} P(≥3)={m['p_ge']['3']:.2f} mean={m['mean_seats_lost']:.2f}")
    for pk, per in res.get("mc_priors", {}).items():
        if pk != res.get("main_prior"):
            typer.echo(f"  MC[{pk}] " + "  ".join(f"{k}: P(≥1)={m['p_ge']['1']:.2f} P(≥2)={m['p_ge']['2']:.2f} mean={m['mean_seats_lost']:.2f}"
                                                for k, m in per.items()))
    if res.get("coverage_scaling"):
        sc = res["coverage_scaling"]
        typer.echo(f"  scaling {sc['coverage']}: largest spread between orders at the same covered share "
                   + ", ".join(f"δ={d}: {s} seat(s)" for d, s in sc["max_spread"].items()))
    if figures:
        figs = build_figures(res, out / "figures")
        typer.echo(f"figures: {len(figs)} -> {out / 'figures'}")
    if article:
        md = art.render(res, out)
        wc = art.word_count(md.read_text(encoding="utf-8"))
        typer.echo(f"article -> {md}  words: body {wc['main_body']:,} (without tables {wc['main_body_without_tables']:,}), total {wc['total']:,}")
        if pdf:
            typer.echo(f"pdf -> {art.publish_pdf(md)}")
    typer.echo(f"results -> {paths['results']} [{time.time() - t0:.1f}s]")


incidents_app = typer.Typer(help="Evidence base: sources, exhibits, cases.")
app.add_typer(incidents_app, name="incidents")


@incidents_app.command("import")
def incidents_import(path: Path = typer.Argument(..., help="Seed YAML with cases and sources")) -> None:
    """Import a hand-structured seed file into data/incidents.sqlite and grade every case."""
    from electionforensics.incidents.seed import import_seed
    from electionforensics.incidents.store import Store

    store = Store()
    stats = import_seed(path, store)
    typer.echo(json.dumps(stats, ensure_ascii=False))
    typer.echo(json.dumps(store.counts()))


@incidents_app.command("collect")
def incidents_collect(
    wayback: bool = typer.Option(True, help="Submit each new candidate URL to the Wayback Machine"),
    tg_pages: int = typer.Option(2, help="Telegram preview pages per channel (~20 posts each)"),
    pace: float = typer.Option(5.0, help="Seconds between Wayback submissions"),
    max_wayback: int = typer.Option(60, help="Cap on Wayback submissions per run"),
) -> None:
    """Fetch the registered RSS feeds and Telegram channels, keep keyword candidates as exhibits, write a review queue."""
    from electionforensics.incidents.collect import collect
    from electionforensics.incidents.store import Store

    stats = collect(Store(), wayback=wayback, tg_pages=tg_pages, pace_s=pace, max_wayback=max_wayback)
    typer.echo(json.dumps(stats, ensure_ascii=False, indent=1))


@incidents_app.command("dose")
def incidents_dose(top: int = typer.Option(30, help="Rows to print")) -> None:
    """First-pass dose table: captured candidate reports per locality (overall and poster-related)."""
    from electionforensics.incidents.geo import dose_table, load_captures

    caps = load_captures()
    df = dose_table(caps)
    ensure_dirs()
    out = DATA_PROCESSED / "dose_reports_by_locality.csv"
    if not df.empty:
        df.to_csv(out, index=False, encoding="utf-8-sig")
    typer.echo(f"captures: {len(caps)}; localities mentioned: {len(df)} -> {out}")
    for _, r in df.head(top).iterrows():
        typer.echo(f"{r.locality:<18} reports={r.reports:>3} posters={r.poster_reports:>3} sources={r.n_sources}  {str(r.examples)[:110]}")


@incidents_app.command("cec-decisions")
def incidents_cec_decisions(
    directory: Path | None = typer.Option(None, help="Downloaded pages (default data/raw/knesset26/cec_decisions)"),
    since: str = typer.Option("2026-07-29", help="Campaign window start (ISO date) for the second per-locality table"),
    top: int = typer.Option(25, help="Local authorities to print (campaign window)"),
) -> None:
    """Flatten the downloaded register of the election-committee chairs' decisions; signage flags; counts per local authority."""
    from electionforensics.incidents.cec_decisions import DECISIONS_DIR, by_locality, load_decisions

    df = load_decisions(directory or DECISIONS_DIR)
    ensure_dirs()
    out = DATA_PROCESSED / "cec_decisions_k26.csv"
    df.to_csv(out, index=False, encoding="utf-8-sig")
    by_locality(df).to_csv(DATA_PROCESSED / "cec_decisions_by_locality.csv", index=False, encoding="utf-8-sig")
    loc = by_locality(df, since)
    out_loc = DATA_PROCESSED / "cec_decisions_by_locality_campaign.csv"
    loc.to_csv(out_loc, index=False, encoding="utf-8-sig")
    win = df[df["decided"] >= pd.Timestamp(since)]
    typer.echo(f"decisions: {len(df)} (signage {int(df['signage'].sum())}, removal {int(df['removal'].sum())}); "
               f"since {since}: {len(win)} (signage {int(win['signage'].sum())}, removal {int(win['removal'].sum())}), "
               f"named local authorities {len(loc)}; fetched {df.attrs['manifest'].get('fetched_at')} -> {out}, {out_loc}")
    for _, r in loc.head(top).iterrows():
        code = "" if pd.isna(r.locality_code) else int(r.locality_code)
        typer.echo(f"{r.local_authority:<28} {code!s:>5} decisions={r.n_decisions:>3} signage={r.n_signage:>3} removal={r.n_removal:>3}  {r.petitioners[:70]}")


@incidents_app.command("statement")
def incidents_statement(
    file: Path = typer.Option(Path("incidents/statements"), "--file",
                              help="Statement file (.md with YAML front matter) or a directory of them"),
    slug: str | None = typer.Option(None, "--case", help="Case slug (overrides the file's front matter)"),
    witness: str | None = typer.Option(None, help="Witness label as stored in sources (overrides front matter)"),
    number: int | None = typer.Option(None, help="Assign the editor's case number (overrides front matter)"),
    consent: str | None = typer.Option(None, help="Consent note (overrides front matter)"),
) -> None:
    """Record witness statements verbatim as exhibits (idempotent on the text); the front matter names the case."""
    from electionforensics.incidents.statements import import_statement, import_statements
    from electionforensics.incidents.store import Store

    store = Store()
    if file.is_dir():
        for eid, s, num in import_statements(file, store):
            typer.echo(f"statement -> exhibit {eid} for case {s}" + (f" (№{num})" if num else ""))
        return
    eid, s, num = import_statement(file, store, case=slug, witness=witness, number=number, consent=consent)
    typer.echo(f"statement recorded as exhibit {eid} for case {s}" + (f" (№{num})" if num else ""))


@incidents_app.command("photos")
def incidents_photos(
    directory: Path = typer.Option(..., "--dir", help="Folder with the witness's photos/videos (inbox/case-NN/...)"),
    slug: str = typer.Option(..., "--case", help="Case slug"),
    role: str = typer.Option("corroborates", help="Link role: reports / corroborates"),
    witness: str = typer.Option("editor (first-hand witness)", help="Witness label as stored in sources"),
    manifest_out: Path | None = typer.Option(None, "--manifest", help="Manifest CSV (default incidents/exhibits/<case>_media.csv)"),
    note: str | None = typer.Option(None, help="Note stored on every exhibit of this batch (e.g. 'context: daytime, not a polling site')"),
) -> None:
    """Hash every photo/video in a folder, read EXIF time/GPS/device, write the manifest and link them to the case."""
    from electionforensics.incidents.photos import import_media, write_manifest
    from electionforensics.incidents.store import Store

    new, total, records = import_media(directory, Store(), slug, witness=witness, role=role, note=note)
    out = write_manifest(records, manifest_out or Path("incidents/exhibits") / f"{slug}_media.csv")
    dated = sum(1 for r in records if r.taken_at)
    typer.echo(f"{total} files ({new} new exhibits, {dated} with EXIF time) -> {out}")


@incidents_app.command("list")
def incidents_list() -> None:
    """List open cases with grade, category and place."""
    from electionforensics.incidents.store import Store

    store = Store()
    for c in store.cases():
        typer.echo(f"[{c['grade'] or '-'}] {c['status']:<12} {c['category']:<24} {c['happened_from'] or '':<25} "
                   f"{(c['locality_name'] or '—'):<14} {c['title_ru'][:90]}")


@incidents_app.command("export")
def incidents_export(out: Path = typer.Option(Path("incidents/export"), help="Directory for CSV exports")) -> None:
    """Export cases (with latest grade) to CSV for the git-tracked evidence ledger."""
    import pandas as pd

    from electionforensics.incidents.store import Store

    store = Store()
    out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(store.cases()).to_csv(out / "cases.csv", index=False, encoding="utf-8-sig")
    for t in ("source", "exhibit", "link", "grade", "decision"):
        pd.read_sql_query(f'SELECT * FROM "{t}"', store.conn).to_csv(out / f"{t}.csv", index=False, encoding="utf-8-sig")  # noqa: S608
    typer.echo(f"exported to {out}: {store.counts()}")


@app.command()
def snapshots() -> None:
    """List the 2026 count snapshots on disk."""
    for p in snapshot_files("expb"):
        typer.echo(f"{p.name}  {p.stat().st_size:>10,d} bytes")


@app.command()
def status() -> None:
    """Show what exists in data/interim and data/processed."""
    for d in (DATA_INTERIM, DATA_PROCESSED):
        typer.echo(f"{d}:")
        if d.exists():
            for p in sorted(d.rglob("*"))[:200]:
                if p.is_file():
                    typer.echo(f"  {p.relative_to(d)}  {p.stat().st_size:,d}")


if __name__ == "__main__":
    app()
