"""A reader-facing issue and a self-contained reproducibility appendix, built from pinned results."""

from __future__ import annotations

import json
import shutil
import zipfile
from dataclasses import replace
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.graph_objects as go
from jinja2 import Environment
from plotly.offline import get_plotlyjs

from electionforensics import __version__
from electionforensics.battery.t16_representation import allocate, load_agreements
from electionforensics.config import DATA_PROCESSED, REPORTS, ROOT, THRESHOLD_BY_KNESSET
from electionforensics.ingest.cec import ElectionData
from electionforensics.ingest.places import load_places
from electionforensics.link.city_effect import city_effect
from electionforensics.link.city_effect import save as save_city
from electionforensics.present.report import _fmt_int, _ts_local
from electionforensics.present.report import build as editorial_report
from electionforensics.provenance import pinned_election, require_calibrated, sha256
from electionforensics.taxonomy import party_label


def correction_scenario(data: ElectionData, candidates: pd.DataFrame) -> dict[str, Any]:
    """A labelled sensitivity scenario; never edit the evidence or assert a true correction."""
    altered = data.stations.copy(deep=True)
    for _, row in candidates.iterrows():
        mask = (altered["locality_code"] == row["locality_code"]) & (altered["station"] == row["station"])
        if mask.sum() != 1:
            raise ValueError("candidate must map to exactly one raw box")
        donor, fringe, votes = f"v_{row.donor}", f"v_{row.fringe}", int(row.votes)
        if int(altered.loc[mask, fringe].iloc[0]) < votes:
            raise ValueError("overlapping candidate transfers exceed the recorded cell")
        altered.loc[mask, fringe] -= votes
        altered.loc[mask, donor] += votes
    agreements, verified = load_agreements(data.knesset)
    scenarios: dict[str, Any] = {}
    for name, pairs in (("listed_agreements", agreements), ("no_agreements", [])):
        before = allocate(data, THRESHOLD_BY_KNESSET[data.knesset], pairs)["seats"]
        after = allocate(replace(data, stations=altered), THRESHOLD_BY_KNESSET[data.knesset], pairs)["seats"]
        scenarios[name] = {p: after.get(p, 0) - before.get(p, 0) for p in set(before) | set(after)
                           if after.get(p, 0) != before.get(p, 0)}
    return {"seat_changes": scenarios, "agreements_verified": verified, "hypothetical": True}


