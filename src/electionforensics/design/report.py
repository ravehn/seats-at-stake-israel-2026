"""Tables and figures of the design document, drawn with the project theme and stamped with the data version.

Everything written here is derived from the simulation results (`simulate_grid`, `retro_power`) and the panel's
source record; the figures are static SVG (no script, no embedded font) so that the Markdown document renders
on GitHub and in the PDF build alike. Colours and type come from `present.theme`; nothing carries a raw colour.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib import rcParams
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.figure import Figure

from electionforensics import __version__
from electionforensics.design.panel import Panel
from electionforensics.design.simulate import normal_approx_mde, normal_approx_n, normal_approx_power
from electionforensics.present.theme import CATEGORICAL, HISTORY_GREYS, SEQUENTIAL, TOKENS

FONT_STACK = "'Segoe UI', 'DejaVu Sans', 'Noto Sans Hebrew', Arial, sans-serif"
DESIGN_RU = {"two_arm": "две симметричные руки", "one_arm": "одна рука против контроля"}
OUTCOME_RU = {"likud": "доля Ликуда", "coalition": "доля коалиции", "change": "доля блока смены"}


def _style() -> None:
    rcParams.update({
        "font.family": "DejaVu Sans", "font.size": 10, "svg.fonttype": "none",
        "axes.edgecolor": TOKENS["rule"], "axes.labelcolor": TOKENS["ink_2"], "xtick.color": TOKENS["muted"],
        "ytick.color": TOKENS["muted"], "axes.titlecolor": TOKENS["ink"], "text.color": TOKENS["ink"],
        "axes.facecolor": TOKENS["card"], "figure.facecolor": TOKENS["paper"], "axes.spines.top": False,
        "axes.spines.right": False, "axes.grid": True, "grid.color": TOKENS["grid"], "grid.linewidth": 0.8,
        "legend.frameon": False, "legend.fontsize": 9,
    })


def data_stamp(source: dict[str, Any]) -> str:
    """One line naming the inputs: files, snapshot stamp, short hashes — printed under every figure."""
    prev, curr = source.get("prev", {}), source.get("curr", {})
    parts = [f"Кнессет {prev.get('knesset')}: {prev.get('file')} ({str(prev.get('sha256', ''))[:12]})",
             f"Кнессет {curr.get('knesset')}: {curr.get('file')} ({str(curr.get('sha256', ''))[:12]})",
             f"зданий {source.get('n_buildings')}, участков {source.get('n_stations')}", f"electionforensics {__version__}"]
    return " · ".join(parts)


def save_figure(fig: Figure, path: Path, stamp: str | None = None, png_dir: Path | None = None) -> Path:
    if stamp:
        fig.text(0.01, 0.005, stamp, fontsize=6.5, color=TOKENS["muted"], ha="left", va="bottom")
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, format="svg", bbox_inches="tight", facecolor=TOKENS["paper"])
    if png_dir is not None:
        png_dir.mkdir(parents=True, exist_ok=True)
        fig.savefig(png_dir / path.with_suffix(".png").name, format="png", dpi=110, bbox_inches="tight", facecolor=TOKENS["paper"])
    plt.close(fig)
    text = path.read_text(encoding="utf-8")
    for old in ("font-family: 'DejaVu Sans'", "font-family:'DejaVu Sans'", "font-family: DejaVu Sans"):
        text = text.replace(old, f"font-family: {FONT_STACK}")
    path.write_bytes(text.encode("utf-8"))
    return path


# ------------------------------------------------------------------------------------------- tables

def _md_table(df: pd.DataFrame, index_label: str, fmt: str = "{:.2f}") -> str:
    cols = [str(c) for c in df.columns]
    lines = ["| " + index_label + " | " + " | ".join(cols) + " |", "|---|" + "---:|" * len(cols)]
    for idx, row in df.astype(object).iterrows():
        cells = [("—" if pd.isna(v) else (fmt.format(v) if isinstance(v, float | np.floating) else str(v))) for v in row]
        lines.append(f"| {idx} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def power_table(results: pd.DataFrame, design: str, compliance: float, kind: str = "itt") -> pd.DataFrame:
    """n × δ matrix of power for one design and compliance level."""
    d = results[(results["design"] == design) & (np.isclose(results["compliance"], compliance))]
    t = d.pivot_table(index="n", columns="delta", values=f"power_{kind}", aggfunc="first")
    t.columns = [f"δ = {c:g}" for c in t.columns]
    return t


def mde_table(results: pd.DataFrame, sigma: float, compliance: float = 1.0) -> pd.DataFrame:
    """Simulated MDE (from the mean SE) against the normal approximation, per design and n."""
    rows = []
    for design in results["design"].unique():
        d = results[(results["design"] == design) & np.isclose(results["compliance"], compliance) & (results["delta"] == 1.0)]
        for _, r in d.sort_values("n").iterrows():
            rows.append({"дизайн": DESIGN_RU.get(design, design), "n": int(r["n"]), "MDE симуляция, п.п.": r["mde_itt"],
                         "MDE нормальное приближение, п.п.": normal_approx_mde(sigma, int(r["n"]), design),
                         "SE оценки δ (симуляция)": r["mean_se_itt"], "эмпирическое SD оценки": r["sd_coef_itt"]})
    return pd.DataFrame(rows).set_index(["дизайн", "n"])


def _interp_n_for_power(d: pd.DataFrame, target: float = 0.8, kind: str = "itt") -> float:
    """Smallest n at which the simulated power curve crosses `target` (linear interpolation between grid points)."""
    d = d.sort_values("n")
    ns, pw = d["n"].to_numpy(float), d[f"power_{kind}"].to_numpy(float)
    if pw.size == 0 or pw.max() < target:
        return float("nan")
    if pw[0] >= target:
        return float(ns[0])
    i = int(np.argmax(pw >= target))
    x0, x1, y0, y1 = ns[i - 1], ns[i], pw[i - 1], pw[i]
    return float(x0 + (target - y0) * (x1 - x0) / max(y1 - y0, 1e-9))


def sample_size_table(results: pd.DataFrame, sigma: float, compliance: float = 1.0) -> pd.DataFrame:
    """Buildings for 80 % power: simulated curve against the planning note's normal approximation."""
    rows = []
    for design in results["design"].unique():
        for delta in sorted(results["delta"].unique()):
            if delta <= 0:
                continue
            d = results[(results["design"] == design) & np.isclose(results["compliance"], compliance) & (results["delta"] == delta)]
            rows.append({"дизайн": DESIGN_RU.get(design, design), "δ, п.п.": delta,
                         "зданий (симуляция, 80 %)": _interp_n_for_power(d),
                         "зданий (нормальное приближение)": normal_approx_n(delta, sigma, design),
                         "мощность при наибольшем n сетки": float(cast(Any, d.sort_values("n")["power_itt"].iloc[-1])) if len(d) else float("nan")})
    return pd.DataFrame(rows).set_index(["дизайн", "δ, п.п."])


