"""Figure pack for social media: PNG, 1:1 and 4:5, Russian and Hebrew from one code path.

Public register: no test ids, no tier names. Every figure carries a footer with the data version
(snapshot time) and the source. Hebrew needs no manual reordering: matplotlib 3.10+ applies the
Unicode bidi algorithm itself.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.figure import Figure

from electionforensics.config import DATA_PROCESSED, REPORTS
from electionforensics.provenance import require_calibrated
from electionforensics.taxonomy import party_label

SIZES = {"1x1": (10.8, 10.8), "4x5": (10.8, 13.5)}
LANGS = ("ru", "he")
T: dict[str, dict[str, Any]] = {
    "ru": {
        "progress_title": "Подсчёт голосов: сколько участков уже в файле ЦИК",
        "progress_y": "участков в опубликованном файле", "progress_total": "всего участков",
        "seats_title": "Как голоса стали мандатами",
        "seats_law": "по закону (Бадер‑Офер)", "seats_prop": "строго пропорционально", "seats_nothr": "без порога 3,25 %",
        "wasted": "Голоса, не давшие ни одного мандата: {w} ({p:.1f} %)",
        "fp_title": "Явка и доля ведущего списка по участкам: {sector}",
        "fp_x": "явка на участке, %", "fp_y": "доля ведущего списка, %",
        "k": "Кнессет {k}",
        "footer": "Источник: ЦИК Израиля, файл результатов по участкам, снимок {ts}. Промежуточный подсчёт. Расчёт: ElectionForensics.",
        "sectors": {"jewish": "еврейские населённые пункты", "arab": "арабские населённые пункты", "haredi": "харедимные населённые пункты", "mixed": "смешанные города"},
    },
    "he": {
        "progress_title": "ספירת הקולות: כמה קלפיות כבר בקובץ ועדת הבחירות",
        "progress_y": "קלפיות בקובץ שפורסם", "progress_total": "סך הקלפיות",
        "seats_title": "איך הקולות הפכו למנדטים",
        "seats_law": "לפי החוק (בדר־עופר)", "seats_prop": "יחסי לחלוטין", "seats_nothr": "ללא אחוז חסימה",
        "wasted": "קולות שלא הניבו מנדט: {w} ({p:.1f}%)",
        "fp_title": "אחוז הצבעה ושיעור הרשימה המובילה לפי קלפי: {sector}",
        "fp_x": "אחוז הצבעה בקלפי", "fp_y": "שיעור הרשימה המובילה, %",
        "k": "הכנסת ה־{k}",
        "footer": "מקור: ועדת הבחירות המרכזית, קובץ תוצאות לפי קלפיות, תמונת מצב {ts}. ספירה חלקית. חישוב: ElectionForensics.",
        "sectors": {"jewish": "יישובים יהודיים", "arab": "יישובים ערביים", "haredi": "יישובים חרדיים", "mixed": "ערים מעורבות"},
    },
}


def _t(lang: str, key: str, **kw: Any) -> str:
    # matplotlib >= 3.10 applies the Unicode bidi algorithm itself (checked 2026-10-28 on 3.11.2:
    # pre-reordered text comes out mirrored), so strings are passed in logical order.
    s = str(T[lang][key])
    return s.format(**kw) if kw else s


def _label(lang: str, s: str) -> str:
    return s


def _footer(fig: Figure, lang: str, ts: str, note: str = "") -> None:
    text = _t(lang, "footer", ts=ts) + ("\n" + note if note else "")
    fig.text(0.5, 0.012, text, ha="center", va="bottom", fontsize=9, color="#555", wrap=True)


def _save(fig: Figure, out: Path, stem: str, lang: str, ratio: str) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    p = out / f"{stem}_{lang}_{ratio}.png"
    fig.savefig(p, dpi=150, facecolor=fig.get_facecolor())
    plt.close(fig)
    return p


def fig_progress(per_snapshot: list[dict[str, Any]], expected: int, ts: str, lang: str, ratio: str, out: Path) -> Path:
    fig, ax = plt.subplots(figsize=SIZES[ratio])
    x = [datetime.strptime(p["ts"], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC).astimezone(ZoneInfo("Asia/Jerusalem")) for p in per_snapshot]
    y = [p["stations"] for p in per_snapshot]
    xs = cast(Any, x)
    ax.plot(xs, y, marker="o", lw=3, color="#1f4e79")
    ax.axhline(expected, ls=":", color="#999")
    ax.text(xs[0], expected, f"  {_t(lang, 'progress_total')}: {expected:,}".replace(",", " "), va="bottom", fontsize=12, color="#666")
    ax.set_title(_t(lang, "progress_title"), fontsize=18, pad=16)
    ax.set_ylabel(_t(lang, "progress_y"), fontsize=13)
    ax.grid(alpha=0.3)
    fig.autofmt_xdate()
    _footer(fig, lang, ts)
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    return _save(fig, out, "count_progress", lang, ratio)


def fig_seats(units: pd.DataFrame, knesset: int, wasted: int, wasted_share: float, ts: str, lang: str, ratio: str, out: Path,
              agreements_verified: bool = True) -> Path:
    d = units[units["seats"] > 0].sort_values("votes", ascending=False)
    labels = [_label(lang, party_label(knesset, p, lang)) for p in d["party"]]
    fig, ax = plt.subplots(figsize=SIZES[ratio])
    xs = np.arange(len(d))
    w = 0.27
    ax.bar(xs - w, d["seats"], w, label=_t(lang, "seats_law"), color="#1f4e79")
    ax.bar(xs, d["proportional_seats"], w, label=_t(lang, "seats_prop"), color="#8fb3d9")
    ax.bar(xs + w, d["seats_thr0"], w, label=_t(lang, "seats_nothr"), color="#d9a441")
    ax.set_xticks(xs)
    ax.set_xticklabels(labels, rotation=35, ha="right", fontsize=11)
    ax.set_title(_t(lang, "seats_title"), fontsize=18, pad=16)
    ax.legend(fontsize=11, frameon=False)
    ax.grid(axis="y", alpha=0.3)
    ax.text(0.01, 0.97, _t(lang, "wasted", w=f"{wasted:,}".replace(",", " "), p=wasted_share * 100), transform=ax.transAxes,
            fontsize=12, va="top", color="#444")
    note = "" if agreements_verified else ("Мандаты — расчётный сценарий. Соглашения об излишках не проверены." if lang == "ru"
                                            else "חלוקת המנדטים היא תרחיש חישובי. הסכמי העודפים לא אומתו.")
    _footer(fig, lang, ts, note)
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    return _save(fig, out, "seats", lang, ratio)


def fig_fingerprint(hist_now: pd.DataFrame, hist_prev: pd.DataFrame, sector: str, k_now: int, k_prev: int,
                    ts: str, lang: str, ratio: str, out: Path) -> Path:
    fig, axes = plt.subplots(1, 2, figsize=SIZES[ratio], sharey=True)
    for ax, h, k in ((axes[0], hist_prev, k_prev), (axes[1], hist_now, k_now)):
        d = h[h["sector"] == sector]
        z = d[[c for c in d.columns if c.startswith("y")]].to_numpy().T
        ax.imshow(np.log1p(z), origin="lower", extent=(0, 100, 0, 100), cmap="viridis", aspect="auto")
        ax.set_title(_t(lang, "k", k=k), fontsize=15)
        ax.set_xlabel(_t(lang, "fp_x"), fontsize=12)
    axes[0].set_ylabel(_t(lang, "fp_y"), fontsize=12)
    fig.suptitle(_t(lang, "fp_title", sector=T[lang]["sectors"].get(sector, sector)), fontsize=17)
    _footer(fig, lang, ts)
    fig.tight_layout(rect=(0, 0.04, 1, 0.95))
    return _save(fig, out, f"fingerprint_{sector}", lang, ratio)


def fig_candidates(candidates: pd.DataFrame, ts: str, lang: str, ratio: str, out: Path) -> Path:
    d = candidates[candidates["case"]].groupby("donor")["votes"].sum().sort_values()
    fig, ax = plt.subplots(figsize=SIZES[ratio])
    fig.set_facecolor("#f6f3eb")
    ax.set_facecolor("#f6f3eb")
    labels = [party_label(26, str(p), lang) for p in d.index]
    ax.barh(labels, d.to_numpy(dtype=float), color="#bb593c", height=0.55)
    ax.set_xlim(0, max(float(d.max()) * 1.3, 1.0) if len(d) else 1.0)
    for y, value in enumerate(d):
        ax.text(float(value) + 8, y, str(int(value)), va="center", fontsize=17)
    title = "Голоса для сверки с протоколом" if lang == "ru" else "קולות לבדיקת התאמה לפרוטוקול"
    ax.set_title(title, fontsize=22, loc="left", pad=40, color="#19333a")
    ax.set_xlabel("Голоса в предполагаемом переносе" if lang == "ru" else "קולות בהעברה המשוערת", fontsize=14)
    ax.tick_params(axis="y", labelsize=13)
    ax.spines[["top", "right", "left"]].set_visible(False)
    note = ("Расчётная версия. Ошибка и виновник не установлены." if lang == "ru"
            else "השערה חישובית. הטעות וזהות האחראי לא הוכחו.")
    _footer(fig, lang, ts, note)
    fig.tight_layout(rect=(0, 0.07, 1, 0.94))
    return _save(fig, out, "protocol_check", lang, ratio)


def build(knesset: int = 26, out: Path | None = None) -> list[Path]:
    source = require_calibrated(knesset)
    d = DATA_PROCESSED / f"knesset{knesset}"
    t14 = json.loads((d / "T14.json").read_text(encoding="utf-8"))["summary"]
    t16 = json.loads((d / "T16.json").read_text(encoding="utf-8"))["summary"]
    ts_raw = (t14.get("_source") or {}).get("snapshot_ts") or ""
    ts = datetime.strptime(ts_raw, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC).astimezone(ZoneInfo("Asia/Jerusalem")).strftime("%d.%m.%Y %H:%M") if ts_raw else "—"
    ts += "  SHA-256 " + source["sha256"][:12]
    out = out or (REPORTS / "figpack" / (ts_raw or "final"))
    seats = pd.read_parquet(d / "T16.parquet")
    hist_now = pd.read_parquet(d / "T02_hist.parquet")
    hist_prev = pd.read_parquet(DATA_PROCESSED / f"knesset{knesset - 1}" / "T02_hist.parquet")
    candidates = pd.read_parquet(d / "T17.parquet")
    paths = []
    for lang in LANGS:
        for ratio in SIZES:
            paths.append(fig_candidates(candidates, ts, lang, ratio, out))
            if t14.get("per_snapshot"):
                paths.append(fig_progress(t14["per_snapshot"], 12_018, ts, lang, ratio, out))
            paths.append(fig_seats(seats, knesset, int(t16["wasted_votes"]), float(t16["wasted_share"]), ts, lang, ratio, out,
                                   agreements_verified=bool(t16.get("agreements_verified"))))
            for sector in ("jewish", "arab", "haredi"):
                paths.append(fig_fingerprint(hist_now, hist_prev, sector, knesset, knesset - 1, ts, lang, ratio, out))
    return paths