def source_bundle(out: Path) -> None:
    """Only analysis code and public statistical inputs. No witness files, captures or audit conversations."""
    paths = list((ROOT / "src").rglob("*.py"))
    paths += [p for p in (ROOT / "taxonomy").rglob("*") if p.is_file()]
    paths += [p for p in (ROOT / "data" / "raw").rglob("*") if p.is_file() and p.suffix != ".tmp"]
    paths += [ROOT / n for n in ("pyproject.toml", "uv.lock", "LICENSE", "CREDITS.md")]
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for p in sorted(paths):
            info = zipfile.ZipInfo(p.relative_to(ROOT).as_posix(), date_time=(2026, 10, 29, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, p.read_bytes())
        archive.writestr("README.md", "# Reproduce the count analysis\n\nuv sync --extra dev\nuv run ef build --baseline\n\n"
                         "The issue is a draft. Statistical inputs are archived files attributed to the CEC; raw HTML error responses are preserved and skipped. "
                         "Witness evidence, incident seeds, source captures and private conversations are deliberately outside this statistical appendix.\n")


def seal_issue(out: Path) -> None:
    manifest = {p.relative_to(out).as_posix(): sha256(p) for p in sorted(out.rglob("*")) if p.is_file() and p.name != "MANIFEST.json"}
    (out / "MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def build(out: Path | None = None, include_bundle: bool = True) -> Path:
    source = require_calibrated(26)
    data = pinned_election(26, source)
    d = DATA_PROCESSED / "knesset26"
    summaries = {f"T{i:02}": json.loads((d / f"T{i:02}.json").read_text(encoding="utf-8"))["summary"] for i in range(1, 18)}
    cal = json.loads((d / "CAL.json").read_text(encoding="utf-8"))
    candidates = pd.read_parquet(d / "T17.parquet")
    candidates = candidates[candidates["case"]].sort_values("votes", ascending=False).copy()
    out = out or REPORTS / "issues" / f"{_ts_local(source['snapshot_ts'])[:10]}_{source['snapshot_ts']}"
    appendix = out / "appendix"
    appendix.mkdir(parents=True, exist_ok=True)
    data.stations.to_csv(appendix / "stations.csv", index=False, encoding="utf-8-sig")
    candidates.to_csv(appendix / "candidates.csv", index=False, encoding="utf-8-sig")
    shutil.copyfile(data.source, appendix / source["file"])
    editorial = editorial_report(26, out / "technical")
    places = load_places(26)
    expected = int(places["n_boxes"].sum())
    scenario = correction_scenario(data, candidates)
    cities = []
    for code in (240, 494):
        ce = city_effect(code, 25, 26, snapshot=data.source)
        save_city(ce, appendix / f"city-{code}")
        cities.append({"code": code, "name": ce.locality_name, "outcomes": ce.outcomes,
                       "coverage": ce.params["target_completeness"], "donor_pool": ce.params["donor_pool"]})
    groups = candidates.groupby("donor")["votes"].sum().sort_values(ascending=True)
    candidate_chart = go.Figure(go.Bar(x=groups.values, y=[str(p) for p in groups.index], orientation="h", marker_color="#bb593c",
                                     text=groups.values, textposition="outside", cliponaxis=False))  # EF-012: the top label must not be cut
    candidate_chart.update_layout(height=360, margin={"l": 220, "r": 60, "t": 15, "b": 45}, paper_bgcolor="#f6f3eb",
                                  plot_bgcolor="#f6f3eb", font={"family": "Arial", "size": 15, "color": "#19333a"},
                                  xaxis={"title": "Голоса в проверяемой версии переноса", "range": [0, float(groups.max()) * 1.28 if len(groups) else 1]},
                                  yaxis_title=None)
    timeline = summaries["T14"].get("per_snapshot", [])
    progress = go.Figure(go.Scatter(x=[_ts_local(r["ts"])[:16] for r in timeline], y=[r["stations"] for r in timeline],
                                   mode="lines+markers", line={"color": "#2c6267", "width": 3}))
    progress.update_layout(height=310, margin={"l": 70, "r": 30, "t": 20, "b": 75}, paper_bgcolor="#f6f3eb", plot_bgcolor="#f6f3eb",
                           font={"family": "Arial", "size": 14, "color": "#19333a"}, yaxis_title="Урн в файле", xaxis_title="Время Израиля")
    by_donor = {str(p): int(v) for p, v in groups.items()}
    facts = {"version": __version__, "snapshot": source["snapshot_ts"], "as_of": _ts_local(source["snapshot_ts"]),
             "source_file": source["file"], "source_sha256": source["sha256"], "stations": len(data.regular), "expected": expected,
             "valid_votes": int(data.stations["valid"].sum()), "arithmetic_flags": summaries["T01"]["n_flagged"],
             "candidate_count": len(candidates), "candidate_votes": int(candidates["votes"].sum()), "by_donor": by_donor,
             "tiers": cal["tiers"], "scenario": scenario, "cities": cities,
             "candidates": json.loads(candidates.to_json(orient="records", force_ascii=False)),
             "baseline_t17": {str(k): {name: value for name, value in json.loads((DATA_PROCESSED / f"knesset{k}" / "T17.json").read_text(encoding="utf-8"))["summary"].items() if name != "_source"} for k in range(16, 26)},
             "count_progress": timeline, "t05": summaries["T05"]}
    (appendix / "facts.json").write_text(json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8")
    provenance = {"source": {k: v for k, v in source.items() if k != "path"}, "version": __version__,
                  "battery_artifacts": cal["params"]["inputs"]["artifacts"], "limitations": cal["params"]["limitations"]}
    (appendix / "provenance.json").write_text(json.dumps(provenance, ensure_ascii=False, indent=2), encoding="utf-8")
    methods = "\n\n".join((ROOT / "docs" / "methods" / n).read_text(encoding="utf-8")
                             for n in ("calibration-audit.md", "reproducibility.md", "evidence-grading.md"))
    (appendix / "methods.md").write_text(methods, encoding="utf-8")
    if (ROOT / "docs/audit/2026-10-29.md").exists():
        shutil.copyfile(ROOT / "docs/audit/2026-10-29.md", appendix / "audit.md")
    if include_bundle:
        source_bundle(appendix / "reproduce.zip")
    ctx = {**facts, "f": facts, "fmt": _fmt_int, "label": lambda p: party_label(26, p), "candidates": candidates,
           "chart_letters": [str(p) for p in groups.index], "chart_labels": [party_label(26, p) for p in groups.index],
           "candidate_chart": candidate_chart.to_html(full_html=False, include_plotlyjs=False, div_id="candidate-chart", config={"displayModeBar": False, "responsive": True}),
           "progress_chart": progress.to_html(full_html=False, include_plotlyjs=False, div_id="progress-chart", config={"displayModeBar": False, "responsive": True}),
           "plotly_js": get_plotlyjs(), "include_bundle": include_bundle,
           "seat_changes": any(scenario["seat_changes"].values()), "editorial": editorial.relative_to(out).as_posix()}
    (out / "index.html").write_text(Environment(autoescape=True).from_string(TEMPLATE).render(**ctx), encoding="utf-8")
    (out / "posts-ru.md").write_text(
        f"# Черновик короткого сообщения\n\nПроверил архив подсчёта выборов в Кнессет. В снимке {facts['as_of']} нашёл "
        f"{facts['candidate_count']} сочетаний чисел, похожих на перенос голосов в графу другого списка. "
        f"В этих версиях задействованы {facts['candidate_votes']} голоса. Это список для сверки с протоколами. "
        "По одним числам нельзя установить, что именно произошло или кто за это отвечает. "
        "Полный разбор, исходный файл, ограничения и код собраны в приложении. Подсчёт ещё неполный.\n",
        encoding="utf-8")
    seal_issue(out)
    return out / "index.html"


TEMPLATE = r"""<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Выборы под проверкой — Кнессет 2026</title><script>{{ plotly_js|safe }}</script>
<style>
:root{--paper:#f6f3eb;--ink:#19333a;--accent:#bb593c;--muted:#52666a}*{box-sizing:border-box}html{scroll-behavior:smooth}
body{margin:0;background:var(--paper);color:var(--ink);font:18px/1.65 Arial,sans-serif}a{color:inherit;text-decoration-thickness:1px;text-underline-offset:4px}
header,main,footer{max-width:1120px;margin:auto;padding:0 6vw}nav{display:flex;justify-content:space-between;gap:20px;padding:26px 0;border-bottom:1px solid #b8c0b7;font-size:14px}
.kicker{font-size:13px;letter-spacing:.12em;text-transform:uppercase;color:var(--accent)}h1,h2{font-family:Georgia,serif;font-weight:normal;line-height:1.08;letter-spacing:-.025em}
h1{font-size:clamp(54px,8vw,102px);max-width:920px;margin:44px 0 24px}h2{font-size:clamp(32px,4vw,51px);max-width:900px;margin:14px 0 27px}h3{font-size:23px;line-height:1.3}
.lead{font-size:clamp(21px,2.5vw,29px);max-width:820px;line-height:1.45}.meta,.caption{font-size:14px;color:var(--muted)}.hero{padding:52px 0 36px}.hero .meta{margin-top:32px}
section{border-top:1px solid #b8c0b7;padding:47px 0 40px}.prose{max-width:760px}p{margin:0 0 22px}.finding{font-size:clamp(70px,12vw,145px);line-height:1;color:var(--accent);font-family:Georgia,serif;margin:25px 0 16px}
.pull{font-size:26px;line-height:1.45;border-left:3px solid var(--accent);padding-left:25px;margin:35px 0;max-width:850px}
.table-wrap{overflow-x:auto}table{width:100%;border-collapse:collapse;font-size:15px;margin:20px 0}th{text-align:left;font-weight:normal;color:var(--muted)}td,th{padding:13px 12px 13px 0;border-bottom:1px solid #d3d7cc;vertical-align:top}bdi{unicode-bidi:isolate}code{overflow-wrap:anywhere;font-size:12px}
details{border-bottom:1px solid #d3d7cc;padding:16px 0}summary{cursor:pointer;font-weight:bold}details p{margin-top:16px}input{border:0;border-bottom:1px solid var(--ink);background:transparent;padding:12px 0;font:inherit;width:min(100%,480px);border-radius:0;color:inherit}
.links{display:flex;flex-wrap:wrap;gap:16px 26px;font-size:16px;margin:25px 0}footer{padding-top:30px;padding-bottom:55px;font-size:13px;border-top:1px solid #b8c0b7}.draft{color:var(--accent)}.no-print{display:block}
@media(max-width:650px){body{font-size:17px}header,main,footer{padding-left:6vw;padding-right:6vw}nav{font-size:12px}.hero{padding-top:25px}section{padding:32px 0}.pull{font-size:22px}.table-wrap{margin-right:-5vw}}
@media print{body{background:white;font-size:11pt}nav,.no-print{display:none}header,main,footer{max-width:none;padding:0}h1{font-size:46pt}h2{font-size:28pt}section{break-inside:auto}table{font-size:9pt}a{text-decoration:none}.finding{font-size:70pt}.js-plotly-plot{break-inside:avoid}}
</style></head><body><header><nav><span>ELECTION FORENSICS · בחירות</span><a href="#appendix">Данные и методика</a></nav>
<div class="hero"><div class="kicker">Проверяемый разбор · 26-й Кнессет</div><h1>Выборы<br>под проверкой</h1>
<p class="lead">В опубликованных числах есть места, где голоса крупного списка могли попасть в графу малого. Их можно перечислить, пересчитать и проверить по протоколам.</p>
<p class="meta">Архивный снимок {{ as_of }} · {{ fmt(stations) }} обычных урн · Предварительный подсчёт<br><span class="draft">Редакционный черновик. Автор принимает решение о публикации.</span></p></div></header>
<main><section id="finding"><div class="kicker">01 · Конкретная находка</div><h2>Числа, которые стоит сверить с протоколами</h2>
<div class="finding">{{ candidate_count }}</div><p class="lead">сочетаний «ноль у крупного списка и необычно много голосов у малого»</p>
<div class="prose"><p>В проверяемых версиях переноса участвуют <strong>{{ fmt(candidate_votes) }} голоса</strong>. Алгоритм ищет места, где крупный список не получил ни одного голоса, хотя похожие участки дают ему заметную поддержку. Одновременно малый список получает число голосов, близкое к этому ожиданию.</p>
<p>Так может выглядеть ошибка переписывания. Так же может выглядеть редкое, но реальное голосование другой группы избирателей. Поэтому каждая строка ниже — вопрос к протоколу.</p></div>
{{ candidate_chart|safe }}<p class="caption">Группировка по крупному списку, которому могли принадлежать голоса. На узком экране показаны буквы списков, полные названия — в таблице ниже. Это расчётная версия. Сумма не означает установленное число неверно учтённых голосов.</p>
{% if candidates|length %}{% set top=candidates.iloc[0] %}<p class="pull"><bdi>{{ top.locality_name }}</bdi>, урна {{ top.station }}: в графе <bdi>{{ top.fringe }}</bdi> стоят {{ top.votes }} голосов, а у <bdi>{{ top.donor }}</bdi> — ноль. Версия для проверки: счёт попал в другую графу.</p>{% endif %}
<p class="prose">Метод возник после изучения этих выборов. На старых финальных файлах он встречает похожие сочетания с другой частотой. Промежуточный файл содержит ещё не исправленные записи, поэтому сравнение с финалами не измеряет рост нарушений.</p>
<details><summary>Все строки для проверки</summary><label for="search">Населённый пункт, урна или буквы списка</label><br><input id="search" placeholder="Например, קלנסווה" autocomplete="off"><div class="table-wrap"><table id="candidates"><thead><tr><th>Населённый пункт</th><th>Урна</th><th>В файле</th><th>Голосов</th><th>Версия для сверки</th></tr></thead><tbody>
{% for _,r in candidates.iterrows() %}<tr><td><bdi>{{ r.locality_name }}</bdi></td><td>{{ r.station }}</td><td><bdi>{{ r.fringe }}</bdi></td><td>{{ r.votes }}</td><td><bdi>{{ label(r.donor) }} ({{ r.donor }})</bdi></td></tr>{% endfor %}</tbody></table></div><p class="caption">Ожидания по похожим урнам и все расчётные поля — в <a href="appendix/candidates.csv">CSV-приложении</a>. Алгоритм не заменяет сверку скана протокола.</p></details>
</section><section><div class="kicker">02 · Масштаб</div><h2>Что меняется в расчёте мандатов</h2><div class="prose">
{% if not seat_changes %}<p class="lead">Если условно выполнить все {{ candidate_count }} предполагаемых переносов, распределение мандатов в двух проверенных сценариях сохраняется.</p>{% else %}<p class="lead">Предполагаемые переносы меняют расчёт мандатов. Подробные разницы по сценариям сохранены в приложении.</p>{% endif %}
<p>Первый сценарий использует записанные в проекте соглашения об излишках голосов, второй считает без соглашений. Для 2026 года сами соглашения ещё не подтверждены первичным документом. Расчёт относится только к текущему неполному файлу, а не к окончательным мандатам.</p>
<p>Даже небольшая ошибка заслуживает исправления. Масштаб её влияния на состав Кнессета — отдельный вопрос.</p></div></section>
<section><div class="kicker">03 · Граница статистики</div><h2>Почему необычный участок ещё не означает нарушения</h2><div class="prose">
<p>Соседние урны одного здания могут обслуживать разные группы избирателей. Номер участка может сохраниться после изменения его границ. Такие различия дают сильные численные отклонения без ошибки подсчёта.</p>
<p>Система сопоставляет оценки с предыдущими выборами внутри того же сектора и учитывает множество одновременных проверок. В этом выпуске <strong>{{ tiers.get('flag',0) }} участков</strong> проходят итоговое правило статистического отбора. Ещё {{ tiers.get('watch',0) }} строк остаются в поисковом списке.</p>
<p>Исторические и текущие данные могут различаться по составу. Чувствительность полного правила отбора ещё не измерена. Поэтому отсутствие прошедших отбор участков не доказывает отсутствия нарушений. Кандидаты на перенос графы выше проходят отдельную разведочную проверку.</p></div></section>
<section><div class="kicker">04 · Инцидент и результат</div><h2>Можно ли увидеть влияние снятия плакатов или давления</h2><div class="prose"><p>Голосование города можно сравнить с похожими населёнными пунктами. Это показывает разрыв, который требует объяснения. Оно не отделяет влияние одного инцидента от миграции, изменения партий, общего политического сдвига и других событий.</p></div>
<div class="table-wrap"><table><tr><th>Город</th><th>Изменение явки с 2022</th><th>Разрыв с похожими городами</th><th>Разброс разрывов в контролях*</th></tr>
{% for c in cities %}{% set r=c.outcomes.turnout %}<tr><td><bdi>{{ c.name }}</bdi></td><td>{{ '%+.1f'|format(r.target_delta*100) }} п.п.</td><td>{{ '%+.1f'|format(r.effect*100) }} п.п.</td><td>{{ '%+.1f'|format(r.band_lo*100) }}…{{ '%+.1f'|format(r.band_hi*100) }} п.п.</td></tr>{% endfor %}</table></div>
<p class="caption">* Центральные 95 % плацебо-разрывов контрольных городов. Это не доверительный интервал эффекта инцидента. Пул сравнения: {% for c in cities %}<bdi>{{ c.name }}</bdi> — {{ c.donor_pool }} населённых пунктов, покрытие реестра {{ '%.1f'|format(c.coverage*100) }} %; {% endfor %}.</p>
<p class="prose">Вывод о самом событии, его организованности, виновнике и влиянии требует разных подтверждений. Жалоба на шум устанавливает наличие жалобы. Показания фиксируют наблюдения свидетеля. Причину и политическую координацию нужно устанавливать отдельно. Закрытые показания и фотографии в этот выпуск не входят.</p></section>
<section><div class="kicker">05 · Подсчёт продолжается</div><h2>{{ fmt(stations) }} урн из {{ fmt(expected) }} в предвыборном реестре</h2>
{{ progress_chart|safe }}<div class="prose"><p>В обычных урнах этого файла арифметические и формальные проверки отметили {{ arithmetic_flags }} строк. Это проверка сумм и формальных ограничений, не подтверждение правильности каждой цифры.</p>
<p>Двойные конверты в выбранном файле отсутствуют. Результаты могут меняться при дополнении и исправлении подсчёта. Время снимка показывает момент сохранения файла проектом.</p>
<p>Этот выпуск анализирует сохранённые проектом файлы, отнесённые к ЦИК. Повторная загрузка текущего CSV во время аудита столкнулась с защитой сайта, поэтому внешнее подтверждение актуальных байтов отдельно не завершено.</p></div></section>
<section id="appendix"><div class="kicker">Приложение</div><h2>Каждое число можно проверить</h2><p class="prose">Материал сохраняет исходный файл, таблицы, точные версии и расчётные сценарии. Технический отчёт показывает все диагностические методы и их ограничения.</p>
<div class="links"><a href="appendix/{{ source_file }}">Исходный CSV</a><a href="appendix/stations.csv">Нормализованные данные</a><a href="appendix/candidates.csv">Строки для проверки</a><a href="appendix/methods.md">Методика</a><a href="{{ editorial }}">Технический отчёт</a><a href="appendix/provenance.json">Хэши и версии</a>{% if include_bundle %}<a href="appendix/reproduce.zip">Код и статистические входные файлы</a>{% endif %}</div>
<p class="caption">SHA-256 исходного файла<br><code>{{ source_sha256 }}</code><br>ElectionForensics {{ version }} · Данные по состоянию на {{ as_of }}</p></section></main>
<footer>Исследование не устанавливает голосование отдельных людей. Сообщения об инцидентах, статистические отклонения и установленный факт нарушения имеют разный доказательный статус.</footer>
<script>
const letters={{ chart_letters|tojson }}, labels={{ chart_labels|tojson }};
function reflowCharts(){const small=window.innerWidth<600;
Plotly.relayout('candidate-chart', {'yaxis.tickmode':'array','yaxis.tickvals':letters,'yaxis.ticktext':small?letters:labels,
 'margin.l':small?55:220,'margin.r':45,'xaxis.title.text':small?'Голосов':'Голоса в проверяемой версии переноса','xaxis.nticks':small?3:6,'font.size':small?13:15});
Plotly.relayout('progress-chart',{'margin.l':small?50:70,'margin.r':20,'xaxis.nticks':small?3:6,'xaxis.tickformat':'%d.%m<br>%H:%M','font.size':small?12:14});}
reflowCharts();window.addEventListener('resize',reflowCharts);
document.getElementById('search')?.addEventListener('input',function(){const q=this.value.toLocaleLowerCase();document.querySelectorAll('#candidates tbody tr').forEach(r=>r.hidden=!r.textContent.toLocaleLowerCase().includes(q));});</script></body></html>"""