def compliance_table(results: pd.DataFrame, design: str, n: int, delta: float) -> pd.DataFrame:
    d = results[(results["design"] == design) & (results["n"] == n) & (results["delta"] == delta)].sort_values("compliance")
    t = d.set_index("compliance")[["power_itt", "bias_itt", "coverage_itt", "truth_itt", "power_cace", "bias_cace", "coverage_cace", "truth_cace"]]
    t.index = [f"{c:.0%}" for c in t.index]
    t.columns = ["мощность ITT", "смещение ITT", "покрытие ITT", "истинный ITT", "мощность CACE", "смещение CACE", "покрытие CACE", "истинный CACE"]
    return t


def write_tables(results: pd.DataFrame, noise: pd.DataFrame, path: Path, sigma: float,
                 variants: pd.DataFrame | None = None) -> Path:
    """The Markdown tables the document quotes, regenerated from the results."""
    out: list[str] = ["# Таблицы мощности (сгенерировано `ef design power`)", ""]
    out += ["## Шум панели", "", _md_table(noise.set_index("outcome"), "исход"), ""]
    for design in results["design"].unique():
        for c in sorted(results["compliance"].unique(), reverse=True):
            out += [f"## Мощность ITT — {DESIGN_RU.get(design, design)}, соблюдение {c:.0%}", "",
                    _md_table(power_table(results, design, c), "n зданий"), ""]
            if c < 1:
                out += [f"## Мощность CACE — {DESIGN_RU.get(design, design)}, соблюдение {c:.0%}", "",
                        _md_table(power_table(results, design, c, "cace"), "n зданий"), ""]
    out += ["## Число зданий для 80 % мощности: симуляция против нормального приближения", "",
            _md_table(sample_size_table(results, sigma).reset_index().set_index("дизайн"), "дизайн", "{:.1f}"), ""]
    out += ["## MDE по n (δ = 1 сценарий, соблюдение 100 %)", "", _md_table(mde_table(results, sigma).reset_index().set_index("дизайн"), "дизайн"), ""]
    for design in results["design"].unique():
        base_n = 96 if design == "two_arm" else 400
        if ((results["design"] == design) & (results["n"] == base_n) & (results["delta"] == 1.0)).any():
            out += [f"## Соблюдение — {DESIGN_RU.get(design, design)}, n = {base_n}, δ = 1", "",
                    _md_table(compliance_table(results, design, base_n, 1.0), "соблюдение"), ""]
    cov = results[results["delta"] > 0].groupby("design")[["coverage_itt", "bias_itt", "coverage_cace", "bias_cace"]].mean()
    out += ["## Покрытие 95 % интервалов и смещение (среднее по сетке, δ > 0)", "", _md_table(cov, "дизайн", "{:.3f}"), ""]
    null = results[results["delta"] == 0].groupby("design")[["power_itt", "power_cace"]].mean()
    out += ["## Доля ложных срабатываний при δ = 0 (α = 0,05)", "", _md_table(null, "дизайн", "{:.3f}"), ""]
    if variants is not None and not variants.empty:
        keys = ["design", "n", "delta", "strata", "heterogeneity", "mechanism", "covariates", "min_distance", "inference"]
        v = variants.set_index([k for k in keys if k in variants.columns])[
            ["n_realised", "power_itt", "mean_se_itt", "bias_itt", "coverage_itt", "dof"]]
        out += ["## Варианты анализа (соблюдение 100 %)", "", _md_table(v.reset_index().set_index("design"), "дизайн", "{:.3f}"), ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(("\n".join(out) + "\n").encode("utf-8"))
    return path


# ------------------------------------------------------------------------------------------- figures

def fig_power_curves(results: pd.DataFrame, sigma: float, path: Path, stamp: str, compliance: float = 1.0,
                     png_dir: Path | None = None) -> Path:
    """Power against n per δ, one panel per design; dashed grey = the normal approximation of the planning note."""
    _style()
    designs = [d for d in ("two_arm", "one_arm") if d in set(results["design"])]
    fig, axes = plt.subplots(1, len(designs), figsize=(4.6 * len(designs), 3.9), sharey=True, squeeze=False)
    deltas = [d for d in sorted(results["delta"].unique()) if d > 0]
    colours = dict(zip(deltas, (CATEGORICAL * 3)[: len(deltas)], strict=True))
    for ax, design in zip(axes[0], designs, strict=True):
        d = results[(results["design"] == design) & np.isclose(results["compliance"], compliance)]
        ns = np.linspace(max(d["n"].min(), 8), d["n"].max(), 100)
        for delta in deltas:
            dd = d[d["delta"] == delta].sort_values("n")
            ax.plot(ns, [normal_approx_power(delta, sigma, int(n), design) for n in ns], color=HISTORY_GREYS[1], lw=1, ls="--", zorder=1)
            ax.plot(dd["n"], dd["power_itt"], marker="o", ms=4, lw=1.8, color=colours[delta], label=f"δ = {delta:g} п.п.", zorder=3)
        ax.axhline(0.8, color=TOKENS["rule"], lw=1, zorder=0)
        ax.set_title(DESIGN_RU[design], fontsize=11, loc="left")
        ax.set_xlabel("зданий в выборке (всего)")
        ax.set_ylim(0, 1.02)
    axes[0][0].set_ylabel("мощность (доля повторов с p < 0,05)")
    axes[0][0].legend(loc="lower right", title="эффект одной руки", title_fontsize=9)
    fig.suptitle(f"Мощность ITT по симуляции на реальной панели (соблюдение {compliance:.0%}); пунктир — нормальное приближение",
                 x=0.01, ha="left", fontsize=10.5, color=TOKENS["ink_2"])
    fig.tight_layout(rect=(0, 0.03, 1, 0.95))
    return save_figure(fig, path, stamp, png_dir)


def fig_power_heatmap(results: pd.DataFrame, path: Path, stamp: str, design: str = "two_arm", compliance: float = 0.8,
                      kind: str = "itt", png_dir: Path | None = None) -> Path:
    """n × δ grid of power as a one-hue heatmap with the numbers printed in the cells."""
    _style()
    t = power_table(results, design, compliance, kind)
    if t.empty:
        raise ValueError("no results for the heatmap")
    cmap = LinearSegmentedColormap.from_list("ef_seq", SEQUENTIAL)
    fig, ax = plt.subplots(figsize=(1.0 + 0.9 * t.shape[1], 0.9 + 0.42 * t.shape[0]))
    z = t.to_numpy(float)
    ax.imshow(z, cmap=cmap, vmin=0, vmax=1, aspect="auto")
    ax.set_xticks(range(t.shape[1]), labels=list(t.columns))
    ax.set_yticks(range(t.shape[0]), labels=[str(int(i)) for i in t.index])
    ax.set_xlabel("эффект одной руки δ")
    ax.set_ylabel("зданий в выборке")
    ax.grid(False)
    for i in range(z.shape[0]):
        for j in range(z.shape[1]):
            v = z[i, j]
            if np.isfinite(v):
                ax.text(j, i, f"{v:.2f}", ha="center", va="center", fontsize=8.5, color=TOKENS["paper"] if v > 0.55 else TOKENS["ink"])
    ax.set_title(f"{DESIGN_RU[design]}: мощность {kind.upper()} при соблюдении {compliance:.0%}", loc="left", fontsize=10.5)
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    return save_figure(fig, path, stamp, png_dir)


def fig_compliance(results: pd.DataFrame, path: Path, stamp: str, design: str = "two_arm", n: int = 96, delta: float = 1.0,
                   png_dir: Path | None = None) -> Path:
    """Power and bias of ITT against CACE as compliance falls."""
    _style()
    d = results[(results["design"] == design) & (results["n"] == n) & (results["delta"] == delta)].sort_values("compliance")
    if d.empty:
        raise ValueError("no results for the compliance figure")
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(9.2, 3.6))
    x = 100 * d["compliance"].to_numpy(float)
    a1.plot(x, d["power_itt"], marker="o", color=CATEGORICAL[0], label="ITT")
    a1.plot(x, d["power_cace"], marker="s", color=CATEGORICAL[1], label="CACE")
    a1.axhline(0.8, color=TOKENS["rule"], lw=1)
    a1.set_ylim(0, 1.02)
    a1.set_xlabel("соблюдение, % назначенных зданий")
    a1.set_ylabel("мощность")
    a1.set_xticks(x)
    a1.legend()
    a2.plot(x, d["bias_itt"] + d["truth_itt"], marker="o", color=CATEGORICAL[0], label="средняя оценка ITT")
    a2.plot(x, d["bias_cace"] + d["truth_cace"], marker="s", color=CATEGORICAL[1], label="средняя оценка CACE")
    a2.axhline(delta, color=TOKENS["rule"], lw=1, ls="--")
    a2.text(x.min(), delta, f" δ соблюдающего = {delta:g}", fontsize=8, color=TOKENS["ink_2"], va="bottom")
    a2.set_xlabel("соблюдение, % назначенных зданий")
    a2.set_ylabel("оценка эффекта, п.п.")
    a2.set_xticks(x)
    a2.legend()
    fig.suptitle(f"{DESIGN_RU[design]}, n = {n}, δ = {delta:g}: ITT падает с соблюдением, CACE возвращает δ соблюдающего",
                 x=0.01, ha="left", fontsize=10.5, color=TOKENS["ink_2"])
    fig.tight_layout(rect=(0, 0.04, 1, 0.94))
    return save_figure(fig, path, stamp, png_dir)


