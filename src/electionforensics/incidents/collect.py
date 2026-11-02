"""Collectors: RSS feeds and public Telegram channels → candidate exhibits with captured copies.

Every item that passes the keyword filter is (1) written to the evidence store as an exhibit with
the source, URL, publication time, language and original text; (2) saved verbatim as a JSON capture
under data/captures/<date>/<sha>.json (the local copy of record); (3) optionally submitted to the
Wayback Machine (one request per item, politely spaced). Nothing is classified here: candidates go
to a review queue (inbox/queue_<date>.csv) for the editor and for the extraction step.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import feedparser
import httpx
import yaml

from electionforensics.config import ROOT, TAXONOMY, USER_AGENT
from electionforensics.incidents.store import Store

CAPTURES = ROOT / "data" / "captures"
QUEUE_DIR = ROOT / "inbox"
TG_MSG_RE = re.compile(
    r'<div class="tgme_widget_message_wrap[^"]*">(?P<block>.*?)(?=<div class="tgme_widget_message_wrap|\Z)', re.S)
TG_POST_RE = re.compile(r'data-post="(?P<post>[^"]+)"')
TG_TEXT_RE = re.compile(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(?P<text>.*?)</div>', re.S)
TG_TIME_RE = re.compile(r'<time[^>]*datetime="(?P<dt>[^"]+)"')
TAG_RE = re.compile(r"<[^>]+>")


@dataclass
class Item:
    source: str
    kind: str
    lang: str
    url: str
    published: str | None
    text: str
    media: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def sha(self) -> str:
        return hashlib.sha256((self.url + "\n" + self.text).encode("utf-8")).hexdigest()


def load_sources(path: Path | None = None) -> list[dict[str, Any]]:
    doc = yaml.safe_load((path or TAXONOMY / "sources.yaml").read_text(encoding="utf-8")) or {}
    return [s for s in doc.get("sources", []) if s.get("collect") in ("rss", "telegram")]


def load_keywords(path: Path | None = None) -> dict[str, list[str]]:
    doc = yaml.safe_load((path or TAXONOMY / "keywords.yaml").read_text(encoding="utf-8")) or {}
    return {grp: [w for lang in doc.get(grp, {}).values() for w in lang] for grp in ("election", "incident")}


def matches(text: str, kw: dict[str, list[str]]) -> tuple[list[str], list[str]]:
    t = text.lower()
    e = [w for w in kw["election"] if w.lower() in t]
    i = [w for w in kw["incident"] if w.lower() in t]
    return e, i


def _strip(s: str) -> str:
    s = re.sub(r"<br\s*/?>", "\n", s)
    return html.unescape(TAG_RE.sub("", s)).strip()


def fetch_rss(src: dict[str, Any], client: httpx.Client) -> list[Item]:
    r = client.get(src["url"], timeout=30)
    r.raise_for_status()
    feed = feedparser.parse(r.content)
    out = []
    for e in feed.entries:
        text = " ".join(x for x in (e.get("title", ""), _strip(e.get("summary", "") or "")) if x)
        pub = e.get("published") or e.get("updated")
        out.append(Item(src["name"], src["kind"], src.get("lang", ""), e.get("link", ""), pub, text,
                        raw={k: str(e.get(k)) for k in ("title", "link", "published", "summary") if e.get(k)}))
    return out


def fetch_telegram(src: dict[str, Any], client: httpx.Client, pages: int = 1) -> list[Item]:
    handle = src["handle"]
    url = f"https://t.me/s/{handle}"
    out: list[Item] = []
    before: str | None = None
    for _ in range(pages):
        r = client.get(url if before is None else f"{url}?before={before}", timeout=30)
        r.raise_for_status()
        body = r.text
        ids: list[int] = []
        for m in TG_MSG_RE.finditer(body):
            block = m.group("block")
            post = TG_POST_RE.search(block)
            txt = TG_TEXT_RE.search(block)
            tm = TG_TIME_RE.search(block)
            if not post:
                continue
            msg_id = post.group("post").split("/")[-1]
            if msg_id.isdigit():
                ids.append(int(msg_id))
            media = "video" if "tgme_widget_message_video" in block else ("photo" if "tgme_widget_message_photo" in block else None)
            out.append(Item(src["name"], src["kind"], src.get("lang", ""), f"https://t.me/{post.group('post')}",
                            tm.group("dt") if tm else None, _strip(txt.group("text")) if txt else "", media,
                            raw={"post": post.group("post")}))
        if not ids:
            break
        before = str(min(ids))
    return out


def capture(item: Item) -> Path:
    d = CAPTURES / datetime.now(UTC).strftime("%Y-%m-%d")
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{item.sha}.json"
    if not p.exists():
        p.write_text(json.dumps({"captured_at": datetime.now(UTC).isoformat(timespec="seconds"), "source": item.source,
                                 "url": item.url, "published": item.published, "lang": item.lang, "media": item.media,
                                 "text": item.text, "raw": item.raw}, ensure_ascii=False, indent=1), encoding="utf-8")
    return p


def wayback_save(url: str, client: httpx.Client) -> str | None:
    try:
        r = client.get(f"https://web.archive.org/save/{url}", timeout=60, follow_redirects=True)
        if r.status_code in (200, 302):
            loc = r.headers.get("content-location") or str(r.url)
            return loc if "web.archive.org" in loc else f"https://web.archive.org/web/{url}"
    except httpx.HTTPError:
        return None
    return None


def collect(store: Store, sources: list[dict[str, Any]] | None = None, wayback: bool = True,
            tg_pages: int = 2, pace_s: float = 5.0, max_wayback: int = 60) -> dict[str, Any]:
    """Run all collectors once. Wayback submissions are capped per run (they take 10–60 s each); items
    beyond the cap are still captured locally and get their archive copy on a later run."""
    sources = sources or load_sources()
    kw = load_keywords()
    stats: dict[str, Any] = {"sources": 0, "fetched": 0, "candidates": 0, "new": 0, "wayback_submitted": 0, "errors": [], "queue": None}
    queue_rows: list[dict[str, Any]] = []
    seen = {r["sha256"] for r in store.conn.execute("SELECT sha256 FROM exhibit")}
    with httpx.Client(headers={"User-Agent": USER_AGENT}, follow_redirects=True) as client:
        for src in sources:
            try:
                items = fetch_rss(src, client) if src["collect"] == "rss" else fetch_telegram(src, client, tg_pages)
            except Exception as exc:
                stats["errors"].append(f"{src['name']}: {type(exc).__name__}: {exc}")
                continue
            stats["sources"] += 1
            stats["fetched"] += len(items)
            sid = store.upsert_source(src["name"], src["kind"], url=src.get("url") or f"https://t.me/s/{src.get('handle')}",
                                      handle=src.get("handle"), lang=src.get("lang"), collection=src["collect"])
            for it in items:
                e_hits, i_hits = matches(it.text, kw)
                if not (e_hits and i_hits):
                    continue
                stats["candidates"] += 1
                if it.sha in seen:
                    continue
                path = capture(it)
                wb = None
                if wayback and it.url and stats["wayback_submitted"] < max_wayback:
                    wb = wayback_save(it.url, client)
                    stats["wayback_submitted"] += 1
                    time.sleep(pace_s)
                store.add_exhibit(sid, it.url, it.text, sha256=it.sha, capture_path=str(path.relative_to(ROOT)),
                                  published_at=it.published, lang=it.lang, media_kind=it.media, wayback_url=wb,
                                  note=f"keywords: {', '.join(e_hits[:3])} | {', '.join(i_hits[:5])}")
                seen.add(it.sha)
                stats["new"] += 1
                queue_rows.append({"source": it.source, "published": it.published, "url": it.url, "lang": it.lang,
                                   "media": it.media, "election_terms": " ".join(e_hits[:3]), "incident_terms": " ".join(i_hits[:6]),
                                   "text": it.text[:400].replace("\n", " "), "capture": str(path.relative_to(ROOT)), "wayback": wb or ""})
    if queue_rows:
        import pandas as pd

        QUEUE_DIR.mkdir(exist_ok=True)
        qp = QUEUE_DIR / f"queue_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.csv"
        pd.DataFrame(queue_rows).to_csv(qp, index=False, encoding="utf-8-sig")
        stats["queue"] = str(qp.relative_to(ROOT))
    return stats
