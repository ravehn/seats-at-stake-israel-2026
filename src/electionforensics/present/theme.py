"""The visual system shared by every HTML output of the project: tokens, base stylesheet, Plotly template.

Design decisions (29.10.2026, after the dataviz method):

* One palette for the whole family of documents — the reader issue (`publication.py`), the technical
  report (`report.py`) and the locality reports: paper `#f6f3eb`, ink `#19333a`, brick accent `#bb593c`.
  Charts sit on a slightly lighter card so that marks keep their contrast.
* Colour does one job at a time. Identity (categorical) uses validated slots in a fixed order
  (`CATEGORICAL`; worst adjacent CVD ΔE 9.6, normal-vision ΔE 24.0 on the card surface). Magnitude uses
  one hue, light → dark (`SEQUENTIAL`). "This year against history" is *emphasis*: 2026 in the accent,
  the previous elections in greys ordered by age (`HISTORY_GREYS`). Tier status (flag / watchlist) uses
  reserved status colours and always ships with a word, never colour alone.
* Text never wears a series colour; numbers in tables are right-aligned tabular figures; Hebrew is
  isolated with `<bdi>` so that mixed-direction cells do not reorder.
* No fonts are downloaded and no script is fetched: every output must open from a folder offline.

Contrast (WCAG, computed in `tests/test_theme.py`): ink on paper 12.0:1, secondary ink 5.5:1, accent as
text 4.1:1 (large/bold only; the darker `accent_ink` 5.5:1 for running text), status inks ≥ 5.3:1.
"""

from __future__ import annotations

import html
from typing import Any

import plotly.graph_objects as go

#: Surface and ink tokens, light mode. The HTML outputs are documents meant for paper and screen alike,
#: so there is no dark mode: one calibrated appearance, the same on every reader's machine.
TOKENS: dict[str, str] = {
    "paper": "#f6f3eb",       # page background
    "card": "#fffcf5",        # chart surface, tiles, callouts
    "ink": "#19333a",         # primary text
    "ink_2": "#52666a",       # secondary text (captions, table heads)
    "muted": "#6b7a7d",       # tertiary (axis labels, footnotes) — 4.0:1 on paper
    "rule": "#d8d3c6",        # hairlines
    "grid": "#e8e3d6",        # chart gridlines, one step off the card
    "accent": "#bb593c",      # the brand brick: 2026 series, emphasis, kickers
    "accent_ink": "#a84b30",  # accent as running text
    "accent_bg": "#f6e4dd",   # accent wash
    "flag": "#d03b3b",        # status: flag (critical)
    "flag_ink": "#8f2a28",
    "flag_bg": "#f7dedb",
    "watch": "#c98500",       # status: watchlist (warning)
    "watch_ink": "#7a4e00",
    "watch_bg": "#f8e9c9",
    "ok_ink": "#2f6b3a",      # status: within the historical band
    "ok_bg": "#e1efe0",
    "info_bg": "#e4ecf1",     # neutral statement
}

#: Categorical slots in fixed order (validated on the card surface, adjacent pairs, light mode):
#: brick, blue, aqua, violet, orange. Never cycled: a sixth series folds into "other" or a facet.
CATEGORICAL: list[str] = ["#bb593c", "#2a78d6", "#1baf7a", "#6250d6", "#eb6834"]

