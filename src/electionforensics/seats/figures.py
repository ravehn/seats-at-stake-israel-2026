"""The article's figures: static SVG and PNG drawn with matplotlib on the project's visual tokens (`present/theme.py`).

English labels only (the article is English; Hebrew list names appear in tables, not in charts). Text is written as
paths in the SVG so that the PDF renderer needs no fonts. Colour does one job: the three coverage sets wear fixed
categorical slots; magnitude on the δ axis is position, not colour.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib import rcParams
from matplotlib.figure import Figure

from electionforensics.present.theme import CATEGORICAL, TOKENS

COVERAGE_COLOUR = {"A": CATEGORICAL[0], "B": CATEGORICAL[1], "B_haredi": CATEGORICAL[2]}
COVERAGE_SHORT = {"A": "A — Likud first or within 5 pp (2022)", "B": "B — non-haredi Jewish sector", "B_haredi": "B⁺ — with haredi localities",
                  "A3": "A, 3-pp gap", "A7": "A, 7-pp gap", "A10": "A, 10-pp gap", "I": "I — Likud 2022 ≥ 28.5 %"}
SECTOR_COLOUR = {"jewish": CATEGORICAL[1], "mixed": CATEGORICAL[2], "haredi": CATEGORICAL[3], "druze": CATEGORICAL[4],
                 "arab": "#9aa6a8", "bedouin": "#c0c8c9", "unknown": "#d6dcdc"}
VARIANT_LABEL = {"main": "main: locality units, pro-rata return", "station": "station units", "victim_דרך": "all to Yashar",
                 "victim_אמת": "all to the Democrats", "victim_רק": "all to Beyachad", "turnout": "turnout channel (votes vanish)"}

rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 9.5, "svg.fonttype": "path", "svg.hashsalt": "electionforensics",
    "axes.edgecolor": TOKENS["rule"], "axes.labelcolor": TOKENS["ink_2"], "xtick.color": TOKENS["muted"],
    "ytick.color": TOKENS["muted"], "axes.titlecolor": TOKENS["ink"], "text.color": TOKENS["ink"],
    "axes.facecolor": TOKENS["card"], "figure.facecolor": TOKENS["paper"], "axes.spines.top": False,
    "axes.spines.right": False, "axes.grid": False, "legend.frameon": False, "legend.fontsize": 8.5,
    "axes.titlesize": 11, "axes.titlelocation": "left", "axes.titleweight": "normal",
})


def _save(fig: Figure, out: Path, stem: str) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    svg, png = out / f"{stem}.svg", out / f"{stem}.png"
    # no creation date and a fixed hash salt: the same results give byte-identical figures (replication)
    fig.savefig(svg, format="svg", bbox_inches="tight", facecolor=TOKENS["paper"], metadata={"Date": None})
    fig.savefig(png, format="png", dpi=200, bbox_inches="tight", facecolor=TOKENS["paper"])
    plt.close(fig)
    return [svg, png]


def _version_note(fig: Figure, results: dict[str, Any]) -> None:
    d = results["data"]["current"]
    fig.text(0.01, -0.02, f"CEC station file, snapshot {d['snapshot_ts']} · SHA-256 {d['sha256'][:12]}… · replication package {results['package_version']}",
             fontsize=7, color=TOKENS["muted"], ha="left", va="top")


def fig_coverage(results: dict[str, Any], out: Path) -> list[Path]:
    cov = results["coverage"]
    keys = [k for k in ("A", "B", "B_haredi", "I", "A3", "A7", "A10") if k in cov]
    fig, ax = plt.subplots(figsize=(7.2, 0.5 + 0.48 * len(keys)))
    sectors = ["jewish", "mixed", "haredi", "druze", "arab", "bedouin", "unknown"]
    y = np.arange(len(keys))[::-1]
    left = np.zeros(len(keys))
    for s in sectors:
        vals = np.array([100 * cov[k]["by_sector"].get(s, 0.0) for k in keys])
        if vals.sum() == 0:
            continue
        ax.barh(y, vals, left=left, color=SECTOR_COLOUR[s], height=0.62, label=s, edgecolor=TOKENS["card"], linewidth=0.5)
        left += vals
    for yi, k in zip(y, keys, strict=True):
        c = cov[k]
        ax.text(left[list(keys).index(k)] + 0.8, yi, f"{100 * c['share_regular']:.1f} %  ·  {c['n_localities']} localities, {c['n_stations']:,} stations",
                va="center", fontsize=8.5, color=TOKENS["ink_2"])
    short = dict(COVERAGE_SHORT)
    if results.get("itinerary"):  # reading I follows the itinerary table (Appendix G)
        short["I"] = f"I — Likud 2022 ≥ {100 * results['itinerary']['tau']:.1f} %"
    ax.set_yticks(y)
    ax.set_yticklabels([short.get(k, k) for k in keys])
    ax.set_xlim(0, 118)
    ax.set_xlabel("share of the valid votes cast at regular stations, 2026 (%)")
    ax.set_title("Where the premise is applied: coverage sets and their weight in the 2026 vote")
    ax.legend(ncol=4, loc="lower right", bbox_to_anchor=(1.0, -0.42), title="sector of the locality", title_fontsize=8)
    _version_note(fig, results)
    return _save(fig, out, "fig1_coverage")


def _lognormal_pdf(x: np.ndarray, median: float, sigma: float) -> np.ndarray:
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(x > 0, np.exp(-((np.log(x) - math.log(median)) ** 2) / (2 * sigma**2)) / (x * sigma * math.sqrt(2 * math.pi)), 0.0)


def fig_curves(results: dict[str, Any], out: Path) -> list[Path]:
    curves = results["_curves"]
    deltas = [d["delta"] for d in results["deltas"]]
    mcp = results["mc_params"]
    fig, (ax, axd) = plt.subplots(2, 1, figsize=(7.2, 5.4), sharex=True, gridspec_kw={"height_ratios": [3.2, 1.0], "hspace": 0.08})
    for k in ("A", "B", "B_haredi"):
        if k not in curves:
            continue
        c = curves[k]
        # reading B is the main reading of the premise and is drawn heaviest; B⁺ dashed
        ax.step(c["delta"], -c["d_coalition"], where="post", color=COVERAGE_COLOUR[k],
                lw={"A": 1.6, "B": 2.6, "B_haredi": 1.2}.get(k, 1.6), ls="-" if k != "B_haredi" else "--", label=COVERAGE_SHORT[k])
    for d in deltas:
        ax.axvline(d, color=TOKENS["grid"], lw=1, zorder=0)
        axd.axvline(d, color=TOKENS["grid"], lw=1, zorder=0)
    # the central scenario of the article, δ = 1
    ax.axvline(1.0, color=TOKENS["accent_ink"], lw=1.4, ls="--", zorder=1)
    ax.text(1.0, 0.06, " δ = 1, central scenario", ha="left", va="bottom", fontsize=8, color=TOKENS["accent_ink"],
            transform=ax.get_xaxis_transform())
    ymax = max(int((-curves[k]["d_coalition"]).max()) for k in curves if k in COVERAGE_COLOUR) + 1
    ax.set_ylim(-0.2, ymax + 0.4)
    ax.set_yticks(range(0, ymax + 1))
    ax.set_ylabel("coalition seats moved by the counterfactual")
    ax.set_title("Seats as a step function of the posited effect δ (main variant)")
    ax.legend(loc="upper left")
    for d in deltas:
        ax.text(d, ymax + 0.3, f"{d:g}", ha="center", va="bottom", fontsize=7.5, color=TOKENS["muted"])
    x = np.linspace(0.01, mcp["curve_max"], 600)
    pdf = _lognormal_pdf(x, mcp["median"], mcp["sigma"])
    priors = results.get("priors", [])
    main_key = results.get("main_prior")
    main_label = next((p["label"] for p in priors if p["key"] == main_key), "main")
    axd.fill_between(x, 0, pdf, color=TOKENS["accent_bg"], lw=0)
    axd.plot(x, pdf, color=TOKENS["accent"], lw=1.2, label=f"{main_label} (main)")
    for p in priors:  # the other distributions of Table 3b, for the robustness table of § 5.3
        if p["key"] == main_key:
            continue
        axd.plot(x, _lognormal_pdf(x, p["median"], p["sigma"]), color=TOKENS["ink_2"], lw=0.9,
                 ls="--" if p["key"] == "premise" else ":", label=p["label"])
    if priors:
        axd.legend(loc="upper right", fontsize=7, frameon=False)
    for q in (mcp["low"], mcp["median"], mcp["high"]):
        axd.axvline(q, color=TOKENS["accent_ink"], lw=0.8, ls=":")
    axd.text(mcp["median"], pdf.max() * 1.05, f"median {mcp['median']:.2f}", ha="center", fontsize=7.5, color=TOKENS["accent_ink"])
    axd.text(mcp["low"], pdf.max() * 0.55, f" 5th pct {mcp['low']:g}", ha="left", fontsize=7.5, color=TOKENS["accent_ink"])
    axd.text(mcp["high"], pdf.max() * 0.55, f"95th pct {mcp['high']:g} ", ha="right", fontsize=7.5, color=TOKENS["accent_ink"])
    axd.set_yticks([])
    axd.set_ylabel("δ prior", color=TOKENS["ink_2"])
    axd.set_xlabel("δ — percentage points of a unit's valid votes moved from Likud to the change bloc")
    axd.set_xlim(0, mcp["curve_max"])
    _version_note(fig, results)
    return _save(fig, out, "fig2_step_curves")


def fig_mc(results: dict[str, Any], out: Path) -> list[Path]:
    mc = results["mc"]
    keys = [k for k in ("A", "B", "B_haredi") if k in mc]
    kmax = max(max(int(k) for k in mc[c]["pmf_seats_lost"]) for c in keys)
    fig, ax = plt.subplots(figsize=(7.2, 3.4))
    w = 0.8 / len(keys)
    x = np.arange(kmax + 1)
    for i, c in enumerate(keys):
        p = np.array([mc[c]["pmf_seats_lost"].get(str(k), mc[c]["pmf_seats_lost"].get(k, 0.0)) for k in range(kmax + 1)])
        ax.bar(x + (i - (len(keys) - 1) / 2) * w, 100 * p, width=w * 0.92, color=COVERAGE_COLOUR[c], label=COVERAGE_SHORT[c])
        for xi, pi in zip(x, p, strict=True):
            if pi >= 0.02:
                ax.text(xi + (i - (len(keys) - 1) / 2) * w, 100 * pi + 1, f"{100 * pi:.0f}", ha="center", fontsize=7.5, color=TOKENS["ink_2"])
    ax.set_xticks(x)
    ax.set_xlabel("coalition seats moved")
    ax.set_ylabel("probability (%)")
    ax.set_title(f"Monte-Carlo over δ ~ log-normal(median {results['mc_params']['median']:.2f}, σ {results['mc_params']['sigma']:.2f}), "
                 f"{results['mc_params']['n_draws']:,} draws")
    ax.legend(loc="upper right")
    ax.set_ylim(0, max(100 * max(mc[c]["pmf_seats_lost"].values()) for c in keys) * 1.25)
    _version_note(fig, results)
    return _save(fig, out, "fig3_monte_carlo")


def fig_sensitivity(results: dict[str, Any], out: Path) -> list[Path]:
    grid = results["grid"]
    deltas = [d["delta"] for d in results["deltas"]]
    variants = [v["key"] for v in results["variants"]]
    markers = {"main": "o", "station": "s", "victim_דרך": "^", "victim_אמת": "v", "victim_רק": "<", "turnout": "x"}
    keys = [k for k in ("A", "B") if k in results["coverage"]]
    fig, axes = plt.subplots(1, len(keys), figsize=(7.2, 3.3), sharey=True)
    axes_list = list(np.atleast_1d(axes))
    for ax, cov in zip(axes_list, keys, strict=True):
        for j, v in enumerate(variants):
            xs, ys = [], []
            for d in deltas:
                rec = next((r for r in grid if r["coverage"] == cov and r["variant"] == v and r["delta"] == d), None)
                if rec is None:
                    continue
                xs.append(deltas.index(d) + (j - (len(variants) - 1) / 2) * 0.11)
                ys.append(-rec["d_blocs"]["coalition"])
            ax.scatter(xs, ys, marker=markers.get(v, "o"), s=28, color=COVERAGE_COLOUR[cov] if v == "main" else TOKENS["ink_2"],
                       alpha=1.0 if v == "main" else 0.75, label=VARIANT_LABEL.get(v, v), zorder=3)
        ax.set_xticks(range(len(deltas)))
        ax.set_xticklabels([f"{d:g}" for d in deltas])
        ax.set_xlabel("δ (pp)")
        ax.set_title(COVERAGE_SHORT[cov])
        ax.grid(axis="y", color=TOKENS["grid"], lw=0.8)
    axes_list[0].set_ylabel("coalition seats moved")
    ymax = max(-r["d_blocs"]["coalition"] for r in grid if r["coverage"] in keys) + 1
    axes_list[0].set_ylim(-0.3, ymax)
    axes_list[0].set_yticks(range(0, ymax + 1))
    handles, labels = axes_list[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=3, bbox_to_anchor=(0.5, -0.2))
    fig.suptitle("Sensitivity to the transfer variant", x=0.01, y=1.04, ha="left", fontsize=11)
    d = results["data"]["current"]
    fig.text(0.01, -0.3, f"CEC station file, snapshot {d['snapshot_ts']} · SHA-256 {d['sha256'][:12]}… · replication package {results['package_version']}",
             fontsize=7, color=TOKENS["muted"], ha="left", va="top")
    return _save(fig, out, "fig4_sensitivity")


def fig_scaling(results: dict[str, Any], out: Path) -> list[Path]:
    """Coalition seats moved against the share of the B⁺ vote covered: Likud-share order against random orders."""
    sc = results.get("coverage_scaling")
    if not sc:
        return []
    rows = pd.DataFrame(sc["rows"])
    deltas = [float(d) for d in sc["deltas"]]
    likud_order = sc["orders"][0]
    fig, axes = plt.subplots(1, len(deltas), figsize=(7.2, 3.3), sharey=True)
    axes_list = list(np.atleast_1d(axes))
    for ax, d in zip(axes_list, deltas, strict=True):
        sub = rows[rows["delta"] == d]
        random_labelled = False
        for name in sc["orders"]:
            g = sub[sub["order"] == name].sort_values("target_share")
            x, y = 100 * g["covered_share_of_set"].to_numpy(), g["coalition_seats_moved"].to_numpy()
            if name == likud_order:
                ax.plot(x, y, marker="o", ms=4, lw=2, color=COVERAGE_COLOUR["B_haredi"], label="localities in the order of Likud's 2022 share", zorder=3)
            else:
                ax.plot(x, y, marker="o", ms=3, lw=1, color=TOKENS["ink_2"], alpha=0.45,
                        label=None if random_labelled else f"{sc['n_random']} random orders", zorder=2)
                random_labelled = True
        for key, m in sc["marks"].items():
            seats = m["seats"].get(f"{d:g}")
            if m["share_of_set"] is None or seats is None:
                continue
            ax.scatter([100 * m["share_of_set"]], [seats], marker="D", s=46, color=COVERAGE_COLOUR[key], edgecolor=TOKENS["card"],
                       linewidth=0.6, zorder=4, label=f"reading {key}")
            ax.annotate(key, (100 * m["share_of_set"], seats), xytext=(0, 7), textcoords="offset points", ha="center", fontsize=8.5,
                        color=COVERAGE_COLOUR[key])
        ax.set_title(f"δ = {d:g} pp")
        ax.set_xlabel("share of the B⁺ vote covered (%)")
        ax.set_xlim(0, 104)
        ax.grid(axis="y", color=TOKENS["grid"], lw=0.8)
    ymax = int(rows["coalition_seats_moved"].max()) + 1
    axes_list[0].set_ylim(-0.3, ymax)
    axes_list[0].set_yticks(range(0, ymax + 1))
    axes_list[0].set_ylabel("coalition seats moved")
    handles, labels = axes_list[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=2, bbox_to_anchor=(0.5, -0.2))
    fig.suptitle("Seats against the covered share of the vote (main variant)", x=0.01, y=1.04, ha="left", fontsize=11)
    dcur = results["data"]["current"]
    fig.text(0.01, -0.3, f"CEC station file, snapshot {dcur['snapshot_ts']} · SHA-256 {dcur['sha256'][:12]}… · replication package {results['package_version']}",
             fontsize=7, color=TOKENS["muted"], ha="left", va="top")
    return _save(fig, out, "fig5_coverage_scaling")


def build_figures(results: dict[str, Any], out: Path) -> list[Path]:
    paths: list[Path] = []
    paths += fig_coverage(results, out)
    paths += fig_curves(results, out)
    if results.get("mc"):
        paths += fig_mc(results, out)
    paths += fig_sensitivity(results, out)
    paths += fig_scaling(results, out)
    return paths
