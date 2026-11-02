"""SQLite store for the evidence base, built to the shape of an investigation file.

Entities
- source   : where things come from (a channel, a feed, a site, a person's manual inbox)
- exhibit  : one captured item — URL, capture time, hash of the captured copy, author, language,
             original text, translation (separate, with translator tag). Never edited, only superseded.
- case     : one incident — category, place (CBS locality code), time window, actors, status
- link     : exhibit → case with a role (reports / corroborates / refutes / official / duplicate)
- grade    : append-only history of evidence grade and attribution confidence per case

Nothing is deleted. `grade_case` recomputes the grade from the link graph each time it is called.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from electionforensics.config import ROOT

DB_PATH = ROOT / "data" / "incidents.sqlite"

SCHEMA = """
CREATE TABLE IF NOT EXISTS source (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL, url TEXT, handle TEXT, lang TEXT,
  collection TEXT, alive INTEGER DEFAULT 1, first_seen TEXT, note TEXT, UNIQUE(name));
CREATE TABLE IF NOT EXISTS exhibit (
  id INTEGER PRIMARY KEY, source_id INTEGER REFERENCES source(id), url TEXT, captured_at TEXT NOT NULL,
  capture_path TEXT, sha256 TEXT, author TEXT, published_at TEXT, lang TEXT, text_original TEXT,
  text_ru TEXT, translator TEXT, media_kind TEXT, wayback_url TEXT, superseded_by INTEGER, note TEXT);
CREATE TABLE IF NOT EXISTS "case" (
  id INTEGER PRIMARY KEY, slug TEXT UNIQUE, category TEXT NOT NULL, title_ru TEXT NOT NULL, title_he TEXT,
  locality_code INTEGER, locality_name TEXT, place_detail TEXT, happened_from TEXT, happened_to TEXT,
  actors_alleged TEXT, victims TEXT, status TEXT NOT NULL DEFAULT 'open', attribution TEXT NOT NULL DEFAULT 'unknown',
  false_flag_considered INTEGER DEFAULT 0, summary_ru TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  superseded_by INTEGER);
CREATE TABLE IF NOT EXISTS link (
  id INTEGER PRIMARY KEY, exhibit_id INTEGER REFERENCES exhibit(id), case_id INTEGER REFERENCES "case"(id),
  role TEXT NOT NULL, note TEXT, created_at TEXT NOT NULL, UNIQUE(exhibit_id, case_id, role));
CREATE TABLE IF NOT EXISTS grade (
  id INTEGER PRIMARY KEY, case_id INTEGER REFERENCES "case"(id), graded_at TEXT NOT NULL, grade TEXT NOT NULL,
  attribution TEXT NOT NULL, basis TEXT);
CREATE TABLE IF NOT EXISTS decision (
  id INTEGER PRIMARY KEY, case_id INTEGER REFERENCES "case"(id), decided_at TEXT NOT NULL, by_whom TEXT NOT NULL,
  decision TEXT NOT NULL, note TEXT);