#: One-hue ramp for magnitude (blue, light → dark), anchored to the card surface at zero.
SEQUENTIAL: list[str] = ["#fffcf5", "#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]

#: Greys for the previous elections in an emphasis chart, most recent first (darkest = most recent).
HISTORY_GREYS: list[str] = ["#5f6f72", "#849395", "#a3aeb0", "#c0c8c9", "#d6dcdc"]

FONT_SANS = 'system-ui, "Segoe UI", Roboto, "Noto Sans Hebrew", Arial, sans-serif'
FONT_SERIF = 'Georgia, "Times New Roman", "Noto Serif Hebrew", serif'
FONT_MONO = 'ui-monospace, Consolas, "Cascadia Mono", monospace'


def history_colour(knesset: int, current: int) -> str:
    """Emphasis rule: the current election wears the accent, earlier ones a grey by distance in time."""
    if knesset == current:
        return TOKENS["accent"]
    i = min(max(current - knesset - 1, 0), len(HISTORY_GREYS) - 1)
    return HISTORY_GREYS[i]


def sequential_scale() -> list[list[Any]]:
    """Plotly colorscale for the one-hue ramp."""
    n = len(SEQUENTIAL) - 1
    return [[i / n, c] for i, c in enumerate(SEQUENTIAL)]


def plotly_template() -> go.layout.Template:
    """Chart chrome: recessive solid hairline grid, left-aligned serif title, legend above the plot."""
    axis = {
        "gridcolor": TOKENS["grid"], "gridwidth": 1, "zerolinecolor": TOKENS["rule"], "zerolinewidth": 1,
        "linecolor": TOKENS["rule"], "linewidth": 1, "ticks": "outside", "tickcolor": TOKENS["rule"], "ticklen": 4,
        "tickfont": {"color": TOKENS["ink_2"], "size": 12}, "title": {"font": {"color": TOKENS["ink_2"], "size": 12}},
        "showline": True, "mirror": False, "automargin": True,
    }
    return go.layout.Template(layout=go.Layout(
        font={"family": FONT_SANS, "size": 13, "color": TOKENS["ink"]},
        paper_bgcolor=TOKENS["card"], plot_bgcolor=TOKENS["card"],
        title={"font": {"family": FONT_SERIF, "size": 17, "color": TOKENS["ink"]}, "x": 0, "xanchor": "left", "pad": {"l": 0}},
        colorway=CATEGORICAL,
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "xanchor": "left", "x": 0,
                "font": {"size": 12, "color": TOKENS["ink_2"]}, "bgcolor": "rgba(0,0,0,0)"},
        hoverlabel={"bgcolor": TOKENS["ink"], "bordercolor": TOKENS["ink"],
                    "font": {"family": FONT_SANS, "size": 12, "color": TOKENS["paper"]}},
        xaxis=axis, yaxis=axis, margin={"l": 56, "r": 16, "t": 64, "b": 48},
        annotationdefaults={"font": {"color": TOKENS["ink_2"], "size": 12}, "arrowcolor": TOKENS["muted"]},
    ))


PLOTLY_CONFIG: dict[str, Any] = {"displayModeBar": False, "responsive": True, "displaylogo": False}


def figure_html(fig: go.Figure) -> str:
    """Embed a figure without its own Plotly bundle (the page carries one copy) and without the toolbar.

    The chart title lives in HTML (`fig.layout.meta["title"]`, set by the figure builders), not inside the
    Plotly canvas: there it cannot collide with a wrapped legend on a narrow screen and it reflows with the text.
    """
    meta = fig.layout.meta if isinstance(fig.layout.meta, dict) else {}
    title = str(meta.get("title", "")) if meta else ""
    head = f'<h4 class="chart-title">{html.escape(title)}</h4>' if title else ""
    return head + str(fig.to_html(full_html=False, include_plotlyjs=False, config=PLOTLY_CONFIG))


def _css_tokens() -> str:
    lines = [f"  --{k.replace('_', '-')}: {v};" for k, v in TOKENS.items()]
    lines += [f"  --sans: {FONT_SANS};", f"  --serif: {FONT_SERIF};", f"  --mono: {FONT_MONO};"]
    return ":root{\n" + "\n".join(lines) + "\n}"


