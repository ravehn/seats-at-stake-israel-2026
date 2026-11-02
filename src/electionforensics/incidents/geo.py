"""Locality mentions in captured texts, and the first-pass "dose" table of reported events per city.

Hebrew localities are matched by name with the usual proclitic prefixes (ב, ל, מ, ו, ה, ש, כ) and
word boundaries; names come from the CBS file (population ≥ 10,000, or ≥ 4 letters and ≥ 5,000)
plus Russian aliases for the cities most often named in Russian-language media. Short names that
are also common words are excluded explicitly. The dose table counts captured candidate reports
per locality, overall and for poster/sign-related reports, by distinct source.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import pandas as pd

from electionforensics.config import ROOT
from electionforensics.ingest.cbs import load_cbs

CAPTURES = ROOT / "data" / "captures"
POSTER_RE = re.compile(r"שלט|שלטים|פלקט|כרז|מודעות|плакат|баннер|billboard|poster|sign", re.I)
HE_PREFIX = r"(?<![א-ת])(?:[בלמוהשכ]{0,2})"
STOP = {"אור", "גן", "חצור", "עין", "נוף", "כפר", "בית", "גבעה", "מעלה", "תל", "ים", "קדימה", "שדרות", "רמת", "אבן", "יהוד", "צור", "ניר", "שער", "שלום"}
#: City names that are also ordinary words (רחובות = streets, מודיעין = intelligence, מעלות = degrees,
#: נתיבות = paths, טירה = castle, אופקים = horizons): matched only bare, without a proclitic prefix.
HOMOGRAPHS = {"רחובות", "מודיעין", "מעלות", "נתיבות", "טירה", "אופקים", "ערד", "יבנה"}
HE_BARE = r"(?<![א-ת])"
RU_ALIASES = {
    "Йокнеам": 240, "Нетани": 7400, "Хайф": 4000, "Беэр-Шев": 9000, "Беэр Шев": 9000, "Ашдод": 70, "Ашкелон": 7100,
    "Лод": 7000, "Рамл": 8500, "Бат-Ям": 6200, "Бат Ям": 6200, "Холон": 6600, "Петах-Тикв": 7900, "Ришон": 8300,
    "Реховот": 8400, "Тель-Авив": 5000, "Иерусалим": 3000, "Кирьят-Ям": 9600, "Димон": 2200, "Тверия": 6700, "Тверии": 6700,
    "Назарет": 7300, "Рахат": 1161, "Калансу": 638, "Калансав": 638, "Тайбе": 2730, "Сахнин": 7500, "Акко": 7600, "Акк": 7600,
    "Эйлат": 2600, "Нагария": 9100, "Кармиэл": 1139, "Афул": 7700, "Хадер": 6500, "Бней-Брак": 6100, "Бней Брак": 6100,
    "Бейт-Шемеш": 2610, "Модиин-Илит": 3797, "Бейтар-Илит": 3780, "Эльад": 1309, "Нешер": 2500, "Кирьят-Моцкин": 8200,
    "Кирьят-Бялик": 9500, "Кирьят-Ата": 6800, "Кирьят-Гат": 2630, "Кирьят-Малахи": 1034, "Кирьят-Шмон": 2800, "Ор-Акив": 1020,
    "Тират-Кармель": 2100, "Мигдаль-ха-Эмек": 874, "Сдерот": 1031, "Нетивот": 246, "Офаким": 31, "Арад": 2560, "Раанан": 8700,
    "Герцли": 6400, "Кфар-Сав": 6900, "Ход-ха-Шарон": 9700, "Рамат-Ган": 8600, "Гиватаим": 6300, "Хол": 6600,
    "Ноф-ха-Галиль": 1061, "Умм-эль-Фахм": 2710, "Шфарам": 8800, "Сегев-Шалом": 1286, "Хур": 1303, "Тель-Шев": 1054, "Ярк": 502,
}


def _he_patterns() -> list[tuple[re.Pattern[str], int, str]]:
    cbs = load_cbs()
    pats: list[tuple[re.Pattern[str], int, str]] = []
    for name, code, pop in zip(cbs["cbs_name"], cbs["locality_code"], cbs["population"], strict=True):
        if pd.isna(code) or pd.isna(pop):
            continue
        n = str(name).strip()
        base = re.sub(r"\s*\(.*?\)\s*", " ", n).strip()
        if base in STOP or len(base) < 3:
            continue
        if not (int(pop) >= 10_000 or (int(pop) >= 5_000 and len(base) >= 4)):
            continue
        variants = {base, base.replace("-", " "), base.replace(" - ", "-"), base.replace("קריית", "קרית")}
        prefix = HE_BARE if base.split()[0] in HOMOGRAPHS else HE_PREFIX
        for v in variants:
            pats.append((re.compile(prefix + re.escape(v) + r"(?![א-ת])"), int(code), base))
    return pats


_PATTERNS: list[tuple[re.Pattern[str], int, str]] | None = None


def mentions(text: str) -> dict[int, str]:
    """Locality codes mentioned in `text` → the name matched."""
    global _PATTERNS
    if _PATTERNS is None:
        _PATTERNS = _he_patterns()
    found: dict[int, str] = {}
    for pat, code, name in _PATTERNS:
        if pat.search(text):
            found.setdefault(code, name)
    for alias, code in RU_ALIASES.items():
        if alias in text:
            found.setdefault(code, alias)
    return found


def load_captures(root: Path = CAPTURES) -> list[dict[str, Any]]:
    out = []
    for p in sorted(root.glob("*/*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            d["_path"] = str(p.relative_to(ROOT))
            out.append(d)
        except (OSError, json.JSONDecodeError):
            continue
    return out


def dose_table(captures: list[dict[str, Any]] | None = None) -> pd.DataFrame:
    captures = load_captures() if captures is None else captures
    rows: dict[int, dict[str, Any]] = defaultdict(lambda: {"name": "", "reports": 0, "poster_reports": 0, "sources": set(), "first": None, "last": None, "examples": []})
    for c in captures:
        text = c.get("text") or ""
        locs = mentions(text)
        poster = bool(POSTER_RE.search(text))
        for code, name in locs.items():
            r = rows[code]
            r["name"] = r["name"] or name
            r["reports"] += 1
            r["poster_reports"] += int(poster)
            r["sources"].add(c.get("source"))
            ts = c.get("published") or c.get("captured_at")
            r["first"] = min(r["first"], ts) if r["first"] else ts
            r["last"] = max(r["last"], ts) if r["last"] else ts
            if poster and len(r["examples"]) < 3:
                r["examples"].append(text[:160].replace("\n", " "))
    df = pd.DataFrame([{"locality_code": k, "locality": v["name"], "reports": v["reports"], "poster_reports": v["poster_reports"],
                        "n_sources": len(v["sources"]), "first": v["first"], "last": v["last"], "examples": " | ".join(v["examples"])}
                       for k, v in rows.items()])
    if df.empty:
        return df
    return df.sort_values(["poster_reports", "reports"], ascending=False).reset_index(drop=True)