"""

OFFICIAL_KINDS = {"official", "police", "court", "cec"}
MEDIA_KINDS = {"media", "news"}
LINK_ROLES = {"reports", "corroborates", "refutes", "official", "duplicate", "related"}


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path = DB_PATH):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """Additive schema changes for databases created by earlier versions."""
        cols = {r["name"] for r in self.conn.execute('PRAGMA table_info("case")')}
        if "number" not in cols:  # the editor's case numbering (№1 Yokneam, №2 Daliyat al-Karmel, ...)
            self.conn.execute('ALTER TABLE "case" ADD COLUMN number INTEGER')
            self.conn.commit()
        exhibit_cols = {r["name"] for r in self.conn.execute('PRAGMA table_info(exhibit)')}
        if "independence_group" not in exhibit_cols:
            self.conn.execute('ALTER TABLE exhibit ADD COLUMN independence_group TEXT')
            self.conn.commit()

    # -- numbering and statements ---------------------------------------------------------------
    def set_number(self, slug: str, number: int) -> None:
        self.conn.execute('UPDATE "case" SET number=?, updated_at=? WHERE slug=?', (number, now(), slug))
        self.conn.commit()

    def add_statement(self, slug: str, witness: str, text: str, lang: str = "ru", taken_at: str | None = None,
                      consent: str | None = None, capture_path: str | None = None) -> int:
        """Record a witness statement verbatim as an exhibit linked to the case (role 'reports').

        Idempotent on the text: importing the same statement again returns the existing exhibit.
        """
        case = self.conn.execute('SELECT id FROM "case" WHERE slug=?', (slug,)).fetchone()
        if not case:
            raise KeyError(f"no case with slug {slug}")
        sid = self.upsert_source(witness, "witness", lang=lang)
        sha = hashlib.sha256(("\n" + text).encode("utf-8")).hexdigest()  # same payload rule as add_exhibit (url None)
        eid = self.exhibit_by_sha(sha)
        if eid is None:
            eid = self.add_exhibit(sid, None, text, lang=lang, media_kind="statement", captured_at=taken_at,
                                   capture_path=capture_path,
                                   note=f"witness statement; consent: {consent or 'not recorded'}")
        self.link(eid, int(case["id"]), "reports", note="witness statement")
        self.grade_case(int(case["id"]))
        return eid

    def exhibit_by_sha(self, sha: str) -> int | None:
        row = self.conn.execute("SELECT id FROM exhibit WHERE sha256=?", (sha,)).fetchone()
        return int(row["id"]) if row else None

    def seed_exhibit_exists(self, source_id: int, case_id: int, url: str | None) -> bool:
        """A placeholder exhibit from a seed file already links this source to this case (re-import)."""
        row = self.conn.execute(
            "SELECT e.id FROM exhibit e JOIN link l ON l.exhibit_id=e.id WHERE e.source_id=? AND l.case_id=? "
            "AND COALESCE(e.url,'')=COALESCE(?,'') AND e.note LIKE 'seed %' LIMIT 1", (source_id, case_id, url)).fetchone()
        return row is not None

    # -- sources -------------------------------------------------------------------------------
    def upsert_source(self, name: str, kind: str, **kw: Any) -> int:
        cur = self.conn.execute("SELECT id FROM source WHERE name=?", (name,))
        row = cur.fetchone()
        if row:
            sets = ", ".join(f"{k}=?" for k in kw)
            if sets:
                self.conn.execute(f"UPDATE source SET {sets} WHERE id=?", (*kw.values(), row["id"]))  # noqa: S608 - keys are code constants
            self.conn.commit()
            return int(row["id"])
        cols = ["name", "kind", "first_seen", *kw]
        vals = [name, kind, now(), *kw.values()]
        cur = self.conn.execute(f"INSERT INTO source ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", vals)  # noqa: S608
        self.conn.commit()
        return int(cur.lastrowid or 0)

    # -- exhibits ------------------------------------------------------------------------------
    def add_exhibit(self, source_id: int | None, url: str | None, text_original: str | None = None, **kw: Any) -> int:
        payload = (url or "") + "\n" + (text_original or "")
        sha = kw.pop("sha256", None) or hashlib.sha256(payload.encode("utf-8")).hexdigest()
        vals = [source_id, url, kw.pop("captured_at", None) or now(), sha, text_original, *kw.values()]
        cols = ["source_id", "url", "captured_at", "sha256", "text_original", *kw]
        cur = self.conn.execute(f"INSERT INTO exhibit ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", vals)  # noqa: S608
        self.conn.commit()
        return int(cur.lastrowid or 0)

    # -- cases ---------------------------------------------------------------------------------
    def upsert_case(self, slug: str, category: str, title_ru: str, **kw: Any) -> int:
        row = self.conn.execute('SELECT id FROM "case" WHERE slug=?', (slug,)).fetchone()
        ts = now()
        if row:
            kw.update(category=category, title_ru=title_ru, updated_at=ts)  # EF-002: positional columns were never updated
            sets = ", ".join(f"{k}=?" for k in kw)
            self.conn.execute(f'UPDATE "case" SET {sets} WHERE id=?', (*kw.values(), row["id"]))  # noqa: S608
            self.conn.commit()
            return int(row["id"])
        cols = ["slug", "category", "title_ru", "created_at", "updated_at", *kw]
        vals = [slug, category, title_ru, ts, ts, *kw.values()]
        cur = self.conn.execute(f'INSERT INTO "case" ({", ".join(cols)}) VALUES ({", ".join("?" * len(cols))})', vals)  # noqa: S608
        self.conn.commit()
        return int(cur.lastrowid or 0)

    def link(self, exhibit_id: int, case_id: int, role: str, note: str | None = None) -> None:
        if role not in LINK_ROLES:
            raise ValueError(f"unknown evidence link role: {role}")
        self.conn.execute("INSERT OR IGNORE INTO link (exhibit_id, case_id, role, note, created_at) VALUES (?,?,?,?,?)",
                          (exhibit_id, case_id, role, note, now()))
        self.conn.commit()

    def decide(self, case_id: int, by_whom: str, decision: str, note: str | None = None) -> None:
        self.conn.execute("INSERT INTO decision (case_id, decided_at, by_whom, decision, note) VALUES (?,?,?,?,?)",
                          (case_id, now(), by_whom, decision, note))
        self.conn.commit()

    def decision_exists(self, case_id: int, decision: str) -> bool:
        row = self.conn.execute("SELECT id FROM decision WHERE case_id=? AND decision=? LIMIT 1", (case_id, decision)).fetchone()
        return row is not None

    # -- grading -------------------------------------------------------------------------------
    def grade_case(self, case_id: int) -> tuple[str, str]:
        """Recompute the evidence grade (A–E) from the exhibits linked to the case and append it to history."""
        rows = self.conn.execute(
            "SELECT l.role, s.kind, s.name, e.id, e.media_kind, e.independence_group, e.note, "
            "e.text_original, e.capture_path FROM link l JOIN exhibit e ON e.id=l.exhibit_id "
            "LEFT JOIN source s ON s.id=e.source_id WHERE l.case_id=? AND e.superseded_by IS NULL", (case_id,)).fetchall()
        refuting = {r["id"] for r in rows if r["role"] == "refutes"}
        supporting = [r for r in rows if r["role"] in {"reports", "corroborates", "official"}
                      and r["id"] not in refuting and (r["text_original"] or r["capture_path"])]
        kinds = [(r["role"], (r["kind"] or "").lower(), r["name"], r["media_kind"]) for r in supporting]
        official = any(role == "official" or kind in OFFICIAL_KINDS for role, kind, _, _ in kinds)
        media_outlets = {name for role, kind, name, _ in kinds if kind in MEDIA_KINDS and role in ("reports", "corroborates")}
        origins = {r["independence_group"] for r in supporting if r["kind"] in MEDIA_KINDS and r["independence_group"]}
        social_with_media = any(kind == "social" and (mk or "") in ("photo", "video") for _, kind, _, mk in kinds)
        # A named eyewitness's verbatim statement, photos or videos weigh like a first-hand social post with
        # media (D): first-hand, but a single and possibly interested source; independence lifts it to C and above.
        witness_statement = any(kind == "witness" and (mk or "") in ("statement", "photo", "video") for _, kind, _, mk in kinds)
        if official:
            grade = "A"
        elif len(media_outlets) >= 2 and len(origins) >= 2:
            grade = "B"
        elif len(media_outlets) >= 1:
            grade = "C"
        elif social_with_media or witness_statement:
            grade = "D"
        else:
            grade = "E"
        case = self.conn.execute('SELECT attribution FROM "case" WHERE id=?', (case_id,)).fetchone()
        attribution = case["attribution"] if case else "unknown"
        basis = json.dumps({"official": official, "media_outlets": sorted(media_outlets), "social_with_media": social_with_media,
                            "witness_statement": witness_statement, "independence_groups": sorted(origins),
                            "refuting_exhibits": sorted(refuting), "supporting_exhibits": sorted({r["id"] for r in supporting}),
                            "meaning": "source support, not proof of every assertion or attribution"}, ensure_ascii=False)
        self.conn.execute("INSERT INTO grade (case_id, graded_at, grade, attribution, basis) VALUES (?,?,?,?,?)",
                          (case_id, now(), grade, attribution, basis))
        self.conn.commit()
        return grade, attribution

    def grade_all(self) -> dict[int, str]:
        out = {}
        for r in self.conn.execute('SELECT id FROM "case" WHERE superseded_by IS NULL'):
            out[int(r["id"])] = self.grade_case(int(r["id"]))[0]
        return out

    # -- reading -------------------------------------------------------------------------------
    def cases(self) -> list[dict[str, Any]]:
        q = ('SELECT c.*, (SELECT grade FROM grade g WHERE g.case_id=c.id ORDER BY g.id DESC LIMIT 1) AS grade, '
             '(SELECT COUNT(*) FROM link l WHERE l.case_id=c.id) AS n_exhibits FROM "case" c '
             'WHERE c.superseded_by IS NULL ORDER BY c.happened_from, c.id')
        return [dict(r) for r in self.conn.execute(q)]

    def counts(self) -> dict[str, int]:
        return {t: int(self.conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]) for t in ("source", "exhibit", "case", "link", "grade", "decision")}  # noqa: S608

    def close(self) -> None:
        self.conn.close()
