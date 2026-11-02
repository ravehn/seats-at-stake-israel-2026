"""One locality's count as a finished publication: totals, a plan of the polling sites, every ballot
box, the count's dynamics, the city's history, the twin-city comparison and the public-register note —
all from the pinned snapshot that the battery and calibration were computed on.

Design pass (made before the layout, 2026-10-29)
-----------------------------------------------
Type: a system serif for headings and the lede (the issue's voice), a system sans for tables, labels
and figures; a 1.25 scale (13 / 16 / 20 / 25 / 31 px). No font is downloaded. Grid: prose on a 72-ch
measure, tables and figures on the full 1080-px column; side gutters 16 px on phones, 32 px wider.
Colour tokens live in `:root`: the issue's paper / ink / accent / muted set plus six categorical hues
for the lists, assigned in a fixed order by the city's 2026 rank and reused unchanged in every figure
(identity follows the list, never the chart). The six hues were checked with the dataviz palette
validator on the paper surface `#f6f3eb`: lightness band, chroma floor, CVD and normal-vision
separation pass; four hues sit below 3:1 contrast, so every value is also printed as a label and in a
table. Blocs never need a categorical colour: each bloc has its own small multiple in one ink.
Figures are inline SVG drawn here — no Plotly, no script, no network: thin marks, hairline solid
grid, one axis per chart, direct labels only at ends and extremes, a `<title>` per mark for hover.
Hebrew is isolated in `<bdi>`. Tables have sticky heads inside a scrolling wrap, numbers right-aligned
in tabular figures. Print: white paper, no sticky heads, figures and tables kept whole.
"""

from __future__ import annotations

import hashlib
import html
import json
import math
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

import pandas as pd
from jinja2 import Environment
from markupsafe import Markup

from electionforensics import __version__
from electionforensics.config import DATA_PROCESSED, REPORTS
from electionforensics.ingest.cec import ElectionData, load_election
from electionforensics.ingest.places import load_places, load_sites, place_label, places_path, read_boxes
from electionforensics.ingest.registry import ELECTIONS
from electionforensics.ingest.snapshots import load_series
from electionforensics.link.city_effect import BLOCS, CityEffect, blocs, city_effect
from electionforensics.present.report import _fmt_int, _ts_local
from electionforensics.provenance import pinned_election, require_calibrated
from electionforensics.taxonomy import parties, party_label

LOCAL_TZ = ZoneInfo("Asia/Jerusalem")
COUNT_COLS = ["eligible", "voters", "invalid", "valid"]
N_MAIN = 6
#: Russian names of the localities this report is written for; any other code falls back to the Hebrew name.
LOCALITY_RU: dict[int, str] = {240: "Йокнеам-Илит", 494: "Далият-эль-Кармель"}
BLOC_RU = {"coalition": "коалиционный блок", "change": "блок перемен", "arab": "арабские списки", "other": "прочие"}
#: Categorical hues for the lists (slots 1–6 of the dataviz reference palette, validated on the paper surface).
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"]
OTHER_HUE = "#7d8a8d"
INK, ACCENT, MUTED, GRID, BAND, PAPER = "#19333a", "#bb593c", "#52666a", "#e6e1d4", "#e2ddd0", "#f6f3eb"
#: One paragraph per locality about what the project's evidence base holds in its public register.
INCIDENT_NOTES: dict[int, str] = {
    240: ("В базе улик проекта по этому городу зарегистрировано одно сообщение: о массовом снятии предвыборной "
          "агитации оппозиционных списков в ночь на 27 октября 2026 года (дело № 1). Степень доказанности D — "
          "показания одного названного свидетеля; независимых подтверждений нет; согласие на публикацию "
          "подробностей не получено, поэтому ни содержание показаний, ни фотографии, ни имена здесь не приводятся. "
          "Данные подсчёта по городу не дают внутригородского контраста «зачищено / не зачищено», с которым можно "
          "было бы сопоставить результат: по показаниям, снятие затронуло весь город."),
}


# --------------------------------------------------------------------------- inputs

@dataclass
class LocalityInputs:
    """Everything the report reads, so a synthetic city can be rendered in tests without the processed tree."""

    knesset: int
    locality_code: int
    source: dict[str, Any]
    current: ElectionData
    history: dict[int, ElectionData]
    places: pd.DataFrame
    sites: pd.DataFrame
    calibration: pd.DataFrame
    candidates: pd.DataFrame
    revisions: pd.DataFrame
    arrival: pd.DataFrame
    series: list[tuple[str, pd.DataFrame]]
    boxes: pd.DataFrame | None = None
    places_prev: pd.DataFrame | None = None
    city: CityEffect | None = None
    city_error: str | None = None
    version: str = __version__


def _city_rows(st: pd.DataFrame, code: int) -> pd.DataFrame:
    mask = (st["locality_code"] == code).fillna(False)
    if "is_external" in st:
        mask &= ~st["is_external"].astype(bool)
    out = st[mask].copy()
    if "station_num" in out and "station_sub" in out:
        out = out.sort_values(["station_num", "station_sub"], kind="stable")
    return out.reset_index(drop=True)


def _read(path: Path, columns: list[str]) -> pd.DataFrame:
    return pd.read_parquet(path) if path.exists() else pd.DataFrame(columns=columns)


def load_inputs(locality_code: int, knesset: int = 26) -> LocalityInputs:
    """Pinned snapshot, past elections, both registers, calibration tiers and the snapshot series for one city."""
    source = require_calibrated(knesset)
    data = pinned_election(knesset, source)
    history: dict[int, ElectionData] = {}
    for k in sorted(ELECTIONS):
        if k >= knesset:
            continue
        try:
            history[k] = load_election(k)
        except FileNotFoundError:
            continue
    places = load_places(knesset)
    boxes = read_boxes(places_path(knesset))
    try:
        sites = load_sites(knesset)
    except FileNotFoundError:
        sites = pd.DataFrame(columns=["locality_code", "site", "place_name", "address", "place_type", "n_boxes", "eligible", "x", "y"])
    try:
        places_prev: pd.DataFrame | None = load_places(knesset - 1)
    except FileNotFoundError:
        places_prev = None
    d = DATA_PROCESSED / f"knesset{knesset}"
    calibration = _read(d / "CAL_stations.parquet", ["locality_code", "station", "tier"])
    candidates = _read(d / "T17.parquet", ["locality_code", "station", "fringe", "votes", "donor", "expected", "ratio", "score", "case"])
    revisions = _read(d / "T14.parquet", ["locality_code", "station", "field", "old", "new", "ts_from", "ts_to"])
    arrival = _read(d / "T14_arrival.parquet", ["locality_code", "station", "first_seen"])
    series: list[tuple[str, pd.DataFrame]] = []
    if knesset == 26:
        for snap in load_series("expb"):
            if snap.ts <= source["snapshot_ts"]:
                series.append((snap.ts, _city_rows(snap.table, locality_code)))
    city: CityEffect | None = None
    city_error: str | None = None
    try:
        city = city_effect(locality_code, knesset - 1, knesset, snapshot=data.source)
    except (ValueError, FileNotFoundError) as exc:
        city_error = str(exc)
    return LocalityInputs(knesset, locality_code, source, data, history, places, sites, calibration, candidates,
                          revisions, arrival, series, boxes=boxes, places_prev=places_prev, city=city, city_error=city_error)


# --------------------------------------------------------------------------- numbers

def _isum(s: pd.Series) -> int | None:
    v = s.sum(min_count=1) if len(s) else None
    return None if v is None or pd.isna(v) else int(v)


def _int(x: Any) -> int | None:
    return None if x is None or pd.isna(x) else int(x)


def _ratio(a: int | None, b: int | None) -> float | None:
    return a / b if a is not None and b else None


def _pct(x: float | None, digits: int = 1) -> str:
    return "—" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x * 100:.{digits}f} %"


def _pp(x: float | None, digits: int = 1) -> str:
    return "—" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x * 100:+.{digits}f} п.п."


def _local_dt(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC).astimezone(LOCAL_TZ)


def _year_label(k: int) -> str:
    e = ELECTIONS.get(k)
    if e is None:
        return str(k)
    same_year = [j for j, other in ELECTIONS.items() if other.date.year == e.date.year]
    return f"{e.date.year}-{e.date.month:02d}" if len(same_year) > 1 else str(e.date.year)


def _short_year(k: int) -> str:
    e = ELECTIONS.get(k)
    return f"\u2019{e.date.year % 100:02d}" if e else str(k)


def _base_name(label: str) -> str:
    """A list label without its bracketed leader, so 'НДИ (Либерман)' and 'НДИ' count as the same list."""
    return re.sub(r"\s*\([^()]*\)\s*$", "", label).strip()


def _as_int(x: Any) -> int:
    return int(x)