def fig_swings(panel: Panel, path: Path, stamp: str, png_dir: Path | None = None) -> Path:
    """What the estimator has to see through: the distribution of building swings and their locality structure."""
    _style()
    b = panel.buildings
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(9.2, 3.6))
    bins = np.arange(-20, 20.5, 1.0)
    a1.hist(b["swing_likud"], bins=bins, color=CATEGORICAL[0], alpha=0.85, label="Ликуд")
    a1.hist(b["swing_coalition"], bins=bins, histtype="step", color=CATEGORICAL[1], lw=1.5, label="коалиция")
    a1.set_xlabel(f"сдвиг доли по зданию {panel.prev}→{panel.curr}, п.п.")
    a1.set_ylabel("зданий")
    a1.legend()
    g = b.groupby("locality_code")["swing_likud"]
    within = b["swing_likud"] - g.transform("mean")
    multi = g.transform("size") >= 2
    n_g = g.transform("size")[multi]
    sd_within = float(within[multi].std() * np.sqrt((n_g / (n_g - 1)).mean()))
    a2.hist(b["swing_likud"], bins=bins, color=HISTORY_GREYS[2], alpha=0.9, label=f"по стране, SD {b['swing_likud'].std():.2f}")
    a2.hist(within[multi], bins=bins, histtype="step", color=CATEGORICAL[0], lw=1.6,
            label=f"остаток от среднего пункта, SD {sd_within:.2f} (с поправкой)")
    a2.set_xlabel("сдвиг доли Ликуда по зданию, п.п.")
    a2.legend()
    fig.suptitle("Шум, сквозь который должен смотреть эксперимент: сдвиги долей по зданиям между выборами",
                 x=0.01, ha="left", fontsize=10.5, color=TOKENS["ink_2"])
    fig.tight_layout(rect=(0, 0.04, 1, 0.94))
    return save_figure(fig, path, stamp, png_dir)


