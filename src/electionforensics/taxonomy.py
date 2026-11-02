"""Access to the YAML taxonomies (parties, agreements, later: incident categories and law)."""

from __future__ import annotations

from functools import cache
from typing import Any

import yaml

from electionforensics.config import TAXONOMY


@cache
def parties(knesset: int) -> dict[str, dict[str, Any]]:
    doc = yaml.safe_load((TAXONOMY / "parties.yaml").read_text(encoding="utf-8")) or {}
    entry = doc.get(knesset) or {}
    return dict(entry.get("lists", {}))


def party_label(knesset: int, letters: str, lang: str = "ru") -> str:
    info = parties(knesset).get(letters)
    if not info:
        return letters
    name = info.get("he_short" if lang == "he" else lang) or info.get(lang) or info.get("he") or letters
    return f"{name} ({letters})"
