"""Witness statements: verbatim text files with a YAML front matter, imported as exhibits of a case.

A statement file lives under `incidents/statements/` (git-tracked: the SQLite base is regenerated from
seeds and statements). The front matter names the case, the witness, when and how the statement was
taken and the consent given; the body after the second `---` is the statement itself, never edited.
Import is idempotent on the body text (SHA-256), so a rebuild of the base re-links the same exhibit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

from electionforensics.config import ROOT
from electionforensics.incidents.store import Store

STATEMENTS_DIR = ROOT / "incidents" / "statements"
DEFAULT_WITNESS = "editor (first-hand witness)"
_FRONT = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*\r?\n", re.S)


@dataclass
class Statement:
    path: Path
    body: str
    meta: dict[str, Any] = field(default_factory=dict)


def parse_statement(path: Path) -> Statement:
    """Split a statement file into front matter (YAML) and the verbatim body; no front matter is allowed."""
    raw = path.read_text(encoding="utf-8-sig")
    m = _FRONT.match(raw)
    if not m:
        return Statement(path, raw)
    meta = yaml.safe_load(m.group(1)) or {}
    return Statement(path, raw[m.end():], dict(meta))


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime | date):
        return value.isoformat()
    return str(value)


def import_statement(path: Path, store: Store, case: str | None = None, witness: str | None = None,
                     number: int | None = None, consent: str | None = None) -> tuple[int, str, int | None]:
    """Record one statement file; explicit arguments override the file's front matter."""
    st = parse_statement(path)
    slug = case or st.meta.get("case")
    if not slug:
        raise ValueError(f"{path}: no case slug in the front matter and none given")
    who = witness or str(st.meta.get("witness") or DEFAULT_WITNESS)
    num = number if number is not None else st.meta.get("number")
    num = int(num) if num is not None else None
    note = consent or _iso(st.meta.get("consent"))
    if num is not None:
        store.set_number(str(slug), num)
    try:
        rel = str(path.resolve().relative_to(ROOT.resolve()))
    except ValueError:
        rel = str(path)
    eid = store.add_statement(str(slug), who, st.body, lang=str(st.meta.get("lang") or "ru"),
                              taken_at=_iso(st.meta.get("taken_at")), consent=note, capture_path=rel)
    return eid, str(slug), num


def import_statements(directory: Path, store: Store) -> list[tuple[int, str, int | None]]:
    """Import every `*.md` statement under a directory, in name order."""
    return [import_statement(p, store) for p in sorted(directory.glob("*.md"))]