def _totals(rows: pd.DataFrame, parties_: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {c: _isum(rows[c]) if c in rows else None for c in COUNT_COLS}
    out["n"] = len(rows)
    out["turnout"] = _ratio(out["voters"], out["eligible"])
    out["invalid_share"] = _ratio(out["invalid"], out["voters"])
    valid = out["valid"] or 0
    votes = {p: int(rows[f"v_{p}"].sum()) for p in parties_ if f"v_{p}" in rows}
    out["votes"] = votes
    out["shares"] = {p: v / valid for p, v in votes.items()} if valid else {}
    return out


def _bloc_shares(shares: dict[str, float], knesset: int) -> dict[str, float] | None:
    bl = blocs(knesset)
    if not any(bl.values()):
        return None
    out = {b: sum(shares.get(p, 0.0) for p in bl[b]) for b in BLOCS}
    out["other"] = max(0.0, 1.0 - sum(out.values()))
    return out


def _delta(a: float | None, b: float | None) -> float | None:
    return None if a is None or b is None else a - b


# --------------------------------------------------------------------------- svg

def _esc(s: object) -> str:
    return html.escape(str(s), quote=True)


def _svg(markup: str) -> Markup:
    """Figures are built here from escaped values only (`_esc` on every label and title), so they may bypass autoescape."""
    return Markup(markup)  # noqa: S704


def _t(x: float, y: float, text: object, cls: str = "lbl", anchor: str = "start", extra: str = "") -> str:
    return f'<text x="{x:.1f}" y="{y:.1f}" class="{cls}" text-anchor="{anchor}"{extra}>{_esc(text)}</text>'


def _bar_path(x: float, y: float, w: float, h: float, r: float = 4.0) -> str:
    """A bar square at the baseline (left) and rounded at the data end (right)."""
    if w <= 0:
        return ""
    r = min(r, w, h / 2)
    return (f'<path d="M{x:.1f},{y:.1f} h{w - r:.1f} a{r},{r} 0 0 1 {r},{r} v{h - 2 * r:.1f} '
            f'a{r},{r} 0 0 1 -{r},{r} h-{w - r:.1f} z"')


def bars_svg(rows: list[dict[str, Any]], prev_label: str) -> str:
    """Horizontal bars of the current shares; a short ink tick marks the previous share under the same letters."""
    width, left, right, row_h, bar_h = 640, 230, 70, 28, 16
    plot_w = width - left - right
    vmax = max([r["share"] for r in rows] + [r["prev"] or 0 for r in rows] + [0.05])
    top = max(0.1, math.ceil(vmax * 20) / 20)
    height = 24 + row_h * len(rows) + 30
    sx = plot_w / top
    out = [f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" aria-label="Доли списков">']
    for v in [i / 10 for i in range(int(top * 10) + 1)]:
        if v > top + 1e-9:
            break
        x = left + v * sx
        out.append(f'<line x1="{x:.1f}" y1="16" x2="{x:.1f}" y2="{height - 28}" class="grid"/>')
        out.append(_t(x, height - 12, f"{v * 100:.0f} %", "axis", "middle"))
    for i, r in enumerate(rows):
        y = 24 + i * row_h
        w = r["share"] * sx
        out.append(f'<g><title>{_esc(r["title"])}</title>')
        out.append(_t(left - 10, y + bar_h - 4, r["label"], "lbl", "end"))
        out.append(_bar_path(left, y, w, bar_h) + f' fill="{r["hue"]}"/>')
        label_x = left + max(w, (r["prev"] or 0) * sx) + 7
        out.append(_t(label_x, y + bar_h - 4, r["value"], "val", "start"))
        if r["prev"] is not None:
            px = left + r["prev"] * sx
            out.append(f'<line x1="{px:.1f}" y1="{y - 3}" x2="{px:.1f}" y2="{y + bar_h + 3}" class="tick"><title>{_esc(prev_label)}: {_pct(r["prev"])}</title></line>')
        out.append("</g>")
    out.append(f'<line x1="{left}" y1="16" x2="{left}" y2="{height - 28}" class="baseline"/></svg>')
    return "".join(out)


def steps_svg(points: list[dict[str, Any]], expected: int | None, pinned_ts: str) -> str:
    """Boxes of the city present in each snapshot, as steps over local time; changed snapshots get a tick."""
    width, height, left, right, top, bottom = 640, 240, 56, 44, 20, 44
    if not points:
        return ""
    t0 = _local_dt(points[0]["ts"])
    xs = [(_local_dt(p["ts"]) - t0).total_seconds() / 3600 for p in points]
    span = max(xs[-1], 1.0)
    ymax: int = max([int(p["boxes"]) for p in points] + [expected or 0, 1])
    ytop: int = math.ceil(ymax / 5) * 5 if ymax > 5 else ymax + 1
    plot_w, plot_h = width - left - right, height - top - bottom

    def fx(hours: float) -> float:
        return left + hours / span * plot_w

    def fy(n: float) -> float:
        return top + plot_h - n / ytop * plot_h

    out = [f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" aria-label="Урны города в снимках">']
    step = 5 if ytop > 10 else 1
    for n in range(0, ytop + 1, step):
        out.append(f'<line x1="{left}" y1="{fy(n):.1f}" x2="{width - right}" y2="{fy(n):.1f}" class="grid"/>')
        out.append(_t(left - 8, fy(n) + 4, n, "axis", "end"))
    tick_h = 6 if span > 30 else 3 if span > 12 else 1
    hours = 0.0
    while hours <= span + 1e-9:
        x = fx(hours)
        out.append(f'<line x1="{x:.1f}" y1="{top + plot_h}" x2="{x:.1f}" y2="{top + plot_h + 4}" class="baseline"/>')
        stamp = t0 + pd.Timedelta(hours=hours)
        out.append(_t(x, height - 26, stamp.strftime("%d.%m"), "axis", "middle"))
        out.append(_t(x, height - 13, stamp.strftime("%H:%M"), "axis", "middle"))
        hours += tick_h
    if expected:
        out.append(f'<line x1="{left}" y1="{fy(expected):.1f}" x2="{width - right}" y2="{fy(expected):.1f}" class="ref"/>')
        out.append(_t(left + 6, fy(expected) - 5, f"по реестру {expected}", "axis", "start"))
    d = f"M{fx(xs[0]):.1f},{fy(points[0]['boxes']):.1f}"
    for i in range(1, len(points)):
        d += f" H{fx(xs[i]):.1f} V{fy(points[i]['boxes']):.1f}"
    out.append(f'<path d="{d}" class="line"/>')
    for p, x in zip(points, xs, strict=True):
        cls = "dot pin" if p["ts"] == pinned_ts else "dot"
        title = f"{_ts_local(p['ts'])}: урн {p['boxes']}, проголосовавших {_fmt_int(p['voters'])}"
        if p["changed_cells"]:
            title += f", изменённых ячеек {p['changed_cells']}"
            out.append(f'<line x1="{fx(x):.1f}" y1="{fy(p["boxes"]) - 14}" x2="{fx(x):.1f}" y2="{fy(p["boxes"]) - 7}" class="change"/>')
        out.append(f'<circle cx="{fx(x):.1f}" cy="{fy(p["boxes"]):.1f}" r="4" class="{cls}"><title>{_esc(title)}</title></circle>')
    out.append(_t(fx(xs[-1]) + 9, fy(points[-1]["boxes"]) + 4, f"{points[-1]['boxes']}", "val", "start"))
    out.append(f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_h}" class="baseline"/></svg>')
    return "".join(out)


def panel_svg(title: str, series: list[tuple[str, float | None, bool]], ymax: float = 0.8) -> str:
    """One small multiple: a single ink line over elections (gaps where a value is missing), shared y-scale.

    `series` holds (axis label, value, interim) per election, oldest first; every second label is drawn.
    """
    width, height, left, right, top, bottom = 240, 190, 36, 16, 26, 30
    plot_w, plot_h = width - left - right, height - top - bottom
    n = max(len(series) - 1, 1)

    def fx(i: int) -> float:
        return left + i / n * plot_w

    def fy(v: float) -> float:
        return top + plot_h - v / ymax * plot_h

    out = [f'<svg class="chart panel" viewBox="0 0 {width} {height}" role="img" aria-label="{_esc(title)}">']
    out.append(_t(left, 16, title, "ptitle", "start"))
    for v in [i / 4 * ymax for i in range(5)]:
        out.append(f'<line x1="{left}" y1="{fy(v):.1f}" x2="{width - right}" y2="{fy(v):.1f}" class="grid"/>')
        out.append(_t(left - 5, fy(v) + 4, f"{v * 100:.0f}", "axis", "end"))
    last = len(series) - 1
    labelled = {i for i in range(len(series)) if (last - i) % 2 == 0}
    for i, (lab, _v, _interim) in enumerate(series):
        if i in labelled:
            out.append(_t(fx(i), height - 10, lab, "axis", "middle"))
    seg: list[str] = []
    for i, (_lab, val, _interim) in enumerate(series):
        if val is None:
            if len(seg) > 1:
                out.append(f'<path d="M{" L".join(seg)}" class="line"/>')
            seg = []
            continue
        seg.append(f"{fx(i):.1f},{fy(val):.1f}")
    if len(seg) > 1:
        out.append(f'<path d="M{" L".join(seg)}" class="line"/>')
    present = [i for i in range(len(series)) if series[i][1] is not None]
    ends = {present[0], present[-1]} if present else set()
    for i, (lab, val, interim) in enumerate(series):
        if val is None:
            continue
        cls = "dot hollow" if interim else "dot"
        out.append(f'<circle cx="{fx(i):.1f}" cy="{fy(val):.1f}" r="4" class="{cls}"><title>{_esc(lab)}: {_pct(val)}</title></circle>')
        if i in ends:
            out.append(_t(fx(i), fy(val) - 9, f"{val * 100:.1f}", "val", "start" if i == min(ends) else "end"))
    out.append(f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_h}" class="baseline"/></svg>')
    return "".join(out)


def gap_svg(rows: list[dict[str, Any]]) -> str:
    """Per outcome: the twin cities' placebo band (light), zero (hairline) and the city's gap (accent dot)."""
    width, left, right, row_h = 640, 170, 70, 34
    plot_w = width - left - right
    height = 20 + row_h * len(rows) + 34
    raw: float = max([abs(float(r[k])) for r in rows for k in ("effect", "band_lo", "band_hi")] + [0.005]) * 1.1
    lim: float = next((c for c in (0.01, 0.02, 0.03, 0.04, 0.05, 0.075, 0.1, 0.15, 0.2, 0.3, 0.5, 1.0) if c >= raw), 1.0)

    def fx(v: float) -> float:
        return left + (v + lim) / (2 * lim) * plot_w

    out = [f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" aria-label="Разрыв с городами-двойниками">']
    ticks = [i * lim / 2 for i in range(-2, 3)]
    for v in ticks:
        out.append(f'<line x1="{fx(v):.1f}" y1="14" x2="{fx(v):.1f}" y2="{height - 30}" class="grid"/>')
        out.append(_t(fx(v), height - 14, f"{v * 100:+.1f}" if v else "0", "axis", "middle"))
    for i, r in enumerate(rows):
        y = 20 + i * row_h
        out.append(f'<g><title>{_esc(r["title"])}</title>')
        out.append(_t(left - 12, y + 20, r["label"], "lbl", "end"))
        out.append(f'<rect x="{fx(r["band_lo"]):.1f}" y="{y + 7}" width="{fx(r["band_hi"]) - fx(r["band_lo"]):.1f}" height="18" rx="4" class="band"/>')
        out.append(f'<circle cx="{fx(r["effect"]):.1f}" cy="{y + 16}" r="5" class="dot pin"/>')
        side = "start" if r["effect"] >= 0 else "end"
        out.append(_t(fx(r["effect"]) + (9 if r["effect"] >= 0 else -9), y + 20, _pp(r["effect"]), "val", side))
        out.append("</g>")
    out.append(f'<line x1="{fx(0):.1f}" y1="14" x2="{fx(0):.1f}" y2="{height - 30}" class="zero"/>')
    out.append(_t(width - right, height - 2, "разрыв, п.п.", "axis", "end"))
    out.append("</svg>")
    return "".join(out)


def map_svg(points: list[dict[str, Any]]) -> str:
    """Plan of the polling sites from ITM coordinates: north up, metres preserved, scale bar, numbered marks.

    Each point needs x, y (ITM metres), size (eligible voters), hue, label (number) and title.
    """
    width = 640
    if not points:
        return ""
    xs: list[float] = [float(p["x"]) for p in points]
    ys: list[float] = [float(p["y"]) for p in points]
    dx, dy = max(xs) - min(xs), max(ys) - min(ys)
    span: float = max(dx, dy, 200.0)
    pad: float = span * 0.14 + 50
    extent_x, extent_y = dx + 2 * pad, dy + 2 * pad
    scale = (width - 20) / extent_x
    height = max(300, min(720, int(extent_y * scale) + 40))
    scale = min(scale, (height - 40) / extent_y)
    ox = 10 + ((width - 20) - extent_x * scale) / 2
    oy = 10 + ((height - 40) - extent_y * scale) / 2
    x0, y1 = min(xs), max(ys)

    def fx(x: float) -> float:
        return ox + (x - x0 + pad) * scale

    def fy(y: float) -> float:
        return oy + (y1 + pad - y) * scale

    smax: float = max(float(p["size"]) for p in points) or 1.0
    out = [f'<svg class="chart map" viewBox="0 0 {width} {height}" role="img" aria-label="План точек голосования">',
           f'<rect x="0" y="0" width="{width}" height="{height}" class="mapbg"/>']
    marks = [(fx(float(p["x"])), fy(float(p["y"])), 9 + 13 * math.sqrt(float(p["size"]) / smax), p) for p in points]
    marks.sort(key=lambda m: -m[2])
    for i, (cx, cy, r, p) in enumerate(marks):
        out.append(f'<g><title>{_esc(p["title"])}</title>'
                   f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" fill="{p["hue"]}" class="site"/>')
        # a label hidden by an overlapping neighbour moves outside the circle, away from that neighbour
        near = [(qx, qy, qr) for j, (qx, qy, qr, _q) in enumerate(marks) if j != i and math.hypot(cx - qx, cy - qy) < r + qr + 2]
        if near:
            qx, qy, _qr = min(near, key=lambda m: math.hypot(cx - m[0], cy - m[1]))
            ddx, ddy = cx - qx, cy - qy
            norm = math.hypot(ddx, ddy) or 1.0
            ux, uy = ddx / norm, ddy / norm
            lx, ly = cx + ux * (r + 16), cy + uy * (r + 16)
            out.append(f'<line x1="{cx + ux * r:.1f}" y1="{cy + uy * r:.1f}" x2="{lx - ux * 7:.1f}" y2="{ly - uy * 7:.1f}" class="leader"/>')
            out.append(_t(lx, ly + 4.5, p["label"], "sitelbl out", "middle"))
        else:
            out.append(_t(cx, cy + 4.5, p["label"], "sitelbl", "middle"))
        out.append("</g>")
    bar_m = 500 if span >= 1500 else 200 if span >= 600 else 100
    bx, by = 24, height - 22
    out.append(f'<line x1="{bx}" y1="{by}" x2="{bx + bar_m * scale:.1f}" y2="{by}" class="scalebar"/>')
    out.append(f'<line x1="{bx}" y1="{by - 5}" x2="{bx}" y2="{by + 5}" class="scalebar"/>')
    out.append(f'<line x1="{bx + bar_m * scale:.1f}" y1="{by - 5}" x2="{bx + bar_m * scale:.1f}" y2="{by + 5}" class="scalebar"/>')
    out.append(_t(bx + bar_m * scale / 2, by - 9, f"{bar_m} м", "axis", "middle"))
    nx, ny = width - 30, 44
    out.append(f'<path d="M{nx},{ny - 22} L{nx + 7},{ny} L{nx},{ny - 6} L{nx - 7},{ny} z" class="north"/>')
    out.append(_t(nx, ny + 14, "С", "axis", "middle"))
    out.append("</svg>")
    return "".join(out)


# --------------------------------------------------------------------------- composition

@dataclass
class Report:
    ctx: dict[str, Any]
    tables: dict[str, pd.DataFrame] = field(default_factory=dict)


def compose(inp: LocalityInputs) -> Report:
    """Every number of the report, as template context and as the CSV tables it is published with."""
    k, code = inp.knesset, inp.locality_code
    cur = inp.current
    rows = _city_rows(cur.stations, code)
    if rows.empty:
        raise ValueError(f"locality {code} has no station rows in the pinned snapshot")
    name_he = str(rows["locality_name"].iloc[0])
    name_ru = LOCALITY_RU.get(code, name_he)
    hist_rows = {j: _city_rows(e.stations, code) for j, e in inp.history.items()}
    tot = _totals(rows, cur.parties)
    tot_hist = {j: _totals(r, inp.history[j].parties) for j, r in hist_rows.items() if len(r)}
    prev_k = k - 1 if (k - 1) in tot_hist else None
    prev2_k = k - 2 if (k - 2) in tot_hist else None
    bloc_now = _bloc_shares(tot["shares"], k) or {}
    bloc_hist = {j: _bloc_shares(t["shares"], j) for j, t in tot_hist.items()}
    prev_year = _year_label(prev_k) if prev_k else "—"
    prev2_year = _year_label(prev2_k) if prev2_k else "—"

    # lists -------------------------------------------------------------------------------------
    order = sorted(tot["votes"], key=lambda p: (-tot["votes"][p], p))
    main = [p for p in order if tot["votes"][p] > 0][:N_MAIN]
    hue = {p: SERIES[i] for i, p in enumerate(main)}
    known_now, known_prev = parties(k), (parties(prev_k) if prev_k else {})
    list_rows: list[dict[str, Any]] = []
    same_list = {p for p in known_now if p in known_prev and known_now[p].get("bloc") == known_prev[p].get("bloc")}
    for p in order:
        prev_share: float | None = None
        prev_name = ""
        if prev_k and p in known_now and p in known_prev and p in tot_hist[prev_k]["shares"]:
            old_name = str(known_prev[p].get("ru") or known_prev[p].get("he") or "")
            if p in same_list:
                prev_share = tot_hist[prev_k]["shares"][p]
                if _base_name(old_name) != _base_name(str(known_now[p].get("ru") or "")):
                    prev_name = old_name
            else:
                prev_name = f"другой список: {old_name} ({_pct(tot_hist[prev_k]['shares'][p])})"
        list_rows.append({"letters": p, "label_ru": party_label(k, p, "ru"), "label_he": party_label(k, p, "he"),
                          "votes": tot["votes"][p], "share": tot["shares"].get(p, 0.0), "prev_share": prev_share,
                          "delta": _delta(tot["shares"].get(p), prev_share), "prev_label": prev_name,
                          "hue": hue.get(p, OTHER_HUE), "main": p in main})
    shown = [r for r in list_rows if r["votes"] > 0][:10]
    rest = [r for r in list_rows if r not in shown]
    bar_rows = [{"label": r["label_ru"], "share": r["share"], "prev": r["prev_share"], "hue": r["hue"],
                 "value": _pct(r["share"]), "title": f"{r['label_ru']}: {_fmt_int(r['votes'])} голосов, {_pct(r['share'])}"
                 + (f"; {prev_year}: {_pct(r['prev_share'])}" if r["prev_share"] is not None else "")} for r in shown]
    if rest:
        share = sum(r["share"] for r in rest)
        bar_rows.append({"label": f"прочие ({len(rest)})", "share": share, "prev": None, "hue": OTHER_HUE,
                         "value": _pct(share), "title": f"прочие {len(rest)} списков: {_fmt_int(sum(r['votes'] for r in rest))} голосов"})

    # blocs and turnout ----------------------------------------------------------------------------
    bloc_rows: list[dict[str, Any]] = []
    for b in [*BLOCS, "other"]:
        now = bloc_now.get(b)
        p1 = (bloc_hist.get(prev_k) or {}).get(b) if prev_k else None
        p2 = (bloc_hist.get(prev2_k) or {}).get(b) if prev2_k else None
        bloc_rows.append({"key": b, "label": BLOC_RU[b], "now": now, "prev": p1, "prev2": p2,
                          "d_prev": _delta(now, p1), "d_prev2": _delta(now, p2)})
    turnout_prev = tot_hist[prev_k]["turnout"] if prev_k else None
    turnout_prev2 = tot_hist[prev2_k]["turnout"] if prev2_k else None
    turnout_row = {"key": "turnout", "label": "явка", "now": tot["turnout"], "prev": turnout_prev, "prev2": turnout_prev2,
                   "d_prev": _delta(tot["turnout"], turnout_prev), "d_prev2": _delta(tot["turnout"], turnout_prev2)}

    # register and coverage ----------------------------------------------------------------------
    reg = inp.places[inp.places["locality_code"] == code]
    expected = _isum(reg["n_boxes"]) if "n_boxes" in reg else None
    reg_boxes: list[str] = []
    if inp.boxes is not None and len(inp.boxes):
        reg_boxes = sorted(inp.boxes.loc[inp.boxes["locality_code"] == code, "station"].astype(str), key=_station_key)
    present = set(rows["station"].astype(str))
    missing = [s for s in reg_boxes if s not in present]
    extra = sorted(present - set(reg_boxes), key=_station_key) if reg_boxes else []
    place_by_num = {_as_int(r.station_num): r for r in reg.itertuples() if not pd.isna(r.station_num)}
    sites_reg = inp.sites[inp.sites["locality_code"] == code] if len(inp.sites) else inp.sites
    type_by_site = {_as_int(r.site): str(r.place_type) for r in sites_reg.itertuples() if not pd.isna(r.site)}

    def place_of(num: Any, site: Any) -> dict[str, str]:
        p = place_by_num.get(int(num)) if not pd.isna(num) else None
        site_i = int(site) if not pd.isna(site) else None
        return {"place_name": str(p.place_name) if p is not None else "", "address": str(p.address) if p is not None else "",
                "place_type": type_by_site.get(site_i, "") if site_i is not None else ""}

    prev_places: dict[int, str] = {}
    prev_nums_by_place: dict[str, list[int]] = {}
    if inp.places_prev is not None and len(inp.places_prev):
        reg_prev = inp.places_prev[inp.places_prev["locality_code"] == code]
        for r in reg_prev.itertuples():
            if not pd.isna(r.station_num):
                prev_places[_as_int(r.station_num)] = str(r.place_name)
                prev_nums_by_place.setdefault(str(r.place_name), []).append(_as_int(r.station_num))

    # stations ------------------------------------------------------------------------------------
    prev_rows = hist_rows.get(prev_k, pd.DataFrame()) if prev_k else pd.DataFrame()
    prev_parties = inp.history[prev_k].parties if prev_k else []
    prev_by_box: dict[str, pd.DataFrame] = {}
    prev_by_num: dict[int, pd.DataFrame] = {}
    if len(prev_rows):
        for box, grp in prev_rows.groupby(prev_rows["station"].astype(str)):
            prev_by_box[str(box)] = grp
        for num, grp in prev_rows.groupby("station_num"):
            prev_by_num[_as_int(num)] = grp
    bl_now, bl_prev = blocs(k), (blocs(prev_k) if prev_k else {b: [] for b in BLOCS})
    cal = inp.calibration[inp.calibration["locality_code"] == code] if len(inp.calibration) else inp.calibration
    tier_by = {str(r.station): str(r.tier) for r in cal.itertuples()} if len(cal) else {}
    arr = inp.arrival[inp.arrival["locality_code"] == code] if len(inp.arrival) else inp.arrival
    seen_by = {str(r.station): str(r.first_seen) for r in arr.itertuples()} if len(arr) else {}
    rev = inp.revisions[inp.revisions["locality_code"] == code].copy() if len(inp.revisions) else inp.revisions.copy()
    rev_by = rev.groupby(rev["station"].astype(str)).size().to_dict() if len(rev) else {}
    cand = inp.candidates[inp.candidates["locality_code"] == code].copy() if len(inp.candidates) else inp.candidates.copy()
    case_by = cand.groupby(cand["station"].astype(str))["case"].any().to_dict() if len(cand) else {}
    station_rows: list[dict[str, Any]] = []
    for row in rows.itertuples():
        st = str(row.station)
        num = None if pd.isna(row.station_num) else _as_int(row.station_num)
        place = place_of(row.station_num, row.site)
        elig, voters, invalid, valid = (_int(getattr(row, c)) for c in COUNT_COLS)
        votes = {p: int(getattr(row, f"v_{p}")) for p in cur.parties}
        shares = {p: v / valid for p, v in votes.items()} if valid else {}
        b_now = {b: sum(shares.get(p, 0.0) for p in bl_now[b]) for b in BLOCS}
        d_turn = d_coal = d_change = None
        prev_place = ""
        if prev_k and num is not None:
            if num in prev_places:
                prev_place = "" if prev_places[num] == place["place_name"] else f"в {prev_year}: {prev_places[num]}"
            else:
                there = prev_nums_by_place.get(place["place_name"], [])
                prev_place = (f"номера не было в {prev_year}; это место в {prev_year} — урна {', '.join(map(str, there))}"
                              if there else f"номера не было в {prev_year}")
        per_list_delta: dict[str, float | None] = {}
        match = "none"
        prev_grp = prev_by_box.get(st)
        if prev_grp is None and num is not None and num in prev_by_num:
            prev_grp, match = prev_by_num[num], "station"
        elif prev_grp is not None:
            match = "box"
        if prev_grp is not None:
            pt = _totals(prev_grp, prev_parties)
            p_shares = pt["shares"]
            d_turn = _delta(_ratio(voters, elig), pt["turnout"])
            d_coal = _delta(b_now["coalition"], sum(p_shares.get(p, 0.0) for p in bl_prev["coalition"]))
            d_change = _delta(b_now["change"], sum(p_shares.get(p, 0.0) for p in bl_prev["change"]))
            per_list_delta = {p: _delta(shares.get(p), p_shares.get(p)) for p in main if p in same_list and p in p_shares}
            if match == "station":
                boxes_prev = ", ".join(sorted(prev_grp["station"].astype(str), key=_station_key))
                prev_place = (prev_place + "; " if prev_place else "") + f"против суммы урн {boxes_prev} ({prev_year})"
        station_rows.append({"station": st, "site": _int(row.site), **place, "place": place_label(cast(dict[str, object], place)),
                             "eligible": elig, "voters": voters, "turnout": _ratio(voters, elig), "invalid": invalid,
                             "invalid_share": _ratio(invalid, voters), "valid": valid, "votes": votes, "shares": shares,
                             "other_share": max(0.0, 1.0 - sum(shares.get(p, 0.0) for p in main)) if valid else None,
                             "bloc_coalition": b_now["coalition"], "bloc_change": b_now["change"],
                             "d_turnout": d_turn, "d_coalition": d_coal, "d_change": d_change, "per_list_delta": per_list_delta,
                             "prev_match": match,
                             "prev_place": prev_place, "tier": tier_by.get(st, "—"), "first_seen": seen_by.get(st),
                             "first_seen_local": _ts_local(seen_by.get(st)) if seen_by.get(st) else "—",
                             "revisions": int(rev_by.get(st, 0)), "t17_case": bool(case_by.get(st, False))})
    tiers = pd.Series([r["tier"] for r in station_rows]).value_counts().to_dict()

    # sites and the plan ---------------------------------------------------------------------------
    site_rows: list[dict[str, Any]] = []
    for i, s in enumerate(sorted(sites_reg.itertuples(), key=lambda q: _as_int(q.site)), start=1):
        site_i = _as_int(s.site)
        sub = rows[(rows["site"] == site_i).fillna(False)]
        st_tot = _totals(sub, cur.parties)
        leader = max(st_tot["votes"], key=lambda p: (st_tot["votes"][p], p)) if st_tot["votes"] and st_tot["valid"] else None
        site_rows.append({"n": i, "site": site_i, "place_name": str(s.place_name), "address": str(s.address),
                          "place_type": str(s.place_type), "n_boxes_register": _int(s.n_boxes), "n_boxes_file": len(sub),
                          "eligible_register": _int(s.eligible), "eligible": st_tot["eligible"], "voters": st_tot["voters"],
                          "turnout": st_tot["turnout"], "leader": leader, "leader_label": party_label(k, leader, "ru") if leader else "—",
                          "leader_share": st_tot["shares"].get(leader) if leader else None, "hue": hue.get(leader or "", OTHER_HUE),
                          "x": None if pd.isna(s.x) else float(cast(Any, s.x)), "y": None if pd.isna(s.y) else float(cast(Any, s.y))})
    map_points = [{"x": s["x"], "y": s["y"], "size": float(s["eligible_register"] or s["eligible"] or 1), "hue": s["hue"],
                   "label": s["n"], "title": f"{s['n']}. {s['place_name']}, {s['address']}: урн {s['n_boxes_file']} из {s['n_boxes_register']}, "
                   f"явка {_pct(s['turnout'])}, лидер {s['leader_label']} {_pct(s['leader_share'])}"}
                  for s in site_rows if s["x"] is not None and s["y"] is not None]
    leaders = sorted({s["leader"] for s in site_rows if s["leader"]}, key=lambda p: order.index(p) if p in order else 99)

    # count dynamics --------------------------------------------------------------------------------
    progress: list[dict[str, Any]] = []
    prev_tab: pd.DataFrame | None = None
    for ts, tab in inp.series:
        changed = 0
        if prev_tab is not None and len(prev_tab) and len(tab):
            cols = [c for c in [*COUNT_COLS, *[f"v_{p}" for p in cur.parties]] if c in tab and c in prev_tab]
            before = prev_tab.set_index(prev_tab["station"].astype(str))[cols]
            after = tab.set_index(tab["station"].astype(str))[cols]
            common = before.index.intersection(after.index)
            changed = int((before.loc[common].astype("Int64") != after.loc[common].astype("Int64")).fillna(True).to_numpy().sum())
        progress.append({"ts": ts, "local": _ts_local(ts), "boxes": len(tab), "voters": _isum(tab["voters"]) or 0,
                         "changed_cells": changed})
        prev_tab = tab

    # history ---------------------------------------------------------------------------------------
    hist_elections = [*sorted(tot_hist), k]
    history_rows: list[dict[str, Any]] = []
    for j in hist_elections:
        tj = tot if j == k else tot_hist[j]
        bj = bloc_now if j == k else (bloc_hist.get(j) or {})
        history_rows.append({"knesset": j, "year": _year_label(j), "short": _short_year(j), "interim": j == k,
                             "eligible": tj["eligible"], "voters": tj["voters"], "turnout": tj["turnout"], "n_stations": tj["n"],
                             **{f"bloc_{bb}": bj.get(bb) for bb in [*BLOCS, "other"]}})
    panels = [("Явка", [(h["short"], h["turnout"], h["interim"]) for h in history_rows])]
    for b in BLOCS:
        panels.append((BLOC_RU[b].capitalize(), [(h["short"], h[f"bloc_{b}"], h["interim"]) for h in history_rows]))
    first_bloc_year = next((h["year"] for h in history_rows if h["bloc_coalition"] is not None), None)

    # twin cities ---------------------------------------------------------------------------------
    gap_rows: list[dict[str, Any]] = []
    donors: list[dict[str, Any]] = []
    city_params: dict[str, Any] = {}
    if inp.city is not None:
        labels = {"turnout": "явка", **{f"s_{b}": BLOC_RU[b] for b in BLOCS}}
        for key, oc in inp.city.outcomes.items():
            gap_rows.append({"key": key, "label": labels.get(key, key), **oc,
                             "title": f"{labels.get(key, key)}: город {_pp(oc['target_delta'])}, двойники {_pp(oc['counterfactual_delta'])}, "
                             f"разрыв {_pp(oc['effect'])}, плацебо-полоса {_pp(oc['band_lo'])} … {_pp(oc['band_hi'])}, p = {oc['p_placebo']:.2f}"})
        for idx, d in inp.city.donors.iterrows():
            donors.append({"locality_code": int(cast(Any, idx)), "name": str(d["locality_name_p"]), "weight": float(d["weight"]),
                           "n_stations": int(d["n_stations_p"]), "d_turnout": float(d["d_turnout"]),
                           "d_coalition": float(d["d_s_coalition"]), "d_change": float(d["d_s_change"])})
        city_params = dict(inp.city.params)

    as_of = _ts_local(inp.source.get("snapshot_ts"))
    ctx: dict[str, Any] = {
        "version": inp.version, "knesset": k, "code": code, "name_he": name_he, "name_ru": name_ru,
        "year": _year_label(k), "prev_year": prev_year, "prev2_year": prev2_year, "prev_k": prev_k, "prev2_k": prev2_k,
        "election_date": ELECTIONS[k].date.strftime("%d.%m.%Y") if k in ELECTIONS else "",
        "as_of": as_of, "snapshot_ts": inp.source.get("snapshot_ts", ""), "source_file": inp.source.get("file", ""),
        "sha256": inp.source.get("sha256", ""), "built": datetime.now(LOCAL_TZ).strftime("%Y-%m-%d %H:%M (%Z)"),
        "tot": tot, "turnout_row": turnout_row, "bloc_rows": bloc_rows, "list_rows": list_rows, "main": main, "hue": hue,
        "list_rows_shown": [r for r in list_rows if r["share"] >= 0.001],
        "list_rows_tail": [r for r in list_rows if 0 < r["share"] < 0.001],
        "other_total": max(0.0, 1.0 - sum(tot["shares"].get(p, 0.0) for p in main)) if tot["valid"] else None,
        "main_labels": [{"letters": p, "label": party_label(k, p, "ru"), "he": party_label(k, p, "he"), "hue": hue[p]} for p in main],
        "leader": list_rows[0] if list_rows else None,
        "n_boxes": len(rows), "expected": expected, "missing": missing, "extra": extra,
        "missing_places": [(m, place_of(_station_key(m)[0], None)["place_name"]) for m in missing],
        "bars": _svg(bars_svg(bar_rows, prev_year)),
        "sites": site_rows, "map": _svg(map_svg(map_points)), "n_sites": len(site_rows),
        "leaders": [{"letters": p, "label": party_label(k, p, "ru"), "hue": hue.get(p, OTHER_HUE)} for p in leaders],
        "stations": station_rows, "tiers": tiers, "all_none": bool(station_rows) and set(tiers) <= {"none", "—"},
        "n_flag": int(tiers.get("flag", 0)), "n_watch": int(tiers.get("watch", 0)),
        "candidates": cand.to_dict("records"), "revisions": rev.to_dict("records"),
        "progress": progress, "steps": _svg(steps_svg(progress, expected, str(inp.source.get("snapshot_ts", "")))),
        "n_snapshots": len(progress), "n_changed_snapshots": sum(1 for p in progress if p["changed_cells"]),
        "boxes_constant": bool(progress) and all(p["boxes"] == progress[0]["boxes"] for p in progress),
        "first_snapshot": progress[0]["local"] if progress else "",
        "history": history_rows, "panels": [_svg(panel_svg(t, s)) for t, s in panels], "first_bloc_year": first_bloc_year,
        "gap_rows": gap_rows, "gap": _svg(gap_svg(gap_rows) if gap_rows else ""), "donors": donors,
        "city_params": city_params, "city_error": inp.city_error,
        "incident": INCIDENT_NOTES.get(code), "pct": _pct, "pp": _pp, "fmt": _fmt_int,
    }
    tables = _tables(ctx, list_rows, bloc_rows, turnout_row, site_rows, station_rows, progress, history_rows, gap_rows,
                     donors, cand, rev, main, prev_year)
    return Report(ctx, tables)


def _station_key(s: str) -> tuple[int, int]:
    num, _, sub = str(s).partition(".")
    return (int(num) if num.isdigit() else 10**9, int(sub) if sub.isdigit() else 0)


def _tables(ctx: dict[str, Any], list_rows: list[dict[str, Any]], bloc_rows: list[dict[str, Any]], turnout_row: dict[str, Any],
            site_rows: list[dict[str, Any]], station_rows: list[dict[str, Any]], progress: list[dict[str, Any]],
            history_rows: list[dict[str, Any]], gap_rows: list[dict[str, Any]], donors: list[dict[str, Any]],
            cand: pd.DataFrame, rev: pd.DataFrame, main: list[str], prev_year: str) -> dict[str, pd.DataFrame]:
    lists = pd.DataFrame([{"letters": r["letters"], "label_ru": r["label_ru"], "label_he": r["label_he"], "votes": r["votes"],
                           "share": r["share"], f"share_{prev_year}": r["prev_share"], "delta_pp": None if r["delta"] is None else r["delta"] * 100,
                           f"label_{prev_year}": r["prev_label"]} for r in list_rows])
    blocs_t = pd.DataFrame([{"outcome": r["key"], "label_ru": r["label"],
                             f"k{ctx['knesset']}": r["now"], f"k{ctx['prev_k']}": r["prev"], f"k{ctx['prev2_k']}": r["prev2"],
                             f"delta_vs_k{ctx['prev_k']}_pp": None if r["d_prev"] is None else r["d_prev"] * 100,
                             f"delta_vs_k{ctx['prev2_k']}_pp": None if r["d_prev2"] is None else r["d_prev2"] * 100}
                            for r in [turnout_row, *bloc_rows]])
    sites = pd.DataFrame([{kk: v for kk, v in s.items() if kk != "hue"} for s in site_rows])
    stations = pd.DataFrame([{
        "station": s["station"], "site": s["site"], "place_name": s["place_name"], "address": s["address"], "place_type": s["place_type"],
        "eligible": s["eligible"], "voters": s["voters"], "turnout": s["turnout"], "invalid": s["invalid"], "valid": s["valid"],
        **{f"share_{p}": s["shares"].get(p) for p in main}, "share_other": s["other_share"],
        "bloc_coalition": s["bloc_coalition"], "bloc_change": s["bloc_change"],
        "d_turnout_pp": None if s["d_turnout"] is None else s["d_turnout"] * 100,
        "d_coalition_pp": None if s["d_coalition"] is None else s["d_coalition"] * 100,
        "d_change_pp": None if s["d_change"] is None else s["d_change"] * 100,
        **{f"d_share_{p}_pp": None if s["per_list_delta"].get(p) is None else cast(float, s["per_list_delta"][p]) * 100 for p in main},
        f"match_{prev_year}": s["prev_match"], f"place_{prev_year}_note": s["prev_place"], "tier": s["tier"], "first_seen": s["first_seen"], "revisions": s["revisions"],
        "t17_case": s["t17_case"], **{f"v_{p}": v for p, v in s["votes"].items()}} for s in station_rows])
    prog = pd.DataFrame(progress)
    hist = pd.DataFrame(history_rows)
    gaps = pd.DataFrame([{kk: v for kk, v in r.items() if kk != "title"} for r in gap_rows])
    return {"lists": lists, "blocs": blocs_t, "sites": sites, "stations": stations, "count_progress": prog, "history": hist,
            "city_effect": gaps, "city_donors": pd.DataFrame(donors), "revisions": rev.reset_index(drop=True),
            "candidates_t17": cand.reset_index(drop=True)}


# --------------------------------------------------------------------------- output

def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def write(report: Report, out: Path) -> Path:
    """index.html, tables/*.csv, provenance.json and MANIFEST.json (SHA-256 of every other file)."""
    tables_dir = out / "tables"
    tables_dir.mkdir(parents=True, exist_ok=True)
    for name, df in report.tables.items():
        df.to_csv(tables_dir / f"{name}.csv", index=False, encoding="utf-8-sig")
    ctx = dict(report.ctx)
    ctx["table_files"] = sorted(f"tables/{n}.csv" for n in report.tables)
    page = out / "index.html"
    page.write_text(Environment(autoescape=True).from_string(TEMPLATE).render(**ctx), encoding="utf-8")
    (out / "provenance.json").write_text(json.dumps({
        "version": ctx["version"], "knesset": ctx["knesset"], "locality_code": ctx["code"], "snapshot_ts": ctx["snapshot_ts"],
        "source_file": ctx["source_file"], "sha256": ctx["sha256"], "as_of": ctx["as_of"], "built": ctx["built"],
        "interim": True, "city_comparison": ctx["city_params"] or None}, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest = {p.relative_to(out).as_posix(): _sha256(p) for p in sorted(out.rglob("*")) if p.is_file() and p.name != "MANIFEST.json"}
    (out / "MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return page


def build(locality_code: int, knesset: int = 26, out_dir: Path | None = None) -> Path:
    """`ef locality-report <code>`: the report of one locality from the pinned snapshot."""
    inp = load_inputs(locality_code, knesset)
    out = out_dir or REPORTS / f"{_ts_local(inp.source['snapshot_ts'])[:10]}_loc{locality_code}_{inp.source['snapshot_ts']}"
    return write(compose(inp), out)


# --------------------------------------------------------------------------- template

TEMPLATE = r"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ name_ru }} — выборы в {{ knesset }}-й Кнессет: промежуточный подсчёт</title>
<style>
:root{
  color-scheme:light;
  --paper:#f6f3eb; --card:#fdfcf8; --ink:#19333a; --ink-2:#35494f; --muted:#52666a; --accent:#bb593c; --accent-ink:#9e4a31;
  --rule:#d8d2c3; --grid:#e6e1d4; --band:#e2ddd0; --wash:#ebe6d9;
  --s1:#2a78d6; --s2:#eb6834; --s3:#1baf7a; --s4:#eda100; --s5:#e87ba4; --s6:#008300; --s0:#7d8a8d;
  --serif:Georgia,"Times New Roman","David",serif; --sans:system-ui,"Segoe UI",Roboto,Arial,sans-serif;
  --gutter:16px; --measure:72ch;
  --f-xs:13px; --f-s:14px; --f-m:16px; --f-l:20px; --f-xl:25px; --f-xxl:31px;
}
*{box-sizing:border-box}
html{background:var(--paper)}
body{margin:0;background:var(--paper);color:var(--ink);font:var(--f-m)/1.5 var(--serif);-webkit-text-size-adjust:100%}
.page{max-width:1080px;margin:0 auto;padding:0 var(--gutter) 48px}
@media(min-width:760px){:root{--gutter:32px}}
h1,h2,h3{font-family:var(--serif);font-weight:600;line-height:1.15;margin:0}
h1{font-size:var(--f-xxl);letter-spacing:-.01em}
h2{font-size:var(--f-xl);margin:56px 0 12px;padding-top:16px;border-top:2px solid var(--ink)}
h3{font-size:var(--f-l);margin:28px 0 8px}
p{margin:0 0 12px;max-width:var(--measure)}
p.lede{font-size:18px;line-height:1.45}
a{color:var(--accent-ink)}
bdi{unicode-bidi:isolate}
.kicker{font:600 var(--f-xs)/1.3 var(--sans);letter-spacing:.08em;text-transform:uppercase;color:var(--accent-ink);margin:40px 0 10px}
.title-he{font-size:var(--f-xl);color:var(--ink-2);margin-top:4px}
.meta{font:var(--f-xs)/1.5 var(--sans);color:var(--muted);margin:18px 0 0;display:grid;grid-template-columns:max-content 1fr;gap:2px 14px;max-width:none}
.meta dt{margin:0;font-weight:600;color:var(--ink-2)}
.meta dd{margin:0}
.badge{display:inline-block;font:600 var(--f-xs)/1 var(--sans);letter-spacing:.04em;text-transform:uppercase;color:var(--accent-ink);border:1px solid var(--accent-ink);border-radius:3px;padding:4px 7px;margin-top:12px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:20px 0 28px}
.kpi{background:var(--card);border:1px solid var(--rule);border-radius:6px;padding:12px 14px;font-family:var(--sans)}
.kpi .l{font-size:var(--f-xs);color:var(--muted)}
.kpi .v{font-size:28px;font-weight:600;line-height:1.1;margin:4px 0 2px}
.kpi .d{font-size:var(--f-xs);color:var(--ink-2)}
.lede-grid{display:grid;grid-template-columns:3fr 2fr;gap:28px;align-items:start}
.lede-grid>*,figure,.fig-scroll,.table-wrap,.panels{min-width:0;max-width:100%}
@media(max-width:820px){.lede-grid{grid-template-columns:1fr}}
figure{margin:16px 0 24px}
figcaption,.caption{font:var(--f-xs)/1.45 var(--sans);color:var(--muted);margin-top:6px;max-width:var(--measure)}
.fig-scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
.fig-scroll svg{display:block;width:100%;height:auto;min-width:480px}
.fig-scroll svg.map{min-width:440px}
svg.chart text{font-family:var(--sans)}
svg.chart .lbl{font-size:13px;fill:var(--ink)}
svg.chart .val{font-size:12px;fill:var(--ink-2);font-variant-numeric:tabular-nums}
svg.chart .axis{font-size:11px;fill:var(--muted)}
svg.chart .ptitle{font-size:13px;font-weight:600;fill:var(--ink)}
svg.chart .grid{stroke:var(--grid);stroke-width:1}
svg.chart .baseline{stroke:#c3bfb1;stroke-width:1}
svg.chart .ref{stroke:var(--muted);stroke-width:1}
svg.chart .zero{stroke:var(--ink);stroke-width:1}
svg.chart .tick{stroke:var(--ink);stroke-width:2}
svg.chart .change{stroke:var(--accent);stroke-width:2}
svg.chart .line{fill:none;stroke:var(--ink);stroke-width:2;stroke-linejoin:round;stroke-linecap:round}
svg.chart .dot{fill:var(--ink);stroke:var(--paper);stroke-width:2}
svg.chart .dot.hollow{fill:var(--paper);stroke:var(--ink);stroke-width:2}
svg.chart .dot.pin{fill:var(--accent);stroke:var(--paper);stroke-width:2}
svg.chart .band{fill:var(--band)}
svg.chart .mapbg{fill:var(--card);stroke:var(--rule)}
svg.chart .site{fill-opacity:.88;stroke:var(--card);stroke-width:2}
svg.chart .sitelbl{font-size:12px;font-weight:700;fill:#fff;paint-order:stroke;stroke:rgba(0,0,0,.35);stroke-width:2px}
svg.chart .sitelbl.out{fill:var(--ink);stroke:var(--card);stroke-width:3px}
svg.chart .leader{stroke:var(--ink);stroke-width:1}
svg.chart .scalebar,svg.chart .north{stroke:var(--ink);stroke-width:1.5;fill:var(--ink)}
.legend{display:flex;flex-wrap:wrap;gap:6px 18px;font:var(--f-xs)/1.4 var(--sans);color:var(--ink-2);margin:4px 0 8px}
.legend .sw{display:inline-block;width:12px;height:12px;border-radius:3px;vertical-align:-1px;margin-right:6px}
.legend .tk{display:inline-block;width:2px;height:14px;background:var(--ink);vertical-align:-2px;margin:0 8px 0 4px}
.panels{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:8px 16px}
.panels svg{width:100%;height:auto}
.table-wrap{border:1px solid var(--rule);background:var(--card);border-radius:4px;margin:12px 0 6px}
@media(max-width:1119px){.table-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch}}
table{border-collapse:separate;border-spacing:0;font:var(--f-xs)/1.35 var(--sans);min-width:100%}
th,td{padding:6px 8px;border-bottom:1px solid var(--grid);vertical-align:top;text-align:left;white-space:nowrap}
th{position:sticky;top:0;background:var(--card);z-index:1;border-bottom:1px solid var(--ink);font-weight:600;color:var(--ink-2)}
th.n,td.n{text-align:right;font-variant-numeric:tabular-nums}
td.t{white-space:normal;min-width:18ch}
tbody tr:nth-child(even) td{background:rgba(25,51,58,.025)}
tr.sum td{font-weight:600;border-top:1px solid var(--ink)}
.sw{display:inline-block;width:10px;height:10px;border-radius:2px;vertical-align:-1px;margin-right:4px}
.compact td,.compact th{padding:5px 8px}
.note{background:var(--wash);border-left:4px solid var(--accent);padding:14px 18px;margin:20px 0;font-size:var(--f-s);line-height:1.5;max-width:none}
.note p{max-width:var(--measure)}
.muted{color:var(--muted)}
.small{font-size:var(--f-xs)}
footer{margin-top:56px;padding-top:16px;border-top:2px solid var(--ink);font:var(--f-xs)/1.5 var(--sans);color:var(--muted)}
footer p{max-width:none}
code{font:12px/1.4 ui-monospace,Consolas,monospace;background:var(--wash);padding:1px 4px;border-radius:3px;overflow-wrap:anywhere}
.links a{margin-right:14px}
@page{margin:16mm 14mm}
@media print{
  :root{--paper:#fff;--card:#fff;--wash:#f1efe9}
  body{font-size:11pt}
  .page{max-width:none;padding:0}
  h2{break-after:avoid;margin-top:28pt}
  figure,.kpi,.note,table{break-inside:avoid}
  .table-wrap{overflow:visible;border:0}
  table{width:100%;font-size:8pt;line-height:1.25}
  th,td{white-space:normal;padding:3px 4px}
  td.n{white-space:nowrap}
  td.t{min-width:0}
  th{position:static}
  .panels{grid-template-columns:repeat(2,1fr)}
  .lede-grid{grid-template-columns:1fr}
  .fig-scroll{overflow:visible}
  .fig-scroll svg{min-width:0}
  a{color:inherit;text-decoration:none}
  .no-print{display:none}
}
</style>
</head>
<body>
<main class="page">
<header>
<p class="kicker">ElectionForensics · отчёт по населённому пункту · выборы в {{ knesset }}-й Кнессет, {{ election_date }}</p>
<h1>{{ name_ru }}</h1>
<p class="title-he"><bdi lang="he">{{ name_he }}</bdi> <span class="muted small">· код ЦИК {{ code }}</span></p>
<span class="badge">промежуточный подсчёт</span>
<dl class="meta">
<dt>Снимок</dt><dd>{{ as_of }} — <code>{{ source_file }}</code></dd>
<dt>SHA-256</dt><dd><code>{{ sha256 }}</code></dd>
<dt>Система</dt><dd>ElectionForensics {{ version }}; собрано {{ built }}</dd>
<dt>Урны</dt><dd>в файле {{ n_boxes }} из {{ expected if expected is not none else '—' }} по реестру мест голосования{% if missing %}; ещё нет: {% for m, pl in missing_places %}{{ m }} — <bdi lang="he">{{ pl }}</bdi>{% if not loop.last %}; {% endif %}{% endfor %}{% endif %}{% if extra %}; вне реестра: {{ extra|join(', ') }}{% endif %}. Реестр отражает состояние на 25.10.{{ year[-4:] }}; урны, объединённые или перенесённые в день голосования, в нём не отражены.</dd>
</dl>
</header>

<h2 id="totals">Итог по городу</h2>
<p class="lede">{% if leader %}По {{ n_boxes }} урнам снимка в городе лидирует {{ leader.label_ru }} с {{ pct(leader.share) }} действительных голосов; явка {{ pct(tot.turnout) }}{% if turnout_row.d_prev is not none %} ({{ pp(turnout_row.d_prev) }} к {{ prev_year }}){% endif %}.{% endif %}
{% if prev_k %}Изменения считаются против тех же урн и списков прошлых выборов там, где сопоставление возможно; буквы бюллетеня закреплены за зарегистрированной партией, но состав и название списка могли измениться.{% endif %}</p>
<div class="kpis">
<div class="kpi"><div class="l">Явка</div><div class="v">{{ pct(tot.turnout) }}</div><div class="d">{% if turnout_row.d_prev is not none %}{{ pp(turnout_row.d_prev) }} к {{ prev_year }}{% else %}&nbsp;{% endif %}</div></div>
<div class="kpi"><div class="l">Избирателей в списках</div><div class="v">{{ fmt(tot.eligible) }}</div><div class="d">проголосовали {{ fmt(tot.voters) }}</div></div>
<div class="kpi"><div class="l">Недействительных</div><div class="v">{{ fmt(tot.invalid) }}</div><div class="d">{{ pct(tot.invalid_share, 2) }} от проголосовавших</div></div>
<div class="kpi"><div class="l">Урн в файле</div><div class="v">{{ n_boxes }}{% if expected %} <span class="muted" style="font-size:16px;font-weight:400">из {{ expected }}</span>{% endif %}</div><div class="d">{% if missing %}не хватает {{ missing|length }}{% else %}все урны реестра{% endif %}</div></div>
</div>
<div class="lede-grid">
<figure>
<div class="legend">{% for m in main_labels %}<span><span class="sw" style="background:{{ m.hue }}"></span>{{ m.label }}</span>{% endfor %}<span><span class="sw" style="background:var(--s0)"></span>остальные списки</span>{% if prev_k %}<span><span class="tk"></span>доля в {{ prev_year }} под теми же буквами</span>{% endif %}</div>
<div class="fig-scroll">{{ bars }}</div>
<figcaption>Доли действительных голосов в {{ year }} по {{ n_boxes }} урнам снимка. Показаны десять первых списков; остальные сложены в «прочие». Шесть главных списков сохраняют свои цвета во всех рисунках отчёта. Числа — в таблице ниже и в <a href="tables/lists.csv">tables/lists.csv</a>.</figcaption>
</figure>
<div>
<div class="table-wrap"><table class="compact">
<thead><tr><th>Блок / явка</th>{% if prev2_k %}<th class="n">{{ prev2_year }}</th>{% endif %}{% if prev_k %}<th class="n">{{ prev_year }}</th>{% endif %}<th class="n">{{ year }}</th>{% if prev_k %}<th class="n">Δ к {{ prev_year }}</th>{% endif %}{% if prev2_k %}<th class="n">Δ к {{ prev2_year }}</th>{% endif %}</tr></thead>
<tbody>{% for r in [turnout_row] + bloc_rows %}<tr><td>{{ r.label }}</td>{% if prev2_k %}<td class="n">{{ pct(r.prev2) }}</td>{% endif %}{% if prev_k %}<td class="n">{{ pct(r.prev) }}</td>{% endif %}<td class="n">{{ pct(r.now) }}</td>{% if prev_k %}<td class="n">{{ pp(r.d_prev) }}</td>{% endif %}{% if prev2_k %}<td class="n">{{ pp(r.d_prev2) }}</td>{% endif %}</tr>{% endfor %}</tbody>
</table></div>
<p class="caption">Блоки — по таксономии проекта (<code>taxonomy/blocs.yaml</code>): коалиционный блок, блок перемен (в {{ year }} включая <bdi lang="he">ביחד</bdi>, как в основном расчёте), арабские списки; остальное — «прочие». Явка — проголосовавшие к избирателям в списках. Сравнение по блокам не зависит от смены букв и названий списков.</p>
</div>
</div>
<div class="table-wrap"><table>
<thead><tr><th>Список</th><th>Буквы</th><th class="n">Голосов</th><th class="n">Доля {{ year }}</th>{% if prev_k %}<th class="n">Доля {{ prev_year }}</th><th class="n">Δ, п.п.</th><th>Тот же список в {{ prev_year }}</th>{% endif %}</tr></thead>
<tbody>{% for r in list_rows_shown %}<tr><td><span class="sw" style="background:{{ r.hue }}"></span>{{ r.label_ru }}</td><td><bdi lang="he">{{ r.letters }}</bdi></td><td class="n">{{ fmt(r.votes) }}</td><td class="n">{{ pct(r.share) }}</td>{% if prev_k %}<td class="n">{{ pct(r.prev_share) }}</td><td class="n">{{ pp(r.delta) }}</td><td class="t muted">{% if r.prev_share is none and r.prev_label %}{{ r.prev_label }}{% elif r.prev_share is none %}нет сопоставимого списка{% elif r.prev_label %}в {{ prev_year }}: {{ r.prev_label }}{% else %}да{% endif %}</td>{% endif %}</tr>{% endfor %}
{% if list_rows_tail %}<tr><td class="muted" colspan="2">ещё {{ list_rows_tail|length }} списков с долей ниже 0.1 % (в CSV)</td><td class="n muted">{{ fmt(list_rows_tail|map(attribute='votes')|sum) }}</td><td class="n muted">{{ pct(list_rows_tail|map(attribute='share')|sum) }}</td>{% if prev_k %}<td colspan="3"></td>{% endif %}</tr>{% endif %}
<tr class="sum"><td>Действительных голосов</td><td></td><td class="n">{{ fmt(tot.valid) }}</td><td class="n">100 %</td>{% if prev_k %}<td colspan="3"></td>{% endif %}</tr></tbody>
</table></div>
<p class="caption">Сопоставление с {{ prev_year }} — по буквам бюллетеня и только для списков, описанных в таксономии проекта в обоих выборах и отнесённых там к одному блоку; освободившиеся буквы ЦИК может передать другой партии, и тогда разность не считается. У новых списков сопоставимого результата нет. Списки без голосов в городе не показаны (они есть в CSV).</p>

<h2 id="map">Где голосовали</h2>
<p>{{ n_sites }} мест голосования по реестру ЦИК. Размер круга — число избирателей, приписанных к месту; цвет — лидирующий список по урнам снимка; номер — строка таблицы под планом. Схема построена по координатам реестра (сетка Израиля, ITM), север вверху, без картографической подложки.</p>
<div class="legend">{% for l in leaders %}<span><span class="sw" style="background:{{ l.hue }}"></span>лидирует {{ l.label }}</span>{% endfor %}</div>
<figure><div class="fig-scroll">{{ map }}</div></figure>
<div class="table-wrap"><table class="compact">
<thead><tr><th class="n">№</th><th>Место</th><th>Адрес</th><th>Тип</th><th class="n">Урн в файле / по реестру</th><th class="n">Избирателей</th><th class="n">Проголосовали</th><th class="n">Явка</th><th>Лидер</th><th class="n">Доля лидера</th></tr></thead>
<tbody>{% for s in sites %}<tr><td class="n">{{ s.n }}</td><td class="t"><bdi lang="he">{{ s.place_name }}</bdi></td><td class="t"><bdi lang="he">{{ s.address }}</bdi></td><td><bdi lang="he">{{ s.place_type }}</bdi></td><td class="n">{{ s.n_boxes_file }} / {{ s.n_boxes_register if s.n_boxes_register is not none else '—' }}</td><td class="n">{{ fmt(s.eligible) }}</td><td class="n">{{ fmt(s.voters) }}</td><td class="n">{{ pct(s.turnout) }}</td><td>{% if s.leader %}<span class="sw" style="background:{{ s.hue }}"></span>{% endif %}{{ s.leader_label }}</td><td class="n">{{ pct(s.leader_share) }}</td></tr>{% endfor %}</tbody>
</table></div>
<p class="caption">Избиратели и явка — по урнам, присутствующим в снимке; у места с недостающей урной они занижены. Таблица: <a href="tables/sites.csv">tables/sites.csv</a>.</p>

<h2 id="stations">По урнам</h2>
<p>{% if all_none %}Калибровка батареи тестов (<code>CAL_stations.parquet</code>): ни одна урна города не попала во флаги или в разведочный список — у всех {{ n_boxes }} урн статус <code>none</code>.{% else %}Статус калибровки по урнам: флагов {{ n_flag }}, в разведочном списке {{ n_watch }}, без замечаний {{ tiers.get('none', 0) }}.{% endif %}
{% if candidates %}Строк теста T17 (перенос одной клетки) по городу: {{ candidates|length }}.{% else %}Строк теста T17 (перенос одной клетки) по городу нет.{% endif %}
{% if revisions %}Ревизий чисел между снимками по урнам города: {{ revisions|length }}.{% else %}Ревизий чисел между снимками по урнам города не было: каждая урна с момента появления в файле несёт те же числа.{% endif %}</p>
<h3>Итоги и доли главных списков</h3>
<div class="table-wrap"><table>
<thead><tr><th>Урна</th><th>Место</th><th class="n">Избирателей</th><th class="n">Проголосовали</th><th class="n">Явка</th><th class="n">Недейств.</th>{% for m in main_labels %}<th class="n"><span class="sw" style="background:{{ m.hue }}"></span>{{ m.label }}</th>{% endfor %}<th class="n">Прочие</th></tr></thead>
<tbody>{% for s in stations %}<tr><td>{{ s.station }}</td><td class="t"><bdi lang="he">{{ s.place_name }}</bdi><br><span class="muted"><bdi lang="he">{{ s.address }}</bdi></span></td><td class="n">{{ fmt(s.eligible) }}</td><td class="n">{{ fmt(s.voters) }}</td><td class="n">{{ pct(s.turnout) }}</td><td class="n">{{ fmt(s.invalid) }}</td>{% for m in main %}<td class="n">{{ pct(s.shares.get(m)) }}</td>{% endfor %}<td class="n">{{ pct(s.other_share) }}</td></tr>{% endfor %}
<tr class="sum"><td>Город</td><td></td><td class="n">{{ fmt(tot.eligible) }}</td><td class="n">{{ fmt(tot.voters) }}</td><td class="n">{{ pct(tot.turnout) }}</td><td class="n">{{ fmt(tot.invalid) }}</td>{% for m in main %}<td class="n">{{ pct(tot.shares.get(m)) }}</td>{% endfor %}<td class="n">{{ pct(other_total) }}</td></tr></tbody>
</table></div>
<h3>Против {{ prev_year }} и статус проверки</h3>
<div class="table-wrap"><table>
<thead><tr><th>Урна</th><th>Место</th><th>То же место в {{ prev_year }}?</th><th class="n">Δ явки, п.п.</th><th class="n">Δ коалиц. блока, п.п.</th><th class="n">Δ блока перемен, п.п.</th><th>Калибровка</th><th>T17</th><th class="n">Ревизий</th><th>Впервые в файле</th></tr></thead>
<tbody>{% for s in stations %}<tr><td>{{ s.station }}</td><td class="t"><bdi lang="he">{{ s.place_name }}</bdi></td><td class="t muted">{% if s.prev_place %}<bdi>{{ s.prev_place }}</bdi>{% elif s.prev_match == 'none' %}урны с этим номером нет в файле {{ prev_year }}{% else %}да{% endif %}</td><td class="n">{{ pp(s.d_turnout) }}</td><td class="n">{{ pp(s.d_coalition) }}</td><td class="n">{{ pp(s.d_change) }}</td><td><code>{{ s.tier }}</code></td><td>{% if s.t17_case %}случай{% else %}—{% endif %}</td><td class="n">{{ s.revisions }}</td><td>{{ s.first_seen_local }}</td></tr>{% endfor %}</tbody>
</table></div>
<p class="caption">Сравнение — с урной под тем же номером в файле {{ prev_year }}; если такой урны нет, но есть урны того же участка (например, 3 против 3.1 и 3.2), сравнение идёт с их суммой и об этом сказано в строке. Совпадение номера не гарантирует те же границы участка, а реестр {{ prev_year }} показывает, где номер стоял в другом здании. Δ блоков — разность долей действительных голосов. «Впервые в файле» — первый снимок проекта, в котором урна появилась (время Израиля). Все расчётные поля, в том числе Δ по спискам и голоса всех списков: <a href="tables/stations.csv">tables/stations.csv</a>.</p>
{% if candidates %}<h3>Строки T17 по городу</h3>
<div class="table-wrap"><table class="compact"><thead><tr><th>Урна</th><th>Малый список</th><th class="n">Голосов</th><th>Возможный адресат</th><th class="n">Ожидание</th><th class="n">Отношение</th><th class="n">Балл</th><th>Случай</th></tr></thead>
<tbody>{% for c in candidates %}<tr><td>{{ c.station }}</td><td><bdi lang="he">{{ c.fringe }}</bdi></td><td class="n">{{ c.votes }}</td><td><bdi lang="he">{{ c.donor }}</bdi></td><td class="n">{{ '%.1f'|format(c.expected) }}</td><td class="n">{{ '%.2f'|format(c.ratio) }}</td><td class="n">{{ '%.1f'|format(c.score) }}</td><td>{{ 'да' if c.case else 'нет' }}</td></tr>{% endfor %}</tbody></table></div>{% endif %}
{% if revisions %}<h3>Ревизии между снимками</h3>
<div class="table-wrap"><table class="compact"><thead><tr><th>Урна</th><th>Поле</th><th class="n">Было</th><th class="n">Стало</th><th>Снимок до</th><th>Снимок после</th></tr></thead>
<tbody>{% for r in revisions %}<tr><td>{{ r.station }}</td><td><code>{{ r.field }}</code></td><td class="n">{{ r.old }}</td><td class="n">{{ r.new }}</td><td>{{ r.ts_from }}</td><td>{{ r.ts_to }}</td></tr>{% endfor %}</tbody></table></div>{% endif %}

<h2 id="progress">Как шёл подсчёт</h2>
{% if progress %}<p>Проект сохраняет файл ЦИК каждые 30 минут; серия начинается с {{ first_snapshot }}. {% if boxes_constant %}Все {{ progress[0].boxes }} урны города были в файле уже в первом снимке, и в {{ n_snapshots }} снимках до закреплённого включительно их число не менялось{% else %}В {{ n_snapshots }} снимках до закреплённого включительно урны города появлялись ступенями{% endif %}; {% if n_changed_snapshots %}в {{ n_changed_snapshots }} снимках менялись уже опубликованные числа{% else %}уже опубликованные числа ни разу не менялись{% endif %}.</p>
<figure><div class="fig-scroll">{{ steps }}</div>
<figcaption>Урны города в каждом снимке файла <code>expb</code>, время Израиля. Акцентная точка — закреплённый снимок отчёта; штрих над точкой — снимок, в котором изменились числа уже присутствовавших урн. Таблица: <a href="tables/count_progress.csv">tables/count_progress.csv</a>.</figcaption></figure>
<div class="table-wrap"><table class="compact"><thead><tr><th>Снимок</th><th class="n">Урн города</th><th class="n">Проголосовавших</th><th class="n">Изменённых ячеек</th></tr></thead>
<tbody>{% for p in progress %}<tr><td>{{ p.local }}</td><td class="n">{{ p.boxes }}</td><td class="n">{{ fmt(p.voters) }}</td><td class="n">{{ p.changed_cells }}</td></tr>{% endfor %}</tbody></table></div>
{% else %}<p>Серии снимков для этих выборов нет.</p>{% endif %}

<h2 id="history">Город на прошлых выборах</h2>
<p>Явка и доли блоков по урнам города в файлах ЦИК с {{ history[0].year }} года; одна шкала 0–80 % на всех панелях. Последняя точка — промежуточный итог {{ year }} (полый маркер).{% if first_bloc_year %} Блоки определены в таксономии проекта начиная с выборов {{ first_bloc_year }} года, раньше — только явка.{% endif %} В файле 2006 года нет числа избирателей, поэтому явки за тот год нет.</p>
<figure><div class="panels">{% for p in panels %}{{ p }}{% endfor %}</div>
<figcaption>Подписи оси — последние две цифры года через одну точку (две точки 2019 — апрель и сентябрь); все значения — в таблице и в <a href="tables/history.csv">tables/history.csv</a>.</figcaption></figure>
<div class="table-wrap"><table class="compact"><thead><tr><th>Выборы</th><th class="n">Урн</th><th class="n">Избирателей</th><th class="n">Проголосовали</th><th class="n">Явка</th><th class="n">Коалиционный блок</th><th class="n">Блок перемен</th><th class="n">Арабские списки</th><th class="n">Прочие</th></tr></thead>
<tbody>{% for h in history %}<tr{% if h.interim %} class="sum"{% endif %}><td>{{ h.knesset }}-й, {{ h.year }}{% if h.interim %} (промежуточно){% endif %}</td><td class="n">{{ h.n_stations }}</td><td class="n">{{ fmt(h.eligible) }}</td><td class="n">{{ fmt(h.voters) }}</td><td class="n">{{ pct(h.turnout) }}</td><td class="n">{{ pct(h.bloc_coalition) }}</td><td class="n">{{ pct(h.bloc_change) }}</td><td class="n">{{ pct(h.bloc_arab) }}</td><td class="n">{{ pct(h.bloc_other) }}</td></tr>{% endfor %}</tbody></table></div>

<h2 id="twins">Сравнение с похожими городами</h2>
{% if gap_rows %}<p>Изменение города между {{ prev_year }} и {{ year }} сопоставлено с взвешенным изменением {{ donors|length }} городов-двойников того же сектора, подобранных по явке, долям блоков и размеру в {{ prev_year }} (пул — {{ city_params.donor_pool }} населённых пунктов с полным подсчётом, покрытие реестра у города {{ '%.1f'|format(city_params.target_completeness * 100) }} %). Разрыв — описательная величина: насколько город отклонился от двойников. Полоса — центральные 95 % таких же разрывов у самих двойников, когда каждого из них по очереди считали «целью» (плацебо). Это справочное распределение, а не доверительный интервал эффекта какого-либо события; общие причины, ненаблюдаемые изменения и несходство трендов остаются возможными.</p>
<figure><div class="fig-scroll">{{ gap }}</div>
<figcaption>Точка — разрыв города с двойниками, полоса — плацебо-разрывы двойников (2,5–97,5 %). Разрыв внутри полосы означает, что такие отклонения обычны и среди похожих городов.</figcaption></figure>
<div class="table-wrap"><table class="compact"><thead><tr><th>Показатель</th><th class="n">Город, Δ</th><th class="n">Двойники, Δ</th><th class="n">Разрыв</th><th class="n">Плацебо-полоса</th><th class="n">p (плацебо)</th></tr></thead>
<tbody>{% for r in gap_rows %}<tr><td>{{ r.label }}</td><td class="n">{{ pp(r.target_delta) }}</td><td class="n">{{ pp(r.counterfactual_delta) }}</td><td class="n">{{ pp(r.effect) }}</td><td class="n">{{ pp(r.band_lo) }} … {{ pp(r.band_hi) }}</td><td class="n">{{ '%.2f'|format(r.p_placebo) }}</td></tr>{% endfor %}</tbody></table></div>
<p class="caption">Двойники и веса: {% for d in donors %}<bdi lang="he">{{ d.name }}</bdi> ({{ '%.2f'|format(d.weight) }}){% if not loop.last %}, {% endif %}{% endfor %}. Таблицы: <a href="tables/city_effect.csv">tables/city_effect.csv</a>, <a href="tables/city_donors.csv">tables/city_donors.csv</a>. Метод — <code>link/city_effect.py</code>, решение EF-007: описательный разрыв, не причинная оценка.</p>
{% else %}<p>Сравнение с похожими городами для этого снимка не рассчитано{% if city_error %}: {{ city_error }}{% endif %}.</p>{% endif %}

{% if incident %}<h2 id="incidents">Публичный регистр инцидентов</h2>
<div class="note"><p>{{ incident }}</p></div>{% endif %}

<footer>
<p><strong>Воспроизведение.</strong> <code>uv run ef locality-report {{ code }}</code> из корня проекта собирает этот отчёт из закреплённого снимка; батарея и калибровка должны быть рассчитаны на том же снимке (<code>ef build</code>).</p>
<p class="links"><strong>Таблицы отчёта:</strong> {% for f in table_files %}<a href="{{ f }}">{{ f }}</a> {% endfor %}<a href="provenance.json">provenance.json</a> <a href="MANIFEST.json">MANIFEST.json</a></p>
<p><strong>Версия данных.</strong> Снимок {{ as_of }}, файл <code>{{ source_file }}</code>, SHA-256 <code>{{ sha256 }}</code>; ElectionForensics {{ version }}. Хэши всех файлов отчёта — в <code>MANIFEST.json</code>.</p>
<p><strong>Оговорки.</strong> Подсчёт промежуточный: до публикации итогов в «Решумот» числа могут измениться, а недостающие урны — появиться. Статистическая нерегулярность — повод проверить протокол урны, а не вывод о нарушении; у каждой из них есть обычные объяснения — другой состав избирателей за той же дверью, объединённые урны, описка в одной клетке. Автоматический текст не делает выводов о чьих-либо намерениях.</p>
</footer>
</main>
</body>
</html>
"""
