"""The long-form report (Russian, HTML) built from processed battery results and the calibration.

The report never recomputes statistics: it reads what `ef battery` and `ef calibrate` wrote, so
that every number in it is traceable to a file with a data version. Figures are plotly; tables
are exported next to the report as CSV (the open appendix). The look comes from `present/theme.py`
(one visual system for the issue, the technical report and the locality reports); the page opens
offline: Plotly is embedded, no font or script is fetched.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
from jinja2 import Environment
from plotly.offline import get_plotlyjs

from electionforensics import __version__
from electionforensics.config import DATA_PROCESSED, REPORTS
from electionforensics.ingest.places import attach_places, load_places, load_sites, place_label
from electionforensics.ingest.registry import ELECTIONS
from electionforensics.present import figures, theme
from electionforensics.provenance import battery_source, pinned_election, require_calibrated
from electionforensics.taxonomy import party_label

LOCAL_TZ = ZoneInfo("Asia/Jerusalem")

EXPECTED_STATIONS_26 = 12_018  # CEC figure quoted by ynet on 27.10.2026 (docs/research/israel-data-map-2026-10-28.md)
TEST_NAMES_RU = {
    "T01": "целостность файла", "T02": "отпечаток явка × доля", "T03": "параметрическая подгонка Климека", "T05": "целые проценты",
    "T06": "грубые дроби долей", "T07": "последние цифры счётов", "T08": "иерархические остатки",
    "T09": "соседние урны в одном здании", "T10": "ноль у списка при ненулевых соседях",
    "T11": "недействительные против соседей и истории",
    "T12": "сдвиг против собственной истории", "T13": "малые участки против крупных", "T14": "динамика подсчёта",
    "T15": "двойные конверты", "T16": "голоса в мандаты", "T17": "перенос клетки: голоса крупного списка у малого",
}

#: Names of the CSV tables written next to the report (the open appendix), in reading order.
TABLE_FILES = ["flags_and_watchlist", "revisions", "seats", "validity", "zero_against_peers", "sibling_stations", "history_shift",
               "last_digits_localities", "last_digits_parties", "invalid_ballots", "small_stations", "small_stations_localities",
               "double_envelopes_batches", "double_envelopes_parties", "coarse_fractions", "klimek_fit", "klimek_grid",
               "hierarchical_residuals", "displacements"]


def _p(k: int, name: str) -> Path:
    return DATA_PROCESSED / f"knesset{k}" / name


def _summary(k: int, tid: str) -> dict[str, Any]:
    p = _p(k, f"{tid}.json")
    return json.loads(p.read_text(encoding="utf-8"))["summary"] if p.exists() else {}


def _units(k: int, tid: str, extra: str | None = None) -> pd.DataFrame:
    p = _p(k, f"{tid}_{extra}.parquet" if extra else f"{tid}.parquet")
    return pd.read_parquet(p) if p.exists() else pd.DataFrame()


def _fmt_int(x: Any) -> str:
    try:
        return f"{int(x):,}".replace(",", " ")
    except (TypeError, ValueError):
        return "—"


def _fmt_num(x: Any, digits: int = 1, sign: bool = False) -> str:
    """A float with a fixed number of decimals, or an em dash for NaN/None (Jinja cannot test NaN itself)."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "—"
    if v != v:
        return "—"
    return f"{v:+.{digits}f}" if sign else f"{v:.{digits}f}"