#: Base stylesheet for a long document: masthead, side navigation, sections, tiles, tables, figures,
#: chips, notes, print. Written against the tokens above; nothing else carries a raw colour.
BASE_CSS = r"""
*,*::before,*::after{box-sizing:border-box}
html{scroll-behavior:smooth;-webkit-text-size-adjust:100%}
body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.55 var(--sans);font-variant-numeric:lining-nums}
a{color:var(--accent-ink);text-decoration-thickness:1px;text-underline-offset:3px}
a:hover{color:var(--ink)}
bdi,.he{unicode-bidi:isolate}
code,kbd{font-family:var(--mono);font-size:.86em;background:var(--card);border:1px solid var(--rule);border-radius:3px;padding:0 .3em;overflow-wrap:anywhere}
h1,h2,h3{font-family:var(--serif);font-weight:normal;letter-spacing:-.01em;line-height:1.15;margin:0}
h1{font-size:clamp(30px,4.2vw,46px);max-width:24ch}
h2{font-size:clamp(24px,3vw,32px);margin:0 0 10px}
h3{font-size:20px;margin:36px 0 8px}
p{margin:0 0 14px}
p.lede{font-size:18px;line-height:1.5;color:var(--ink);max-width:70ch}
.prose{max-width:78ch}
.kicker{font-family:var(--sans);font-size:12px;letter-spacing:.14em;text-transform:uppercase;color:var(--accent);margin:0 0 8px}
.kicker b{font-weight:600}
.page{max-width:1360px;margin:0 auto;padding:0 clamp(16px,3vw,40px)}
.masthead{padding:34px 0 22px;border-bottom:1px solid var(--rule)}
.masthead .top{display:flex;justify-content:space-between;gap:16px;align-items:baseline;font-size:12px;letter-spacing:.14em;text-transform:uppercase;color:var(--ink-2);margin-bottom:28px}
.masthead .top .draft{color:var(--accent-ink);letter-spacing:.06em;text-transform:none}
.version{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px 28px;margin:26px 0 0;padding:14px 0 0;border-top:1px solid var(--rule);font-size:13px;color:var(--ink-2)}
.version dt{font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:var(--muted);margin:0 0 2px}
.version dd{margin:0;color:var(--ink)}
.version dd code{font-size:12px}
.layout{display:grid;grid-template-columns:minmax(0,1fr);gap:0 48px;align-items:start}
nav.toc{position:sticky;top:0;z-index:5;background:var(--paper);border-bottom:1px solid var(--rule);margin:0 0 8px;padding:8px 0}
nav.toc ol{list-style:none;margin:0;padding:0;display:flex;gap:4px 14px;overflow-x:auto;scrollbar-width:thin;white-space:nowrap}
nav.toc li a{display:inline-flex;align-items:center;gap:6px;padding:4px 2px;color:var(--ink-2);text-decoration:none;font-size:13px;border-bottom:2px solid transparent}
nav.toc li a:hover{color:var(--ink)}
nav.toc li a.active{color:var(--ink);border-bottom-color:var(--accent)}
nav.toc .n{color:var(--muted);font-size:11px;letter-spacing:.08em}
nav.toc .count{font-size:11px;padding:1px 6px;border-radius:9px;background:var(--card);border:1px solid var(--rule);color:var(--ink-2)}
nav.toc .count.flag{background:var(--flag-bg);border-color:transparent;color:var(--flag-ink)}
nav.toc .count.watch{background:var(--watch-bg);border-color:transparent;color:var(--watch-ink)}
nav.toc ol ol{display:none}
@media(min-width:1100px){
  .layout{grid-template-columns:228px minmax(0,1fr)}
  nav.toc{position:sticky;top:0;border:0;padding:28px 0 40px;max-height:100vh;overflow:auto;background:transparent;margin:0}
  nav.toc ol{display:block;white-space:normal}
  nav.toc li a{display:flex;justify-content:space-between;padding:5px 0;border-bottom:0;border-left:2px solid transparent;padding-left:12px;margin-left:-14px;line-height:1.3}
  nav.toc li a.active{border-left-color:var(--accent)}
  nav.toc ol ol{display:block;margin:0 0 6px 10px}
  nav.toc ol ol li a{font-size:12px;color:var(--muted);padding:2px 0 2px 12px}
  nav.toc .toc-title{display:block;font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--muted);margin:0 0 10px}
}
main{min-width:0;padding-bottom:40px}
section.sec{padding:40px 0 20px;border-top:1px solid var(--rule)}
section.sec:first-child{border-top:0;padding-top:28px}
.sub{margin-top:30px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(156px,1fr));gap:12px;margin:26px 0 0}
section.sec,.sub,figure.chart{scroll-margin-top:60px}
@media(min-width:1100px){section.sec,.sub,figure.chart{scroll-margin-top:12px}}
.tile{background:var(--card);border:1px solid var(--rule);border-radius:6px;padding:14px 16px 12px;min-width:0}
.tile .label{font-size:12px;color:var(--ink-2);margin:0 0 6px;letter-spacing:.02em}
.tile .value{font-size:34px;line-height:1;font-weight:600;letter-spacing:-.02em;color:var(--ink)}
.tile .value.zero{color:var(--muted);font-weight:500}
.tile .value small{font-size:14px;font-weight:500;color:var(--ink-2);letter-spacing:0;margin-left:4px}
.tile .note{font-size:12px;color:var(--muted);margin:8px 0 0;line-height:1.35}
.meter{height:6px;background:var(--rule);border-radius:3px;margin:10px 0 0;overflow:hidden}
.meter i{display:block;height:100%;background:var(--accent);border-radius:3px}
.callout{background:var(--card);border-left:3px solid var(--accent);padding:14px 18px;margin:22px 0;border-radius:0 6px 6px 0;max-width:86ch}
.callout.plain{border-left-color:var(--rule)}
.callout p:last-child{margin-bottom:0}
.chip{display:inline-flex;align-items:center;gap:6px;font-size:13px;font-weight:600;line-height:1.2;padding:3px 9px;border-radius:12px;white-space:nowrap;vertical-align:middle}
.chip-ok{background:var(--ok-bg);color:var(--ok-ink)}
.chip-warn{background:var(--watch-bg);color:var(--watch-ink)}
.chip-flag{background:var(--flag-bg);color:var(--flag-ink)}
.chip-info{background:var(--info-bg);color:var(--ink-2)}
.chip-muted{background:transparent;border:1px solid var(--rule);color:var(--muted);font-weight:500}
.chip::before{content:"";width:8px;height:8px;border-radius:50%;background:currentColor;opacity:.85}
.chip-flag::before{border-radius:1px}
.chip-muted::before{display:none}
.verdict{display:flex;flex-wrap:wrap;gap:8px 14px;align-items:center;margin:0 0 14px;font-size:15px}
.table-wrap{overflow-x:auto;margin:12px 0 10px;border:1px solid var(--rule);border-radius:6px;background:var(--card);scrollbar-width:thin}
table.data{border-collapse:separate;border-spacing:0;width:100%;font-size:13.5px;line-height:1.35}
table.data th,table.data td{padding:7px 10px;border-bottom:1px solid var(--rule);vertical-align:top;text-align:left}
table.data thead th{position:sticky;top:0;background:var(--card);color:var(--ink-2);font-weight:600;font-size:12px;letter-spacing:.02em;z-index:1;box-shadow:0 1px 0 var(--rule)}
table.data th.num,table.data td.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
table.data td.txt{min-width:16ch}
table.data tbody tr:last-child td{border-bottom:0}
table.data tbody tr:hover td{background:var(--paper)}
table.data tr.peers td{color:var(--ink-2);font-size:12.5px;background:var(--paper);border-bottom:1px solid var(--rule)}
table.data tr.dim td{color:var(--muted)}
table.data td .d{display:block;color:var(--ink-2);font-size:12px}
.table-tools{display:flex;gap:10px 16px;flex-wrap:wrap;align-items:center;margin:14px 0 0;font-size:13px;color:var(--ink-2)}
.table-tools input{font:inherit;color:var(--ink);background:var(--card);border:1px solid var(--rule);border-radius:4px;padding:5px 9px;min-width:min(100%,300px)}
.table-tools input:focus{outline:2px solid var(--accent);outline-offset:1px}
button.more{font:inherit;font-size:13px;color:var(--accent-ink);background:transparent;border:1px solid var(--rule);border-radius:4px;padding:5px 12px;cursor:pointer;margin:0 0 10px}
button.more:hover{border-color:var(--accent);color:var(--ink)}
.badge{display:inline-block;font-size:11.5px;font-weight:600;letter-spacing:.04em;padding:2px 8px;border-radius:3px;white-space:nowrap}
.badge-flag{background:var(--flag);color:#fff}
.badge-watch{background:var(--watch-bg);color:var(--watch-ink);border:1px solid var(--watch)}
.badge-none{background:transparent;color:var(--muted);border:1px solid var(--rule)}
figure.chart{margin:18px 0 20px;background:var(--card);border:1px solid var(--rule);border-radius:6px;padding:12px 8px 4px;overflow:hidden;min-width:0}
figure.chart figcaption{font-size:12.5px;color:var(--ink-2);padding:6px 10px 8px;line-height:1.4}
.chart-title{font-family:var(--serif);font-weight:normal;font-size:17px;line-height:1.3;margin:0 0 4px;padding:0 10px;color:var(--ink)}
figure.chart .js-plotly-plot{max-width:100%}
.note{font-size:13.5px;color:var(--ink-2);border-left:2px solid var(--rule);padding:2px 0 2px 14px;margin:10px 0 18px;max-width:86ch}
.note p:last-child{margin-bottom:0}
ul.limits{padding-left:20px;max-width:80ch}ul.limits li{margin:0 0 8px}
.files{display:flex;flex-wrap:wrap;gap:6px 8px;margin:8px 0 14px}
.files code{font-size:12px}
dl.kv{display:grid;grid-template-columns:minmax(0,1fr) max-content;gap:4px 16px;font-size:13.5px;margin:10px 0;max-width:60ch}
dl.kv dt{color:var(--ink-2)}dl.kv dd{margin:0;font-variant-numeric:tabular-nums;text-align:right}
nav.toc .toc-title{display:none}
footer.colophon{border-top:1px solid var(--rule);padding:22px 0 50px;font-size:13px;color:var(--ink-2);max-width:90ch}
footer.colophon p{margin:0 0 8px}
.sr-only{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
@media(max-width:640px){
  body{font-size:15px}
  .masthead{padding-top:22px}
  .masthead .top{flex-direction:column;gap:4px;margin-bottom:18px}
  .tile .value{font-size:28px}
  table.data{font-size:12.5px}
  table.data th,table.data td{padding:6px 8px}
  .page{padding:0 16px}
  .table-wrap{margin-left:-16px;margin-right:-16px;border-radius:0;border-left:0;border-right:0}
}
@media print{
  @page{margin:14mm}
  body{background:#fff;color:#000;font-size:10.5pt}
  nav.toc,.table-tools,button.more,.no-print{display:none!important}
  .layout{display:block}
  .page{max-width:none;padding:0}
  .tile,.table-wrap,figure.chart,.callout{border-color:#bbb;background:#fff;break-inside:avoid}
  table.data thead th{position:static}
  table.data tr{break-inside:avoid}
  table.data tr.extra{display:table-row!important}
  table.data td,table.data th{font-size:8.5pt;padding:3px 5px}
  figure.chart{break-inside:avoid}
  h2{break-after:avoid}
  a{color:inherit;text-decoration:none}
  .masthead .version dd code{border:0;background:none}
}
"""