TIMELINE: list[tuple[str, int, int]] = [
    # (label, start day relative to election day E, end day)
    ("соглашение штабов о невывеске; этическая комиссия", -180, -120),
    ("предварительная регистрация (OSF), фиксация источника сида", -120, -90),
    ("выборка зданий и жребий рук по публичному сиду; уведомление ЦИК / МВД", -75, -60),
    ("агитационный период: штабы вешают по назначению", -45, -1),
    ("перепись оград: вечер накануне (после 19:00)", -1, -1),
    ("перепись оград: утро дня голосования (до 07:00) и полдень", 0, 0),
    ("официальные итоги по участкам (Решумот)", 1, 8),
    ("анализ по плану, отчёт", 8, 45),
]


def fig_timeline(path: Path, stamp: str, png_dir: Path | None = None) -> Path:
    """The same protocol on two calendars: a Knesset election (date unknown) and the municipal elections of autumn 2028."""
    _style()
    fig, axes = plt.subplots(2, 1, figsize=(10.4, 6.2), sharex=True)
    titles = ["Вариант 1 — выборы в Кнессет (E = день голосования; дата зависит от роспуска; партнёры — штабы блоков, ЦИК)",
              "Вариант 2 — очередные муниципальные выборы, осень 2028 (E ≈ конец октября 2028; партнёры — местные списки, МВД)"]
    labels = [t[0] for t in TIMELINE]
    for ax, title in zip(axes, titles, strict=True):
        for i, (_label, s, e) in enumerate(TIMELINE):
            y = len(TIMELINE) - i
            colour = CATEGORICAL[0] if (s <= 0 <= e) else (CATEGORICAL[1] if s < 0 else CATEGORICAL[2])
            if s == e:
                ax.plot([s], [y], marker="D", ms=7, color=colour, zorder=3)
            else:
                ax.barh(y, e - s, left=s, height=0.55, color=colour, alpha=0.85)
        ax.axvline(0, color=TOKENS["accent"], lw=1.2)
        ax.set_yticks(range(len(TIMELINE), 0, -1), labels=labels, fontsize=8.4)
        ax.set_ylim(0.3, len(TIMELINE) + 0.9)
        ax.set_title(title, loc="left", fontsize=9.6)
        ax.grid(axis="y", visible=False)
        ax.tick_params(axis="y", length=0)
    axes[1].set_xlabel("дней относительно дня голосования E (красная линия)")
    axes[1].set_xlim(-200, 60)
    fig.suptitle("График основного дизайна на двух календарях — без выбора между ними", x=0.01, ha="left", fontsize=10.5,
                 color=TOKENS["ink_2"])
    fig.tight_layout(rect=(0, 0.04, 1, 0.95))
    return save_figure(fig, path, stamp, png_dir)