def _ts_local(ts: str | None) -> str:
    if not ts:
        return "—"
    dt = datetime.strptime(ts, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    return dt.astimezone(LOCAL_TZ).strftime("%Y-%m-%d %H:%M (%Z)")


def rounding_assessment(summary: dict[str, Any], calibration: dict[str, Any]) -> str:
    """A conclusion must change when the data change, and stay scoped to this diagnostic."""
    comparisons = calibration.get("election_level", {})
    if not all(f"T05_{name}" in comparisons and name in summary for name in ("turnout", "leader_share")):
        return "Для вывода о целых процентах не хватает калибровки."
    excess = any(summary[name].get("p_mc", 1) <= 0.05 and summary[name]["z"] > comparisons[f"T05_{name}"]["baseline_max"]
                 for name in ("turnout", "leader_share"))
    return ("Избыток целых процентов превышает историческую верхнюю границу. Нужна проверка протоколов."
            if excess else "Этот диагностический тест не выделяет избытка целых процентов сверх исторического диапазона.")


def _peer_summaries(knesset: int, flags: pd.DataFrame) -> list[str]:
    """For each flagged station: its own turnout and top list, then the building peers' values — what the editor needs to judge."""
    from electionforensics.ingest.cbs import load_cbs
    from electionforensics.model.features import build_features

    feat = build_features(pinned_election(knesset, battery_source(knesset)), load_cbs())
    st = feat.stations
    out: list[str] = []
    for _, r in flags.iterrows():
        me = st[(st["locality_code"] == r["locality_code"]) & (st["station"] == r["station"])]
        if me.empty:
            out.append("")
            continue
        me = me.iloc[0]
        peers = st[(st["site_key"] == me["site_key"]) & (st["station"] != me["station"])] if pd.notna(me["site_key"]) else st.iloc[0:0]
        detail = str(r.get("T09_detail") or r.get("T10_detail") or "")
        top = next((p for p in feat.major if f" {p} " in f" {detail.split(';')[-1].strip()} " or detail.strip().startswith(p + " ")), None)
        if top is None:
            for p in feat.major:
                if p in detail:
                    top = p
                    break
        top = top or feat.leader

        def fmt(x: Any) -> str:
            return f"{100 * float(x):.0f}" if pd.notna(x) else "—"

        mine = f"сам: явка {fmt(me['turnout'])} %, {top} {fmt(me[f's_{top}'])} %"
        if peers.empty:
            out.append(mine + "; соседей по зданию нет")
            continue
        pt = "/".join(fmt(x) for x in peers["turnout"].head(8))
        ps = "/".join(fmt(x) for x in peers[f"s_{top}"].head(8))
        out.append(f"{mine}; соседи ({len(peers)}): явка {pt} %, {top} {ps} %")
    return out


def render(ctx: dict[str, Any]) -> str:
    """Render the page from a complete context (what `build` assembles; tests pass a minimal one)."""
    base = {"css": theme.stylesheet(), "js": theme.BASE_JS, "fmt": _fmt_int, "num": _fmt_num, "tsl": _ts_local,
            "version": __version__, "test_names": TEST_NAMES_RU, "table_files": TABLE_FILES}
    return Environment(autoescape=True).from_string(TEMPLATE).render(**{**base, **ctx})


def build(knesset: int = 26, out_dir: Path | None = None) -> Path:
    k = knesset
    src = require_calibrated(k)
    ts = src.get("snapshot_ts")
    day = datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")
    out_dir = out_dir or (REPORTS / f"{day}_k{k}_{ts or 'final'}")
    (out_dir / "tables").mkdir(parents=True, exist_ok=True)

    t01, t03, t05, t06, t07, t08, t09, t10, t11, t12, t13, t14, t15, t16, t17 = (
        _summary(k, t) for t in ("T01", "T03", "T05", "T06", "T07", "T08", "T09", "T10", "T11", "T12", "T13", "T14", "T15", "T16", "T17"))
    t15_parties = _units(k, "T15", "parties")
    if not t15_parties.empty:
        t15_parties["label"] = [party_label(k, p) for p in t15_parties["party"]]
    cal = json.loads(_p(k, "CAL.json").read_text(encoding="utf-8")) if _p(k, "CAL.json").exists() else {}
    cal_st = pd.read_parquet(_p(k, "CAL_stations.parquet")) if _p(k, "CAL_stations.parquet").exists() else pd.DataFrame()
    # polling-place register (CEC voter guide): place, address and type for every station-level table
    try:
        places = load_places(k)
    except FileNotFoundError:
        places = pd.DataFrame()
    try:
        sites = load_sites(k)
    except FileNotFoundError:
        sites = pd.DataFrame()

    def with_places(df: pd.DataFrame) -> pd.DataFrame:
        if df.empty or places.empty or not {"locality_code", "station"} <= set(df.columns):
            return df
        return attach_places(df, k, places, sites)

    cal_st = with_places(cal_st)
    cal_loc = pd.read_parquet(_p(k, "CAL_localities.parquet")) if _p(k, "CAL_localities.parquet").exists() else pd.DataFrame()
    t07_top = pd.DataFrame()
    if not cal_loc.empty and "T07_score_pctl" in cal_loc.columns:
        t07_top = cal_loc[(cal_loc["T07_score_pctl"] >= 0.999) | cal_loc["T07_above_baseline_max"].fillna(False)].copy()
        t07_top = t07_top.astype(object).where(t07_top.notna(), float("nan"))
    n_stations = t01.get("n_stations", 0)
    t03_rows: list[dict[str, Any]] = []
    for sec in ("jewish", "arab", "haredi", "druze", "bedouin", "mixed", "all"):
        r3 = (t03.get("by_sector") or {}).get(sec)
        if r3:
            el = cal.get("election_level", {}) if cal else {}
            t03_rows.append({"sector": sec, **r3, "bi": el.get(f"T03_f_i_{sec}"), "be": el.get(f"T03_f_e_{sec}")})

    figs: dict[str, str] = {}
    if t14.get("per_snapshot"):
        figs["progress"] = theme.figure_html(figures.count_progress(t14["per_snapshot"], EXPECTED_STATIONS_26))
    hists = {kk: _units(kk, "T02", "hist") for kk in (22, 23, 24, 25, k) if not _units(kk, "T02", "hist").empty}
    leaders = {kk: _summary(kk, "T02").get("leader", "") for kk in hists}
    for sector in ("jewish", "arab", "haredi", "mixed"):
        sec_leaders = {kk: _summary(kk, "T02").get("by_sector", {}).get(sector, {}).get("leader", leaders[kk]) for kk in hists}
        figs[f"fp_{sector}"] = theme.figure_html(figures.fingerprint_panels(
            hists, sector, sec_leaders, f"Отпечаток: {figures.SECTOR_RU.get(sector, sector)} населённые пункты"))
    figs["t09"] = theme.figure_html(figures.score_ecdf(
        {kk: _units(kk, "T09")["max_abs_z"] for kk in (22, 23, 24, 25, k) if not _units(kk, "T09").empty},
        "T09: наибольшее |z| участка против соседей по зданию", "|z| (биномиальная шкала, до калибровки)"))
    figs["t12"] = theme.figure_html(figures.score_ecdf(
        {kk: _units(kk, "T12")["max_abs_z"] for kk in (22, 23, 24, 25, k) if not _units(kk, "T12").empty},
        "T12: сдвиг участка против собственной истории", "робастный |z| внутри сектора"))
    t16u = _units(k, "T16")
    if not t16u.empty:
        figs["seats"] = theme.figure_html(figures.seats_bar(t16u, k))

    # tables
    flags = cal_st[cal_st["tier"].isin(["flag", "watch"])].copy() if not cal_st.empty else pd.DataFrame()
    if not flags.empty:
        flags = flags.astype(object).where(flags.notna(), float("nan"))
        flags["peers"] = _peer_summaries(k, flags)
        flags = flags.sort_values(["tier", "locality_name"], ascending=[True, True])
    t12_top = pd.DataFrame()
    if not cal_st.empty and "T12_turnout_pctl" in cal_st.columns:
        t12_top = cal_st[(cal_st["T12_turnout_pctl"] >= 0.999) | (cal_st["T12_party_pctl"] >= 0.999)].copy()
        t12_top = t12_top.astype(object).where(t12_top.notna(), float("nan")).sort_values("T12_turnout", ascending=False)
    t08_top = pd.DataFrame()
    if not cal_st.empty and "T08_turnout_pctl" in cal_st.columns:
        t08_top = cal_st[(cal_st["T08_turnout_pctl"] >= 0.999) | (cal_st["T08_leader_pctl"] >= 0.999)].copy()
        t08_top = t08_top.astype(object).where(t08_top.notna(), float("nan")).sort_values("T08_turnout", ascending=False)
    t17_top = _units(k, "T17")
    if not t17_top.empty:
        t17_top = with_places(t17_top[t17_top["case"]].copy()).sort_values("votes", ascending=False)
    t13_top = pd.DataFrame()
    if not cal_st.empty and "T13_turnout_pctl" in cal_st.columns:
        t13_top = cal_st[(cal_st["T13_turnout_pctl"] >= 0.999) | (cal_st["T13_leader_pctl"] >= 0.999)].copy()
        t13_top = t13_top.astype(object).where(t13_top.notna(), float("nan")).sort_values("T13_turnout", ascending=False)
    rev = _units(k, "T14")
    seats = t16u.copy()
    if not seats.empty:
        seats["label"] = [party_label(k, p) for p in seats["party"]]
    for name, df in (("flags_and_watchlist", flags), ("revisions", rev), ("seats", seats), ("validity", _units(k, "T01")),
                     ("zero_against_peers", _units(k, "T10")), ("sibling_stations", _units(k, "T09")), ("history_shift", _units(k, "T12")),
                     ("last_digits_localities", cal_loc), ("last_digits_parties", _units(k, "T07", "parties")),
                     ("invalid_ballots", _units(k, "T11")), ("small_stations", _units(k, "T13")),
                     ("small_stations_localities", _units(k, "T13", "localities")),
                     ("double_envelopes_batches", _units(k, "T15")), ("double_envelopes_parties", t15_parties),
                     ("coarse_fractions", _units(k, "T06", "fractions")), ("klimek_fit", _units(k, "T03")), ("klimek_grid", _units(k, "T03", "grid")),
                     ("hierarchical_residuals", _units(k, "T08")), ("displacements", _units(k, "T17"))):
        if not df.empty:
            with_places(df).to_csv(out_dir / "tables" / f"{name}.csv", index=False, encoding="utf-8-sig")

    power_files = sorted((DATA_PROCESSED / "power").glob("power_*.json")) if (DATA_PROCESSED / "power").exists() else []
    power_rows: list[dict[str, Any]] = []
    if power_files:
        for r in json.loads(power_files[-1].read_text(encoding="utf-8")):
            row = {"kind": r["kind"], "intensity": r["intensity"], "fraction": r["fraction"], "n": r["n_altered"], "base": r["base_knesset"]}
            for tid, v in r["tests"].items():
                if tid == "T05":
                    row["T05"] = f"{v['shift']:+.1f}"
                else:
                    p1, p2 = v.get("p0.999", {}).get("power"), v.get("p0.99", {}).get("power")
                    row[tid] = f"{p1:.2f} / {p2:.2f}" if p1 is not None and p2 is not None else "—"
            power_rows.append(row)
    power_cols = [c for c in ("T08:turnout", "T08:leader", "T09:turnout", "T09:party", "T10:party", "T11:peers", "T11:history",
                              "T12:turnout", "T12:party", "T05") if any(c in r for r in power_rows)]
    t01_issues = sum(int(v) for key, v in t01.items() if key.startswith("flag_") and isinstance(v, int | float))

    ctx = {
        "power_rows": power_rows, "power_cols": power_cols, "power_file": power_files[-1].name if power_files else None,
        "k": k, "built": datetime.now(LOCAL_TZ).strftime("%Y-%m-%d %H:%M"),
        "plotly_js": get_plotlyjs(), "rounding_assessment": rounding_assessment(t05, cal),
        "snapshot_local": _ts_local(ts), "sha": src.get("sha256", ""), "file": src.get("file", ""),
        "n_stations": n_stations, "expected": EXPECTED_STATIONS_26, "share_counted": n_stations / EXPECTED_STATIONS_26 if n_stations else 0,
        "election": ELECTIONS[k], "t01": t01, "t01_issues": t01_issues, "t03": t03, "t03_rows": t03_rows, "t05": t05, "t06": t06,
        "t07": t07, "t09": t09, "t10": t10, "t11": t11, "t12": t12, "t13": t13,
        "t14": t14, "t15": t15, "t15_parties": t15_parties, "t16": t16, "t17": t17, "t17_top": t17_top,
        "cal": cal, "flags": flags, "t12_top": t12_top, "t13_top": t13_top, "t08_top": t08_top, "t08": t08, "t07_top": t07_top, "rev": rev,
        "seats": seats, "figs": figs,
        "label": lambda p: party_label(k, p), "place": place_label, "has_places": not places.empty,
        "per_snapshot": t14.get("per_snapshot", []),
    }
    html = render(ctx)
    out = out_dir / "report.html"
    out.write_text(html, encoding="utf-8")
    return out


TEMPLATE = r"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Кнессет {{ k }}: данные подсчёта — технический отчёт</title>
<meta name="description" content="Технический отчёт ElectionForensics по опубликованным данным подсчёта голосов на выборах в {{ k }}-й Кнессет: ход подсчёта, целостность файла, флаги и лист наблюдения, тесты уровня выборов, мандаты.">
<script>{{ plotly_js|safe }}</script>
<style>{{ css|safe }}</style>
</head><body>
{% set el = cal.get('election_level', {}) if cal else {} %}
{% set tiers = cal.get('tiers', {}) if cal else {} %}
{% set loc = cal.get('localities', {}) if cal else {} %}
{% set n_flag = tiers.get('flag', 0) if tiers else 0 %}
{% set n_watch = tiers.get('watch', 0) if tiers else 0 %}
<div class="page">
<header class="masthead">
  <div class="top"><span>Election Forensics · технический отчёт</span><span>Кнессет {{ k }}{% if election %} · {{ election.date.strftime('%d.%m.%Y') }}{% endif %} · промежуточный подсчёт</span></div>
  <p class="kicker">Опубликованные данные подсчёта</p>
  <h1>Выборы в {{ k }}-й Кнессет: что говорят опубликованные данные подсчёта</h1>
  <p class="lede" style="margin-top:14px">Каждый статистический тест сначала прогнан на выборах 2019–2022 годов (а часть и с 2003 года); «необычным» считается только то, чего те выборы не давали. Статистическая нерегулярность — это повод проверить протокол, а не вывод о нарушении: у каждой строки ниже перечислены возможные объяснения.</p>
  <div class="tiles">
    <div class="tile"><p class="label">Урн в файле</p><div class="value">{{ fmt(n_stations) }}<small>из {{ fmt(expected) }}</small></div><div class="meter"><i style="width:{{ '%.1f'|format(share_counted*100) }}%"></i></div><p class="note">{{ "%.0f"|format(share_counted*100) }} % урн по реестру; {% if not t15.n_batches %}двойные конверты в файле отсутствуют{% else %}двойные конверты присутствуют и рассмотрены отдельно{% endif %}.</p></div>
    <div class="tile"><p class="label">Флаги</p><div class="value{% if not n_flag %} zero{% endif %}">{{ n_flag }}</div><p class="note">калиброванный отбор с поправками на множественность; ноль флагов не доказывает чистоту подсчёта</p></div>
    <div class="tile"><p class="label">Лист наблюдения</p><div class="value{% if not n_watch %} zero{% endif %}">{{ n_watch }}</div><p class="note">выше 99,9‑го процентиля базы хотя бы по одному тесту — для ручного разбора по адресам</p></div>
    <div class="tile"><p class="label">Перенос клетки (T17)</p><div class="value{% if not t17.n_cases %} zero{% endif %}">{{ t17.n_cases if t17 else '—' }}</div><p class="note">{% if t17 %}кандидатов на сверку с протоколом · {{ fmt(t17.displaced_votes) }} голосов · разведочно{% else %}тест не запускался{% endif %}</p></div>
    <div class="tile"><p class="label">Ревизии между снимками</p><div class="value{% if not t14.n_revised_cells %} zero{% endif %}">{{ t14.n_revised_cells if t14 else '—' }}</div><p class="note">{% if t14 %}ячеек в {{ t14.n_revised_stations }} участках за {{ t14.n_snapshots }} снимков{% else %}снимков нет{% endif %}</p></div>
  </div>
  <dl class="version">
    <div><dt>Файл ЦИК</dt><dd><code>{{ file }}</code></dd></div>
    <div><dt>Снимок</dt><dd>{{ snapshot_local }}</dd></div>
    <div><dt>SHA‑256 файла</dt><dd><code>{{ sha }}</code></dd></div>
    <div><dt>Сборка</dt><dd>{{ built }} · ElectionForensics {{ version }}</dd></div>
  </dl>
</header>

<div class="layout">
<nav class="toc" aria-label="Содержание"><p class="toc-title">Содержание</p><ol>
  <li><a href="#count"><span><span class="n">01</span> Ход подсчёта</span>{% if t14.n_revised_cells %}<span class="count">{{ t14.n_revised_cells }}</span>{% endif %}</a></li>
  <li><a href="#integrity"><span><span class="n">02</span> Целостность файла</span>{% if t01_issues %}<span class="count">{{ t01_issues }}</span>{% endif %}</a></li>
  <li><a href="#tiers"><span><span class="n">03</span> Флаги и лист наблюдения</span><span class="count{% if n_flag %} flag{% elif n_watch %} watch{% endif %}">{{ n_flag }} / {{ n_watch }}</span></a>
    <ol><li><a href="#t12">Сдвиг против истории (T12)</a></li><li><a href="#t08">Иерархические остатки (T08)</a></li><li><a href="#t17">Перенос клетки (T17)</a></li></ol></li>
  <li><a href="#election"><span><span class="n">04</span> Тесты уровня выборов</span></a>
    <ol><li><a href="#t05">Целые проценты (T05)</a></li><li><a href="#t06">Грубые дроби (T06)</a></li><li><a href="#t07">Последние цифры (T07)</a></li><li><a href="#t13">Малые участки (T13)</a></li><li><a href="#t15">Двойные конверты (T15)</a></li><li><a href="#curves">Соседи и история (T09, T12)</a></li><li><a href="#t02">Отпечатки (T02)</a></li><li><a href="#t03">Подгонка Климека (T03)</a></li></ol></li>
  <li><a href="#seats"><span><span class="n">05</span> Голоса в мандаты</span></a></li>
  <li><a href="#power"><span><span class="n">06</span> Чувствительность тестов</span></a></li>
  <li><a href="#limits"><span><span class="n">07</span> Ограничения</span></a></li>
  <li><a href="#appendix"><span><span class="n">A</span> Приложение</span></a></li>
</ol></nav>

<main>
<section class="sec" id="count">
<p class="kicker">01 · Динамика</p><h2>Ход подсчёта и ревизии опубликованных чисел</h2>
{% if per_snapshot %}
<p class="lede">Снимков файла: {{ t14.n_snapshots }} (первый {{ tsl(t14.first) }}, последний {{ tsl(t14.last) }}). Участков в последнем снимке: {{ fmt(n_stations) }}.
Ревизий уже опубликованных чисел между снимками: {{ t14.n_revised_cells }} ячеек в {{ t14.n_revised_stations }} участках.</p>
<figure class="chart">{{ figs.progress|safe }}<figcaption>Верхняя панель — сколько урн присутствует в каждом сохранённом снимке файла; серая линия — число урн по предвыборному реестру. Нижняя — сколько урн между соседними снимками изменили уже опубликованные числа.</figcaption></figure>
{% if rev|length %}
<div class="table-wrap"><table class="data" data-collapse><thead><tr><th class="txt">Населённый пункт</th><th class="num">Участок</th><th>Поле</th><th class="num">Было</th><th class="num">Стало</th><th>Между снимками (время Израиля)</th></tr></thead><tbody>
{% for _, r in rev.iterrows() %}<tr><td class="txt"><bdi>{{ r.locality_name }}</bdi></td><td class="num">{{ r.station }}</td><td>{{ r.field }}</td><td class="num">{{ r.old }}</td><td class="num">{{ r.new }}</td><td>{{ tsl(r.ts_from) }} → {{ tsl(r.ts_to) }}</td></tr>{% endfor %}</tbody></table></div>
<div class="note"><p>Исправление протокола председателем окружной комиссии или ЦИК предусмотрено законом (§ 79(ה)–(ז) Закона о выборах в Кнессет: арифметическая ошибка, ошибка переписывания, запись голосов не тому списку); таблица фиксирует факт правки, чтобы её можно было сверить со сканом протокола.</p></div>
{% endif %}{% else %}<p>Снимков подсчёта для этих выборов нет.</p>{% endif %}
</section>

<section class="sec" id="integrity">
<p class="kicker">02 · Арифметика</p><h2>Целостность файла (T01)</h2>
<div class="verdict">{% if t01_issues %}<span class="chip chip-warn">{{ t01_issues }} строк с формальными отклонениями</span>{% else %}<span class="chip chip-ok">формальные проверки пройдены</span>{% endif %}<span>проверено {{ fmt(t01.n_stations) }} участков</span></div>
<dl class="kv">
<dt>Явка выше 100 %</dt><dd>{{ t01.flag_turnout_gt_100 }}</dd><dt>Явка ровно 100 %</dt><dd>{{ t01.flag_turnout_eq_100 }}</dd>
<dt>Ноль проголосовавших при ненулевом списке избирателей</dt><dd>{{ t01.flag_zero_voters }}</dd>
<dt>Сумма по спискам ≠ действительным</dt><dd>{{ t01.flag_sum_parties_ne_valid }}</dd>
<dt>Действительные + недействительные ≠ проголосовавшим</dt><dd>{{ t01.flag_valid_plus_invalid_ne_voters }}</dd>
<dt>Дубли номеров участков</dt><dd>{{ t01.flag_duplicate_station }}</dd><dt>Доля недействительных выше 10 %</dt><dd>{{ t01.flag_invalid_share_gt_10pct }}</dd>
</dl>
<div class="note"><p>Это проверка сумм и формальных ограничений, не подтверждение правильности каждой цифры: ошибка переписывания, сохраняющая суммы, здесь невидима и ищется тестами ниже.</p></div>
</section>

<section class="sec" id="tiers">
<p class="kicker">03 · Участки</p><h2>Флаги и лист наблюдения</h2>
<div class="verdict"><span class="chip{% if n_flag %} chip-flag{% else %} chip-muted{% endif %}">флагов: {{ n_flag }}</span><span class="chip{% if n_watch %} chip-warn{% else %} chip-muted{% endif %}">в листе наблюдения: {{ n_watch }}</span>{% if tiers.none is defined %}<span class="chip chip-muted">без яруса: {{ fmt(tiers.none) }}</span>{% endif %}</div>
<p class="prose">Правило ярусов: оценка участка по каждому тесту помещается в распределение той же оценки для того же сектора в 2019–2022 годах.
<b>Лист наблюдения</b> — выше 99,9‑го процентиля базы хотя бы по одному тесту. <b>Флаг</b> — выше всего, что встречалось в базе,
не менее двух тестов указывают на тот же участок и q после поправок Бонферрони и Benjamini–Yekutieli ≤ 0,05. Методы зависимы, историческая обменность остаётся предположением.</p>
{% if flags|length %}
<div class="table-tools"><label>Поиск по таблице <input data-filter="tiers-table" placeholder="населённый пункт, участок, список"></label></div>
<div class="table-wrap"><table class="data" id="tiers-table" data-collapse><thead><tr><th>Ярус</th><th class="txt">Населённый пункт</th><th class="num">Участок</th><th>Сектор</th><th class="txt">Место голосования (адрес, тип)</th><th class="txt">T09 соседи по зданию (z явки; список z)</th><th class="txt">T10 ноль против соседей</th><th class="txt">T11 недействительные против соседей</th><th class="txt">T12 дрейф к соседям против 2022</th></tr></thead><tbody>
{% for _, r in flags.iterrows() %}<tr><td><span class="badge badge-{{ r.tier }}">{{ 'флаг' if r.tier == 'flag' else 'наблюдение' }}</span></td><td class="txt"><bdi>{{ r.locality_name }}</bdi></td><td class="num">{{ r.station }}</td><td>{{ r.sector }}</td><td class="txt"><bdi>{{ place(r) }}</bdi></td>
<td class="txt">{% if r.T09_turnout == r.T09_turnout %}{{ r.T09_detail }}<span class="d">pctl явка {{ num(r.T09_turnout_pctl, 4) }} / список {{ num(r.T09_party_pctl, 4) }}</span>{% endif %}</td>
<td class="txt">{% if r.T10_score == r.T10_score %}{{ r.T10_detail }}<span class="d">−log₁₀p {{ num(r.T10_score, 0) }} · pctl {{ num(r.T10_score_pctl, 4) }}</span>{% endif %}</td>
<td class="txt">{% if "T11_peers" in r and r.T11_peers == r.T11_peers %}{{ r.T11_detail }}{% if r.T11_peers > 0 %}<span class="d">pctl {{ num(r.T11_peers_pctl, 4) }}</span>{% endif %}{% endif %}</td>
<td class="txt">{% if r.T12_peerdrift == r.T12_peerdrift %}дрейф к соседям z {{ num(r.T12_peerdrift, 1) }}<span class="d">pctl {{ num(r.T12_peerdrift_pctl, 4) }}</span>{% else %}нет пары 2022{% endif %}</td></tr>
<tr class="peers"><td></td><td colspan="8">{{ r.peers }}</td></tr>{% endfor %}</tbody></table></div>
<div class="note"><p>Чтение: «ג 80.1» означает, что доля списка ג на участке на 80 биномиальных сигм выше доли на соседних урнах того же здания; явка и списки калибруются отдельно, потому что законная неоднородность долей между соседними урнами много больше неоднородности явки.
Колонка T11: доля недействительных конвертов участка против соседей по зданию (z ≥ 0 — только избыток; необычно «чистый» участок не оценивается); в стране бракуется {% if t11.invalid_share_national %}{{ "%.1f"|format(t11.invalid_share_national*100) }} %{% else %}около 1 %{% endif %} конвертов.</p>
<p>Для харедимных и арабских списков в еврейских городах самое частое объяснение — участок обслуживает другой квартал, чем его соседи по зданию;
такие строки остаются в листе наблюдения, пока их не объяснит место голосования. Колонка «место голосования» берётся из списка ЦИК
«רשימת מקומות הקלפי» (состояние на 25.10.2026): название здания, адрес и тип места. Если соседи по зданию числятся по тому же адресу, адрес сам
по себе различие не объясняет — тогда различие в публике задаётся нарезкой избирательных участков, а не зданием, и нужен скан протокола.</p></div>
{% else %}<p>Ни один участок не превысил базу 2019–2022 годов.</p>{% endif %}

<div class="sub" id="t12"><h3>Разведочный список: сдвиг против собственной истории (T12)</h3>
<p class="prose">Не участвует в ярусах для этих выборов: пары в базе отстоят на 1–2 года, а 2022→2026 — на четыре, и дрейф участков
(перенарезка границ, новые кварталы) больше всего, что есть в базе. Список приводится для проверки по адресам участков.</p>
{% if t12_top|length %}
<div class="table-wrap"><table class="data" data-collapse><thead><tr><th class="txt">Населённый пункт</th><th class="num">Участок</th><th>Сектор</th><th class="txt">Место голосования</th><th class="num">z явки</th><th class="txt">Список, z</th></tr></thead><tbody>
{% for _, r in t12_top.head(25).iterrows() %}<tr><td class="txt"><bdi>{{ r.locality_name }}</bdi></td><td class="num">{{ r.station }}</td><td>{{ r.sector }}</td><td class="txt"><bdi>{{ place(r) }}</bdi></td><td class="num">{{ num(r.T12_turnout, 1) }}</td><td class="txt">{{ r.T12_detail }}</td></tr>{% endfor %}</tbody></table></div>
{% endif %}</div>

{% if t08 and t08.n_stations %}
<div class="sub" id="t08"><h3>Разведочный список: иерархические остатки (T08)</h3>
<p class="prose">Одна модель на сектор: явка (и доля местного лидера) участка = эффект населённого пункта + размер участка + его собственное отклонение в прошлый раз.
Остаток стандартизован по сектору; в ярусах не участвует (перекрывается с T09 и T12), но покрывает участки без соседей по зданию и без пары 2022 года
({{ fmt(t08.n_stations) }} участков, из них с историей {{ fmt(t08.n_with_history) }}). Участков с |z| &gt; 4: {{ t08.n_above_4 }}, &gt; 6: {{ t08.n_above_6 }};
с избытком и явки, и лидера выше 3: {{ t08.n_both_above_3 }}.
{% if t08.components.jewish %}Устойчивость отклонений (γ, еврейский сектор): явка {{ "%.2f"|format(t08.components.jewish.turnout.gamma_history) }}, лидер {{ "%.2f"|format(t08.components.jewish.leader.gamma_history) }} —
доля прошлого отклонения, которая повторяется.{% endif %}</p>
{% if t08_top|length %}
<div class="table-wrap"><table class="data" data-collapse><thead><tr><th class="txt">Населённый пункт</th><th class="num">Участок</th><th>Сектор</th><th class="txt">Место голосования</th><th class="num">z явки</th><th class="txt">Остатки</th><th class="num">Процентиль базы (явка / лидер)</th></tr></thead><tbody>
{% for _, r in t08_top.head(25).iterrows() %}<tr><td class="txt"><bdi>{{ r.locality_name }}</bdi></td><td class="num">{{ r.station }}</td><td>{{ r.sector }}</td><td class="txt"><bdi>{{ place(r) }}</bdi></td><td class="num">{{ num(r.T08_turnout, 1) }}</td><td class="txt">{{ r.T08_detail }}</td><td class="num">{{ num(r.T08_turnout_pctl, 4) }} / {{ num(r.T08_leader_pctl, 4) }}</td></tr>{% endfor %}</tbody></table></div>
{% endif %}</div>{% endif %}

{% if t17 %}
<div class="sub" id="t17"><h3>Разведочный список: перенос одной клетки (T17)</h3>
<div class="verdict"><span class="chip chip-info">кандидатов {{ t17.n_candidates }} · случаев {{ t17.n_cases }} · {{ fmt(t17.displaced_votes) }} голосов</span><span class="chip chip-muted">разведочный тест</span></div>
<p class="prose">Список с долей по стране ниже 1 % держит на участке счёт, который по соседям (урны того же здания, иначе населённый пункт,
иначе страна) полагался крупному списку, стоящему на этом участке на нуле. Второе ожидание — по профилю: что дают этому
списку десять самых похожих по голосованию участков того же сектора; оно отделяет описку от другой публики за той же дверью.
Голосов в предполагаемых переносах — {{ "%.0f"|format(t17.displaced_per_million) }} на миллион действительных{% if el.T17_displaced_per_million %};
окончательные файлы 2019–2022: {{ "%.0f"|format(el.T17_displaced_per_million.baseline_min) }}…{{ "%.0f"|format(el.T17_displaced_per_million.baseline_max) }}{% endif %};
в соседних колонках файла — {{ t17.n_cases_adjacent }}. Это кандидаты на ошибку переноса. Числа сами по себе не устанавливают ошибку, виновника или умысел.
Для подтверждения нужен протокол. Метод разработан после просмотра 2026 года, поэтому результат разведочный. Сила — меньший из двух показателей −log₁₀p (ноль у списка при
ожидании по профилю; счёт малого списка при его доле по стране).</p>
{% if t17_top|length %}
<div class="table-tools"><label>Поиск по таблице <input data-filter="t17-table" placeholder="населённый пункт, урна или буквы списка"></label></div>
<div class="table-wrap"><table class="data" id="t17-table" data-collapse><thead><tr><th class="txt">Населённый пункт</th><th class="num">Участок</th><th class="txt">Место голосования</th><th class="txt">Записано у</th><th class="num">Голосов</th><th class="txt">Полагалось</th><th class="num">Ожидание по соседям</th><th>Соседи</th><th class="num">Ожидание по профилю</th><th>Соседние колонки</th><th class="num">Сила</th></tr></thead><tbody>
{% for _, r in t17_top.head(40).iterrows() %}<tr><td class="txt"><bdi>{{ r.locality_name }}</bdi></td><td class="num">{{ r.station }}</td><td class="txt"><bdi>{{ place(r) }}</bdi></td><td class="txt"><bdi>{{ label(r.fringe) }}</bdi></td><td class="num">{{ r.votes }}</td><td class="txt"><bdi>{{ label(r.donor) }}</bdi></td><td class="num">{{ num(r.expected, 0) }}</td><td>{{ r.peer_level }} ({{ r.n_peers }})</td><td class="num">{{ num(r.expected_profile, 0) }}</td><td>{{ "да" if r.adjacent else "—" }}</td><td class="num">{{ num(r.score, 0) }}</td></tr>{% endfor %}</tbody></table></div>
{% endif %}</div>{% endif %}
</section>

<section class="sec" id="election">
<p class="kicker">04 · Выборы в целом</p><h2>Тесты уровня выборов</h2>
<p class="lede">Каждый показатель ниже сравнивается с его значениями на прошлых выборах; «в полосе» значит, что 2026 год не вышел за край того, что давали чистые по этому признаку выборы 2013–2022 годов.</p>

<div class="sub" id="t05"><h3>Целые проценты (T05)</h3>
{% if el.T05_turnout and t05.turnout %}
<div class="verdict">{% if 'превышает' in rounding_assessment %}<span class="chip chip-warn">избыток выше базы</span>{% elif 'не выделяет' in rounding_assessment %}<span class="chip chip-ok">в полосе 2013–2022</span>{% else %}<span class="chip chip-muted">нет калибровки</span>{% endif %}<span>{{ rounding_assessment }}</span></div>
<p class="prose">Участков с явкой в пределах ±0,05 п.п. от целого процента: {{ t05.turnout.observed }} при ожидаемых {{ "%.0f"|format(t05.turnout.expected) }} по биномиальному нулю
(z = {{ "%.2f"|format(t05.turnout.z) }}). В 2013–2022 годах тот же z лежал между {{ "%.2f"|format(el.T05_turnout.baseline_min) }} и {{ "%.2f"|format(el.T05_turnout.baseline_max) }}
(среднее {{ "%.2f"|format(el.T05_turnout.baseline_mean) }}, разброс {{ "%.2f"|format(el.T05_turnout.baseline_sd) }}); 2026 год отстоит от среднего базы на
{{ "%.1f"|format(el.T05_turnout.baseline_z) }} её разброса. Для доли лидера: z = {{ "%.2f"|format(t05.leader_share.z) }} при базе
{{ "%.2f"|format(el.T05_leader_share.baseline_min) }}…{{ "%.2f"|format(el.T05_leader_share.baseline_max) }}.</p>
{% else %}<p class="prose">{{ rounding_assessment }}</p>{% endif %}</div>

{% if t06 and el.T06_leader_share %}{% set e6 = el.T06_leader_share %}{% set e6t = el.T06_turnout %}
<div class="sub" id="t06"><h3>Грубые дроби долей (T06)</h3>
<div class="verdict">{% if e6.z <= e6.baseline_max and e6t.z <= e6t.baseline_max %}<span class="chip chip-ok">избытка нет</span><span>участков, «подогнанных» под круглую дробь, не видно</span>{% else %}<span class="chip chip-warn">избыток выше базы</span><span>выше всего, что было в 2013–2022 годах — смотреть таблицу дробей в приложении</span>{% endif %}</div>
<p class="prose">Тот же вопрос для дробей, на которые целится рука (1/2, 2/3, 3/4, 3/5 и все k/d с d ≤ 10): доля лидера на участке в пределах ±0,05 п.п. от такой дроби —
{{ t06.leader_share.observed }} участков при ожидаемых {{ "%.0f"|format(t06.leader_share.expected) }} по биномиальному нулю (z = {{ "%.2f"|format(t06.leader_share.z) }};
в 2013–2022 годах z лежал между {{ "%.2f"|format(e6.baseline_min) }} и {{ "%.2f"|format(e6.baseline_max) }}); явка — {{ t06.turnout.observed }} при {{ "%.0f"|format(t06.turnout.expected) }}
(z = {{ "%.2f"|format(t06.turnout.z) }}; база {{ "%.2f"|format(e6t.baseline_min) }}…{{ "%.2f"|format(e6t.baseline_max) }}). Наибольший избыток у дроби {{ t06.top_excess_fraction_leader }}.
Опасен только избыток.</p></div>
{% endif %}

{% if t07 and el.T07_z_spike05 %}{% set e7 = el.T07_z_spike05 %}{% set v7 = el.T07_voters_z_spike05 %}
<div class="sub" id="t07"><h3>Последние цифры счётов (T07)</h3>
<div class="verdict">{% if e7.within_baseline_range and (not v7 or v7.within_baseline_range) %}<span class="chip chip-ok">в полосе 2003–2022</span><span>показатель не выходит за исторический диапазон; он не определяет долю нарушений</span>{% else %}<span class="chip chip-warn">вне полосы 2003–2022</span><span>требует проверки по населённым пунктам ниже</span>{% endif %}</div>
<p class="prose">Последние цифры всех счётов списков не ниже 100 на обычных участках: {{ fmt(t07.n) }} цифр. Доля цифр 0 и 5: {{ "%.1f"|format(t07.share_05*100) }} %
при ожидаемых 20 % (z = {{ "%.2f"|format(t07.z_05) }}); пик нулей и пятёрок над соседними цифрами: {{ "%+.2f"|format(t07.spike_05*100) }} п.п.
(z = {{ "%.2f"|format(t07.z_spike05) }}); расстояние распределения цифр от равномерного {{ "%.3f"|format(t07.tv) }} (описательная мера отличия от равномерного распределения). В 2003–2022 годах z пика лежал между {{ "%.2f"|format(e7.baseline_min) }} и {{ "%.2f"|format(e7.baseline_max) }}
(среднее {{ "%.2f"|format(e7.baseline_mean) }}, разброс {{ "%.2f"|format(e7.baseline_sd) }}); {{ k }}-й Кнессет отстоит от среднего базы на {{ "%.1f"|format(e7.baseline_z) }} её разброса.
Последние цифры числа проголосовавших: z пика {{ "%.2f"|format(t07.voters_last_digit.z_spike05) }}{% if v7 %} при базе {{ "%.2f"|format(v7.baseline_min) }}…{{ "%.2f"|format(v7.baseline_max) }}{% endif %}.</p>
<div class="note"><p>Хи‑квадрат против равномерности здесь не используется как критерий: в Израиле большинство счётов ≥ 100 лежит в полосе 100–200, где число счётов быстро падает с величиной,
и цифра 0 во всех выборах с 2003 года чуть чаще цифры 9 по чисто арифметической причине. Пик над соседними цифрами к этому градиенту нечувствителен.</p></div>
{% if t07_top|length %}
<p class="prose">Населённые пункты, где пик 0/5 выше 99,9‑го процентиля базы или выше всего, что встречалось в 2003–2022 годах ({{ loc.watch }} / {{ loc.above_baseline_max }} из {{ loc.n }}):</p>
<div class="table-wrap"><table class="data" data-collapse><thead><tr><th class="txt">Населённый пункт</th><th>Сектор</th><th class="num">Цифр</th><th class="num">Доля 0/5, %</th><th class="num">Пик 0/5, п.п.</th><th class="num">z пика</th><th class="num">Процентиль базы</th></tr></thead><tbody>
{% for _, r in t07_top.head(25).iterrows() %}<tr><td class="txt"><bdi>{{ r.locality_name }}</bdi></td><td>{{ r.sector }}</td><td class="num">{{ r.T07_n }}</td><td class="num">{{ "%.1f"|format(r.T07_share_05*100) }}</td><td class="num">{{ "%+.1f"|format(r.T07_spike_05*100) }}</td><td class="num">{{ "%.1f"|format(r.T07_score) }}</td><td class="num">{{ "%.4f"|format(r.T07_score_pctl) }}</td></tr>{% endfor %}</tbody></table></div>
<div class="note"><p>Объяснения, которые нужно исключить до любого вывода: мало крупных счётов в населённом пункте (случайный пик), один‑два участка с круглыми числами (смотреть протоколы), совпадение.</p></div>
{% else %}<p class="prose">Ни один населённый пункт не вышел за полосу 2003–2022 годов по последним цифрам.</p>{% endif %}</div>
{% endif %}

{% if t13 and t13.n_small_scored and el.T13_t_turnout %}{% set s13 = el.T13_slope_turnout %}{% set s13l = el.T13_slope_leader %}
<div class="sub" id="t13"><h3>Малые участки против крупных того же населённого пункта (T13)</h3>
<div class="verdict">{% if s13.z >= s13.baseline_min and s13l.z >= s13l.baseline_min %}<span class="chip chip-ok">в полосе 2013–2022</span><span>признака «чем меньше участок, тем экстремальнее» нет</span>{% else %}<span class="chip chip-warn">наклон ниже базы</span><span>{% if s13.z < s13.baseline_min %}по явке{% endif %}{% if s13.z < s13.baseline_min and s13l.z < s13l.baseline_min %} и {% endif %}{% if s13l.z < s13l.baseline_min %}по доле местного лидера{% endif %} малые участки экстремальнее крупных сильнее обычного; при неполном подсчёте перепроверить на финальном файле (ещё не подсчитанные участки — не случайная выборка), затем смотреть адреса участков</span>{% endif %}</div>
<p class="prose">Там, где подделать протокол легче всего — на малых участках, — явка и доля победителя в подтасованных выборах систематически выше, чем на крупных
(Jiménez и др., 2017). Внутри населённого пункта наклон явки по размеру участка: {{ "%+.2f"|format(t13.slope_turnout_pp_per_doubling) }} п.п. на удвоение электората
(t = {{ "%+.1f"|format(t13.t_turnout) }}); в 2013–2022 годах наклон лежал между {{ "%+.2f"|format(s13.baseline_min) }} и {{ "%+.2f"|format(s13.baseline_max) }}.
Доля местного лидера: {{ "%+.2f"|format(t13.slope_leader_pp_per_doubling) }} п.п. на удвоение (t = {{ "%+.1f"|format(t13.t_leader) }}) при базе
{{ "%+.2f"|format(s13l.baseline_min) }}…{{ "%+.2f"|format(s13l.baseline_max) }}. Малых участков (до {{ t13.small_max }} избирателей) при трёх и более обычных (от {{ t13.large_min }}) в том же
населённом пункте: {{ t13.n_small_scored }} в {{ t13.n_localities }} населённых пунктах; их явка в среднем на {{ "%+.1f"|format(t13.mean_d_turnout_small*100) }} п.п.
отличается от крупных; участков с z &gt; 4 по явке: {{ t13.n_z_turnout_above_4 }}, по лидеру: {{ t13.n_z_leader_above_4 }}, по обоим выше 3: {{ t13.n_both_above_3 }}.
Признак подтасовки — отрицательный наклон (чем меньше участок, тем выше явка и доля лидера).</p>
{% if t13_top|length %}
<div class="table-wrap"><table class="data" data-collapse><thead><tr><th class="txt">Населённый пункт</th><th class="num">Участок</th><th>Сектор</th><th class="txt">Место голосования</th><th class="txt">Малый участок против крупных</th><th class="num">Процентиль базы (явка / лидер)</th></tr></thead><tbody>
{% for _, r in t13_top.head(20).iterrows() %}<tr><td class="txt"><bdi>{{ r.locality_name }}</bdi></td><td class="num">{{ r.station }}</td><td>{{ r.sector }}</td><td class="txt"><bdi>{{ place(r) }}</bdi></td><td class="txt">{{ r.T13_detail }}</td><td class="num">{{ num(r.T13_turnout_pctl, 4) }} / {{ num(r.T13_leader_pctl, 4) }}</td></tr>{% endfor %}</tbody></table></div>
{% endif %}
<div class="note"><p>Разведочный тест, в ярусах не участвует: малый участок внутри города почти всегда обслуживает особую публику (дом престарелых, отдалённый квартал,
религиозный район), и его отличие от крупных участков законно. Строки выше — повод посмотреть место голосования (колонка в таблице), не вывод.</p></div></div>
{% endif %}

<div class="sub" id="t15"><h3>Двойные конверты (T15)</h3>
{% if not t15 or t15.skipped %}<div class="verdict"><span class="chip chip-muted">в этом снимке не опубликованы</span></div>
<p class="prose">Двойные конверты (מעטפות חיצוניות — солдаты, дипломаты, заключённые, больные, члены комиссий) в этом снимке ещё не опубликованы:
ЦИК считает их централизованно после дня голосования. В 2013–2022 годах это было 5,6–9,7 % всех голосов и та часть подсчёта, где меньше всего наблюдателей;
тест сравнит состав этих голосов с участками и с прошлыми выборами и пачки конвертов между собой, как только они появятся в файле.</p>
{% else %}{% set e15 = el.T15_external_share %}{% set e15i = el.T15_invalid_ratio %}{% set e15g = el.T15_max_abs_gap_shift %}
<div class="verdict">{% if e15g %}{% if e15g.within_baseline_range %}<span class="chip chip-ok">сдвиг разрыва в полосе 2013–2022</span>{% else %}<span class="chip chip-warn">сдвиг разрыва вне полосы</span><span>смотреть таблицу</span>{% endif %}{% endif %}</div>
<p class="prose">Двойных конвертов: {{ fmt(t15.voters_external) }} голосов ({{ "%.1f"|format(t15.external_share*100) }} % всех{% if e15 %}; в 2013–2022 годах {{ "%.1f"|format(e15.baseline_min*100) }}–{{ "%.1f"|format(e15.baseline_max*100) }} %{% endif %})
в {{ t15.n_batches }} пачках по {{ fmt(t15.batch_voters_median) }} конвертов (медиана). Недействительных среди них {{ "%.2f"|format(t15.invalid_external*100) }} % против
{{ "%.2f"|format(t15.invalid_regular*100) }} % на участках (отношение {{ "%.2f"|format(t15.invalid_ratio) }}{% if e15i %}; база {{ "%.2f"|format(e15i.baseline_min) }}…{{ "%.2f"|format(e15i.baseline_max) }}{% endif %}).
Самый большой разрыв состава: {{ label(t15.largest_gap.party) }} {{ "%+.1f"|format(t15.largest_gap.gap_pp) }} п.п. среди двойных конвертов против участков — сам по себе разрыв нормален
(солдаты и дипломаты голосуют не как страна), сигнал — его скачок.{% if t15.previous_knesset %} Наибольший сдвиг разрыва против {{ t15.previous_knesset }}-го Кнессета:
{{ label(t15.max_abs_gap_shift_party) }}, {{ "%.1f"|format(t15.max_abs_gap_shift_pp) }} п.п.{% if e15g %} (в 2013–2022 годах наибольший сдвиг был {{ "%.1f"|format(e15g.baseline_min) }}…{{ "%.1f"|format(e15g.baseline_max) }} п.п.){% endif %}.{% endif %}
{% if "seats_moved" in t15 %} Мандатов, которые двойные конверты перемещают относительно подсчёта только по участкам: {{ t15.seats_moved }}{% if t15.seat_changes %} ({{ t15.seat_changes }}){% endif %}.{% endif %}
Пачек, отличающихся от остальных на |z| &gt; 4: {{ t15.n_batches_above_4 }}.</p>
{% if t15_parties|length %}
<div class="table-wrap"><table class="data"><thead><tr><th class="txt">Список</th><th class="num">Среди двойных, %</th><th class="num">На участках, %</th><th class="num">Разрыв, п.п.</th><th class="num">Разрыв в прошлый раз</th><th class="num">Сдвиг, п.п.</th></tr></thead><tbody>
{% for _, r in t15_parties.head(14).iterrows() %}<tr><td class="txt">{{ r.label }}</td><td class="num">{{ "%.1f"|format(r.share_ext*100) }}</td><td class="num">{{ "%.1f"|format(r.share_reg*100) }}</td><td class="num">{{ "%+.1f"|format(r.gap_pp) }}</td><td class="num">{{ num(r.gap_prev_pp, 1, true) }}</td><td class="num">{{ num(r.gap_shift_pp, 1, true) }}</td></tr>{% endfor %}</tbody></table></div>
{% endif %}{% endif %}</div>

<div class="sub" id="curves"><h3>Соседние урны (T09) и собственная история (T12)</h3>
<figure class="chart">{{ figs.t09|safe }}</figure>
<figure class="chart">{{ figs.t12|safe }}<figcaption>Кривые показывают, какая доля участков имеет оценку не ниже x; 2026 год (красно‑коричневая линия) читается на фоне четырёх предыдущих выборов (серые, чем светлее — тем старше). Хвост 2026 года
{% if t09.max_abs_z_quantiles %}(99‑й процентиль |z| = {{ "%.1f"|format(t09.max_abs_z_quantiles["0.99"]) }}){% endif %} сравним с 2019–2022.</figcaption></figure></div>

<div class="sub" id="t02"><h3>Отпечатки явка × доля (T02)</h3>
<p class="prose">Двумерные гистограммы участков по явке и доле ведущего списка сектора, одинаковые панели для пяти выборов (чем темнее клетка, тем больше участков). В Израиле корреляция явки и доли лидера
отрицательна или около нуля во всех секторах и во все годы; масса в углу «явка ≥ 90 % и доля ≥ 80 %» равна нулю везде.</p>
<figure class="chart">{{ figs.fp_jewish|safe }}</figure><figure class="chart">{{ figs.fp_arab|safe }}</figure><figure class="chart">{{ figs.fp_haredi|safe }}</figure><figure class="chart">{{ figs.fp_mixed|safe }}</figure></div>

{% if t03_rows %}
<div class="sub" id="t03"><h3>Параметрическая подгонка отпечатка (T03, модель Климека)</h3>
<p class="prose">Модель Климека и др. (2012) спрашивает, какая доля участков с «инкрементальным» вбросом (f<sub>i</sub>: часть неявившихся записана лидеру) и с «экстремальным»
(f<sub>e</sub>: почти все избиратели записаны явившимися и почти все — лидеру) воспроизвела бы наблюдаемый отпечаток. Подгонка по секторам, с лидером и разбросом сектора;
«улучшение» — насколько лучшая подгонка ближе к данным, чем чистая модель без искажений; диапазон — все точки сетки в 5 % от лучшей. Оценка считается идентифицированной,
если улучшение не меньше 10 % и диапазон f<sub>i</sub> не шире 0,3; иначе поверхность цели плоская и число ничего не значит. Полоса — значения того же сектора в 2013–2022 годах.</p>
<div class="table-wrap"><table class="data"><thead><tr><th>Сектор</th><th class="num">Участков</th><th class="txt">Лидер</th><th class="num">f<sub>i</sub> (диапазон)</th><th class="num">база f<sub>i</sub></th><th class="num">f<sub>e</sub> (диапазон)</th><th class="num">база f<sub>e</sub></th><th class="num">Улучшение</th><th class="num">«Добавленных» голосов</th><th class="txt">Статус</th></tr></thead><tbody>
{% for r in t03_rows %}<tr{% if r.mixture %} class="dim"{% endif %}><td>{{ r.sector }}</td><td class="num">{{ fmt(r.n) }}</td><td class="txt">{{ label(r.leader) }}</td><td class="num">{{ "%.2f"|format(r.f_i) }} ({{ "%.2f"|format(r.f_i_lo) }}–{{ "%.2f"|format(r.f_i_hi) }})</td>
<td class="num">{% if r.bi %}{{ "%.2f"|format(r.bi.baseline_min) }}–{{ "%.2f"|format(r.bi.baseline_max) }}{% else %}—{% endif %}</td><td class="num">{{ "%.2f"|format(r.f_e) }} ({{ "%.2f"|format(r.f_e_lo) }}–{{ "%.2f"|format(r.f_e_hi) }})</td>
<td class="num">{% if r.be %}{{ "%.2f"|format(r.be.baseline_min) }}–{{ "%.2f"|format(r.be.baseline_max) }}{% else %}—{% endif %}</td><td class="num">{{ "%.0f"|format(r.improvement*100) }} %</td><td class="num">{{ "%.1f"|format(r.added_share*100) }} %</td>
<td class="txt">{% if r.mixture %}смесь электоратов — модель принимает её за вброс, не интерпретируется{% elif r.identified %}идентифицировано{% elif r.f_i == 0 and r.f_e == 0 %}чистая модель достаточна{% else %}не идентифицировано (плоская цель){% endif %}</td></tr>{% endfor %}</tbody></table></div>
<div class="note"><p>Чтение: идентифицированное f<sub>i</sub> заметно выше полосы прошлых выборов в том же секторе — повод смотреть T09/T12 по этому сектору; f<sub>i</sub> = 0 при улучшении около нуля —
чистая модель и так описывает сектор. Страна в целом и смешанные города дают f<sub>i</sub> на краю сетки во всех выборах с 2009 года: это артефакт смеси, а не находка
(критика Мебейна, 2013). Модель видит только грубый вброс, сдвигающий участки на несколько разбросов сектора; тонкие локальные вбросы — дело T09 и T12.</p></div></div>
{% endif %}
</section>

<section class="sec" id="seats">
<p class="kicker">05 · Представительство</p><h2>Голоса в мандаты (T16)</h2>
{% if seats|length %}
<p class="lede">Порог {{ "%.2f"|format(t16.threshold*100) }} % = {{ fmt(t16.threshold_votes) }} голосов при {{ fmt(t16.valid_votes) }} действительных в снимке.
Голоса, не давшие ни одного мандата: {{ fmt(t16.wasted_votes) }} ({{ "%.1f"|format(t16.wasted_share*100) }} %).</p>
<p class="prose">Соглашения об излишках{% if not t16.agreements_verified %} (по сообщениям прессы, не сверены с ЦИК){% endif %}: {% for pair in t16.agreements %}<bdi>{{ label(pair[0]) }}</bdi> + <bdi>{{ label(pair[1]) }}</bdi>{% if not loop.last %}; {% endif %}{% else %}не учтены{% endfor %}. Без них распределение сдвинулось бы на {{ t16.seat_shift_vs_no_agreements }} мандат(а).</p>
<div class="table-wrap"><table class="data"><thead><tr><th class="txt">Список</th><th class="num">Голоса</th><th class="num">%</th><th class="num">Мандаты</th><th class="num">Голосов на мандат</th><th class="num">Запас до порога</th><th class="num">Без соглашений</th><th class="num">Порог 2 %</th><th class="num">Без порога</th></tr></thead><tbody>
{% for _, r in seats.iterrows() if r.votes > 2000 %}<tr{% if not r.seats %} class="dim"{% endif %}><td class="txt">{{ r.label }}</td><td class="num">{{ fmt(r.votes) }}</td><td class="num">{{ "%.2f"|format(r.share*100) }}</td><td class="num">{{ r.seats }}</td>
<td class="num">{{ fmt(r.votes_per_seat) if r.seats else "—" }}</td><td class="num">{{ fmt(r.margin_to_threshold_votes) }}</td><td class="num">{{ r.seats_no_agreements }}</td><td class="num">{{ r.seats_thr2 }}</td><td class="num">{{ r.seats_thr0 }}</td></tr>{% endfor %}</tbody></table></div>
<figure class="chart">{{ figs.seats|safe }}<figcaption>Расчёт по методу Бадера‑Офера; на файле 2022 года он воспроизводит официальное распределение мандатов точно (проверено тестом).</figcaption></figure>
{% endif %}
</section>

<section class="sec" id="power">
<p class="kicker">06 · Стенд</p><h2>Чувствительность тестов: стенд синтетических искажений</h2>
{% if power_rows %}
<p class="prose">В копию реальных выборов (Кнессет {{ power_rows[0].base }}) вписывается искажение известного типа на {{ "%.0f"|format(power_rows[0].fraction*100) }} % участков,
затем батарея прогоняется заново. Ячейка: доля изменённых участков, которые тест поднял выше уровня, не превышенного чистыми данными
(99,9‑й процентиль) / выше 99‑го процентиля. Это мощность отдельных оценок, не полного правила флага после поправок. Для T05 — сдвиг z. Интенсивность: для вброса — доля
«неявки», уходящая лидеру; для переброса — доля чужих голосов, уходящая лидеру.</p>
<div class="table-wrap"><table class="data"><thead><tr><th class="txt">Тип</th><th class="num">Интенс.</th><th class="num">Участков</th>{% for c in power_cols %}<th class="num">{{ c }}</th>{% endfor %}</tr></thead><tbody>
{% for r in power_rows %}<tr><td class="txt">{{ r.kind }}</td><td class="num">{{ r.intensity }}</td><td class="num">{{ r.n }}</td>{% for c in power_cols %}<td class="num">{{ r.get(c, "—") }}</td>{% endfor %}</tr>{% endfor %}</tbody></table></div>
<div class="note"><p>Стенд измеряет только перечисленные искусственные изменения на одной выбранной базе и при показанных настройках.
Он не измеряет полноту обнаружения реальных нарушений и не валидирует итоговое правило флага. Мощность T17 отдельно не измерена. Файл: <code>{{ power_file }}</code>.</p></div>
{% else %}<p>Стенд ещё не запускался.</p>{% endif %}
</section>

<section class="sec" id="limits">
<p class="kicker">07 · Границы</p><h2>Ограничения этого выпуска</h2>
<ul class="limits">
<li>Подсчёт неполный; позже добавляются двойные конверты (около 10 % голосов), которые публикуются отдельной строкой и анализируются отдельно. Ещё не подсчитанные участки — не случайная выборка.</li>
<li>Калибровка по 2019–2022 годам: четыре выборов — достаточно для масштаба, мало для редких событий; с 2003 года доступны тесты без ключа здания. Историческая обменность (что чистые прошлые выборы — верная база для нынешних) остаётся предположением.</li>
<li>Чувствительность полного правила флага (после поправок на множественность) не измерена; стенд оценивает отдельные показатели. Ноль флагов не устанавливает чистоту подсчёта.</li>
<li>T17 — разведочный тест, разработанный после просмотра данных 2026 года; его кандидаты — вопросы к протоколам, не установленные ошибки.</li>
<li>Сравнения городов с похожими (в читательском выпуске) дают описательные разрывы с полосой плацебо; это не доверительные интервалы влияния инцидентов.</li>
<li>Слой инцидентов (сообщения о краже агитации, запугивании, подкупе) и его связка с локальными отклонениями в этот выпуск не включены.</li>
</ul>
</section>

<section class="sec" id="appendix">
<p class="kicker">A · Открытое приложение</p><h2>Таблицы и источники</h2>
<p class="prose">Все таблицы отчёта лежат рядом в <code>tables/</code> в кодировке UTF‑8 (с BOM для Excel){% if has_places %}; в таблицах по участкам есть колонки <code>place_name</code>, <code>address</code>, <code>place_type</code> — место голосования по списку ЦИК{% endif %}.</p>
<div class="files">{% for f in table_files %}<code>tables/{{ f }}.csv</code>{% endfor %}</div>
<p class="prose">Воспроизведение: <code>uv run ef battery</code> → <code>uv run ef calibrate</code> → <code>uv run ef report</code> на том же файле (SHA‑256 выше); сборка отказывается смешивать версии данных и кода.</p>
<details><summary>Параметры калибровки и хэши входов</summary><pre style="font-size:12px;overflow:auto">{{ cal.get('params', {})|tojson(indent=2) if cal else '{}' }}</pre></details>
<div class="note" style="margin-top:18px"><p>Источники: ЦИК (media{{ k }}.bechirot.gov.il, expb.csv), ЦИК — «מידע לבוחר → רשימת מקומות הקלפי» (gov.il, Polling Station.xlsx и Rikuzim.xlsx,
состояние на 25.10.2026), data.gov.il (пакет votes-knesset, Кнессеты 16–25), ЦСБ (קובץ היישובים 2024). Методы: Klimek 2012; Kobak, Shpilkin, Pshenichnikov 2016; Cantú 2014; Beber &amp; Scacco 2012; Medzihorsky 2015; см. docs/research.</p></div>
</section>
</main>
</div>

<footer class="colophon">
<p>Версия данных: <code>{{ file }}</code>, снимок {{ snapshot_local }}, SHA‑256 <code>{{ sha }}</code>. Сборка {{ built }}, ElectionForensics {{ version }}. Все числа выше берутся из этого снимка и пересчитываются при каждом обновлении.</p>
<p>Исследование не устанавливает голосование отдельных людей. Статистическое отклонение, сообщение об инциденте и установленный факт нарушения имеют разный доказательный статус; этот отчёт относится только к первому.</p>
</footer>
</div>
<script>{{ js|safe }}</script>
</body></html>
"""
