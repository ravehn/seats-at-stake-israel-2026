"""Plotly figures for the report, drawn with the shared theme (`present/theme.py`).

Forms follow the data's job: the count progress is two stacked panels on one time axis (never a second
y-axis); "2026 against history" curves use emphasis (accent for 2026, greys for the earlier elections);
the fingerprint heatmaps use one hue, light → dark; the seats chart uses three validated categorical
slots. The data version is carried by the page, not repeated in every title.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from electionforensics.present.theme import (
    CATEGORICAL,
    TOKENS,
    history_colour,
    plotly_template,
    sequential_scale,
)
from electionforensics.taxonomy import party_label

LOCAL_TZ = ZoneInfo("Asia/Jerusalem")
TEMPLATE = plotly_template()

SECTOR_RU = {"jewish": "еврейские", "arab": "арабские", "haredi": "харедимные", "mixed": "смешанные города",
             "bedouin": "бедуинские", "unknown": "без классификации"}


def _local_naive(ts: str) -> datetime:
    """Snapshot stamp `YYYYMMDDTHHMMSSZ` → Israel local time, naive (Plotly keeps naive datetimes as given)."""
    return datetime.strptime(ts, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC).astimezone(LOCAL_TZ).replace(tzinfo=None)


def fingerprint_panels(hists: dict[int, pd.DataFrame], sector: str, leader_by_k: dict[int, str], title: str) -> go.Figure:
    ks = sorted(hists)
    current = max(ks)
    fig = make_subplots(rows=1, cols=len(ks), shared_yaxes=True, horizontal_spacing=0.025,
                        subplot_titles=[f"Кнессет {k} · лидер {leader_by_k.get(k, '')}" for k in ks])
    for i, k in enumerate(ks, start=1):
        h = hists[k]
        d = h[h["sector"] == sector]
        if d.empty:
            continue
        z = d[[c for c in d.columns if c.startswith("y")]].to_numpy().T  # rows = leader-share bins, cols = turnout bins
        z = np.log1p(z)
        fig.add_trace(go.Heatmap(z=z, x=d["turnout_bin"] * 100, y=np.arange(z.shape[0]) * 2.5, colorscale=sequential_scale(),
                                 zmin=0, showscale=False, xgap=0, ygap=0,
                                 hovertemplate="явка %{x:.0f} % · доля лидера %{y:.0f} % · log(1+n) = %{z:.2f}<extra></extra>"),
                      row=1, col=i)
        fig.update_xaxes(title_text="явка, %", range=[0, 100], dtick=25, showgrid=False, row=1, col=i)
        fig.update_yaxes(range=[0, 100], dtick=25, showgrid=False, row=1, col=i)
    fig.update_yaxes(title_text="доля лидера, %", row=1, col=1)
    fig.update_layout(template=TEMPLATE, meta={"title": title}, height=320, margin={"l": 56, "r": 12, "t": 36, "b": 48})
    for a, k in zip(fig.layout.annotations, ks, strict=False):
        a.font.size = 12
        a.font.color = TOKENS["accent_ink"] if k == current else TOKENS["ink_2"]
    return fig


def score_ecdf(scores: dict[int, pd.Series], title: str, xlabel: str) -> go.Figure:
    fig = go.Figure()
    current = max(scores) if scores else 0
    for k, s in sorted(scores.items()):
        x = np.sort(s.dropna().to_numpy())
        if x.size == 0:
            continue
        y = 1 - np.arange(1, x.size + 1) / x.size
        fig.add_trace(go.Scatter(x=x, y=np.maximum(y, 1 / x.size), mode="lines", name=f"Кнессет {k}",
                                 line={"width": 2.5 if k == current else 1.5, "color": history_colour(k, current)},
                                 hovertemplate="%{y:.2%} участков с оценкой ≥ %{x:.1f}<extra>Кнессет " + str(k) + "</extra>"))
    fig.update_yaxes(type="log", title_text="доля участков с оценкой ≥ x")
    fig.update_xaxes(title_text=xlabel)
    fig.update_layout(template=TEMPLATE, meta={"title": title}, height=380, hovermode="x unified",
                      legend={"y": 1.0, "yanchor": "bottom"}, margin={"l": 56, "r": 16, "t": 64, "b": 48})
    return fig


def count_progress(per_snapshot: list[dict[str, Any]], expected_stations: int) -> go.Figure:
    """Two panels on one time axis: boxes present in each snapshot; boxes whose numbers changed since the previous one."""
    ts = [_local_naive(p["ts"]) for p in per_snapshot]
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.12, row_heights=[0.66, 0.34],
                        subplot_titles=["Урн в файле", "Урн с ревизией опубликованных чисел с прошлого снимка"])
    fig.add_trace(go.Scatter(x=ts, y=[p["stations"] for p in per_snapshot], mode="lines+markers", name="урн в файле",
                             line={"color": TOKENS["accent"], "width": 2},
                             marker={"size": 7, "color": TOKENS["accent"], "line": {"width": 2, "color": TOKENS["card"]}},
                             hovertemplate="%{y:,} урн<extra></extra>"), row=1, col=1)
    fig.add_hline(y=expected_stations, line={"color": TOKENS["muted"], "width": 1}, row=1, col=1,
                  annotation_text=f"всего урн по реестру: {expected_stations:,}".replace(",", " "),
                  annotation_position="top left")
    fig.add_trace(go.Bar(x=ts, y=[p.get("changed_stations", 0) for p in per_snapshot], name="урн с ревизией",
                         marker={"color": CATEGORICAL[1], "line": {"width": 0}}, width=1000 * 60 * 18,
                         hovertemplate="%{y} урн с изменёнными числами<extra></extra>"), row=2, col=1)
    fig.update_xaxes(tickformat="%d.%m<br>%H:%M", title_text="время Израиля", row=2, col=1)
    fig.update_yaxes(title_text="урн", range=[0, expected_stations * 1.12], row=1, col=1)
    fig.update_yaxes(title_text="урн", rangemode="tozero", row=2, col=1)
    fig.update_layout(template=TEMPLATE, meta={"title": "Ход публикации подсчёта по снимкам"}, height=460, showlegend=False,
                      hovermode="x unified", margin={"l": 56, "r": 16, "t": 28, "b": 56})
    for a in fig.layout.annotations:
        a.font.size = 12
        a.font.color = TOKENS["ink_2"]
        a.xanchor = "left"
        a.x = 0
    return fig


def seats_bar(units: pd.DataFrame, knesset: int) -> go.Figure:
    d = units[units["votes"] > 0].sort_values("votes", ascending=False).head(16)
    labels = [party_label(knesset, p) for p in d["party"]]
    fig = go.Figure()
    for col, name, colour in (("seats", "мандаты по закону (Бадер‑Офер, порог, соглашения)", CATEGORICAL[0]),
                              ("proportional_seats", "строго пропорционально голосам", CATEGORICAL[1]),
                              ("seats_thr0", "без порога", CATEGORICAL[2])):
        fig.add_trace(go.Bar(y=labels, x=d[col], name=name, orientation="h", marker={"color": colour, "line": {"width": 0}},
                             hovertemplate="%{x} мандатов<extra>" + name + "</extra>"))
    fig.update_layout(template=TEMPLATE, barmode="group", bargap=0.28, bargroupgap=0.06, meta={"title": "Голоса в мандаты: закон против пропорции"},
                      legend={"y": 1.0, "yanchor": "bottom"}, height=max(360, 64 + 34 * len(d)), margin={"l": 8, "r": 16, "t": 76, "b": 48})
    fig.update_yaxes(autorange="reversed", showgrid=False, ticksuffix="  ")
    fig.update_xaxes(title_text="мандаты", dtick=5, rangemode="tozero")
    return fig