def fig_retro(summary: pd.DataFrame, path: Path, stamp: str, outcome: str = "s_מחל", png_dir: Path | None = None) -> Path:
    """Power of the retrospective twins design against the injected city-wide δ, one line per number of cities."""
    _style()
    d = summary[summary["outcome"] == outcome]
    if d.empty:
        raise ValueError("no retro results for this outcome")
    fig, ax = plt.subplots(figsize=(5.6, 3.8))
    for i, (m, g) in enumerate(d.groupby("m")):
        g = g.sort_values("delta")
        ax.plot(g["delta"], g["power"], marker="o", color=CATEGORICAL[i % len(CATEGORICAL)], label=f"{int(cast(Any, m))} городов")
    ax.axhline(0.8, color=TOKENS["rule"], lw=1)
    ax.set_ylim(0, 1.02)
    ax.set_xlabel("общегородской эффект δ, п.п. доли Ликуда")
    ax.set_ylabel("мощность (объединённый пермутационный тест)")
    ax.legend(title="«зарегистрированных» городов", title_fontsize=9)
    ax.set_title("Ретроспективный дизайн A (города-близнецы): чувствительность", loc="left", fontsize=10.5)
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    return save_figure(fig, path, stamp, png_dir)


# ------------------------------------------------------------------------------------------- bundle