def stylesheet() -> str:
    """Tokens + base rules, ready to drop into a `<style>` element."""
    return _css_tokens() + "\n" + BASE_CSS


#: Behaviour that degrades gracefully: without JavaScript every row is visible and nothing is lost.
BASE_JS = r"""
(function(){
  var LIMIT = 12;
  document.querySelectorAll('table.data[data-collapse]').forEach(function(t){
    var rows = Array.prototype.slice.call(t.tBodies[0].rows).filter(function(r){return !r.classList.contains('peers')});
    if (rows.length <= LIMIT) return;
    var keep = 0, hidden = 0;
    Array.prototype.forEach.call(t.tBodies[0].rows, function(r){
      if (!r.classList.contains('peers')) keep++;
      if (keep > LIMIT) { r.classList.add('extra'); r.hidden = true; hidden++; }
    });
    var b = document.createElement('button'); b.type = 'button'; b.className = 'more';
    var total = rows.length;
    b.textContent = 'Показать все строки (' + total + ')';
    b.addEventListener('click', function(){
      var open = b.getAttribute('data-open') === '1';
      t.querySelectorAll('tr.extra').forEach(function(r){ r.hidden = open; });
      b.setAttribute('data-open', open ? '0' : '1');
      b.textContent = open ? 'Показать все строки (' + total + ')' : 'Свернуть до ' + LIMIT + ' строк';
    });
    t.closest('.table-wrap').insertAdjacentElement('afterend', b);
  });
  document.querySelectorAll('input[data-filter]').forEach(function(inp){
    var t = document.getElementById(inp.getAttribute('data-filter'));
    if (!t) return;
    inp.addEventListener('input', function(){
      var q = inp.value.trim().toLocaleLowerCase();
      var btn = t.closest('.table-wrap').nextElementSibling;
      Array.prototype.forEach.call(t.tBodies[0].rows, function(r){
        if (r.classList.contains('peers')) { r.hidden = r.previousElementSibling ? r.previousElementSibling.hidden : false; return; }
        var match = !q || r.textContent.toLocaleLowerCase().indexOf(q) >= 0;
        r.hidden = q ? !match : r.classList.contains('extra') && !(btn && btn.getAttribute('data-open') === '1');
      });
      if (btn && btn.classList.contains('more')) btn.hidden = !!q;
    });
  });
  var links = Array.prototype.slice.call(document.querySelectorAll('nav.toc a[href^="#"]'));
  if ('IntersectionObserver' in window && links.length) {
    var byId = {}; links.forEach(function(a){ byId[a.getAttribute('href').slice(1)] = a; });
    var current = null;
    var io = new IntersectionObserver(function(entries){
      entries.forEach(function(e){ if (e.isIntersecting) current = e.target.id; });
      if (!current) return;
      links.forEach(function(a){ a.classList.remove('active'); });
      var a = byId[current]; if (a) a.classList.add('active');
    }, {rootMargin: '-10% 0px -75% 0px'});
    Object.keys(byId).forEach(function(id){ var el = document.getElementById(id); if (el) io.observe(el); });
  }
})();
"""