def write_results(results: pd.DataFrame, panel: Panel, out_dir: Path, prefix: str, params: dict[str, Any],
                  noise: pd.DataFrame, variants: pd.DataFrame | None = None, png_dir: Path | None = None) -> list[Path]:
    """CSV + params + Markdown tables + the four figures that depend on the power grid."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    results.to_csv(out_dir / f"{prefix}_grid.csv", index=False, encoding="utf-8", lineterminator="\n")
    written.append(out_dir / f"{prefix}_grid.csv")
    if variants is not None:
        variants.to_csv(out_dir / f"{prefix}_variants.csv", index=False, encoding="utf-8", lineterminator="\n")
        written.append(out_dir / f"{prefix}_variants.csv")
    noise.to_csv(out_dir / f"{prefix}_noise.csv", index=False, encoding="utf-8", lineterminator="\n")
    p = out_dir / f"{prefix}_params.json"
    p.write_bytes(json.dumps({**params, "source": panel.source, "version": __version__}, ensure_ascii=False, indent=1).encode("utf-8"))
    written.append(p)
    sigma = float(noise.set_index("outcome")["sd_within_locality"]["likud"])
    written.append(write_tables(results, noise, out_dir / f"{prefix}_tables.md", sigma, variants))
    stamp = data_stamp(panel.source)
    written.append(fig_power_curves(results, sigma, out_dir / f"{prefix}_fig1_power_curves.svg", stamp, png_dir=png_dir))
    if (np.isclose(results["compliance"], 0.8)).any():
        written.append(fig_power_heatmap(results, out_dir / f"{prefix}_fig2_heatmap.svg", stamp, png_dir=png_dir))
    n0 = 96 if ((results["n"] == 96) & (results["design"] == "two_arm")).any() else int(results["n"].min())
    if results[(results["design"] == "two_arm") & (results["n"] == n0) & (results["delta"] == 1.0)]["compliance"].nunique() > 1:
        written.append(fig_compliance(results, out_dir / f"{prefix}_fig3_compliance.svg", stamp, n=n0, png_dir=png_dir))
    written.append(fig_swings(panel, out_dir / f"{prefix}_fig4_swings.svg", stamp, png_dir=png_dir))
    written.append(fig_timeline(out_dir / f"{prefix}_fig5_timeline.svg", stamp, png_dir=png_dir))
    return written


def scenario_columns(results: pd.DataFrame) -> Sequence[str]:
    return [c for c in ("design", "n", "delta", "compliance", "strata", "heterogeneity", "mechanism", "outcome") if c in results.columns]
