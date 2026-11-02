"""Render the article from its template so that every number in the text comes from `results.json`.

`docs/article/seat-estimate-k26.template.md` is the prose with Jinja2 placeholders and `[@key]` citations;
`render()` fills the placeholders, then runs Pandoc's citeproc over the result with `references.bib` and the APA 7
style (`apa.csl`), so that `docs/article/seat-estimate-k26.md` carries author–year citations and the formatted
reference list (in the `::: {#refs}` division of the template). The PDF is produced by the external publisher script (`EF_PUBLISHER`,
Pandoc → Typst) from that Markdown; its `--keep-typst` option writes the intermediate `.typ` next to the source,
which is what lets Typst find the figures (its root is the `.typ`'s directory). Nothing here changes a result.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from jinja2 import Environment, StrictUndefined

from electionforensics.seats.estimate import ARTICLE_DIR
from electionforensics.taxonomy import party_label

TEMPLATE = "seat-estimate-k26.template.md"
ARTICLE = "seat-estimate-k26.md"
BIB = "references.bib"
CSL = "apa.csl"
#: The external Markdown→PDF publisher (`publish.py`, Pandoc → Typst): its path comes from EF_PUBLISHER, no default on purpose.
PUBLISHER = os.environ.get("EF_PUBLISHER")
#: Interpreter that has the publisher's dependencies (pypandoc, the typst wheel): the system Python via the `py`
#: launcher by default, never the project's own virtual environment.
PUBLISHER_PYTHON = os.environ.get("EF_PUBLISHER_PYTHON", "py")
#: Pandoc Markdown flavour of the rendered article: pipe tables only (readable on GitHub and accepted by the publisher).
MD_OUT = "markdown+pipe_tables-grid_tables-multiline_tables-simple_tables-raw_html+yaml_metadata_block-smart"
#: The public repository every file the article names links to — a mirror of the replication package, filled from the
#: private working repository by `tools/publish_public_repo.py` (author's decision of 2 November 2026) — and the ref the
#: links point at (a tag at release: `EF_REPO_REF=v1.0.0`). The package keeps the working tree's paths, so the article's
#: own directory inside it is `docs/article/`.
REPO_URL = "https://github.com/ravehn/seats-at-stake-israel-2026"
REPO_REF = os.environ.get("EF_REPO_REF", "main")
REPO_ARTICLE_DIR = "docs/article"
#: Photographs per contact sheet in Appendix H (three columns by three rows), and the before/after pair shown at full width.
SHEET_COLS, SHEET_ROWS = 3, 3
PAIR = (11, 20)


def fmt_int(x: Any) -> str:
    return f"{round(float(x)):,}"


def fmt_pct(x: Any, d: int = 1) -> str:
    return f"{100 * float(x):.{d}f}"


def fmt_num(x: Any, d: int = 1) -> str:
    return f"{float(x):.{d}f}"


def fmt_links(urls: list[str]) -> str:
    """Markdown links whose text is the address decoded for reading (Hebrew paths) and whose target is the exact URL."""
    return "; ".join(f"[{unquote(u)}]({u})" for u in urls)


def repo_url(path: str, base: str = "") -> str:
    """Address of a file (or, with a trailing slash, a directory) of the repository at the ref the build points at."""
    rel = f"{base}/{path}" if base else path
    kind = "tree" if rel.endswith("/") else "blob"
    return f"{REPO_URL}/{kind}/{REPO_REF}/{rel.rstrip('/')}"


def link_repo(path: str, text: str | None = None) -> str:
    """Markdown link to a file of the repository root, the path in code font unless a text is given."""
    return f"[{text if text is not None else '`' + path + '`'}]({repo_url(path)})"


def link_art(path: str, text: str | None = None) -> str:
    """Markdown link to a file of the article's directory (`docs/article/`)."""
    return f"[{text if text is not None else '`' + path + '`'}]({repo_url(path, REPO_ARTICLE_DIR)})"


def short_host(url: str) -> str:
    """The publication behind a URL, for a compact source column: `www.ynet.co.il` -> `ynet`, `tmi.maariv.co.il` -> `maariv`."""
    from urllib.parse import urlparse

    host = urlparse(url).netloc.lower()
    parts = [p for p in host.split(".") if p not in ("www", "co", "com", "org", "net", "il", "tmi", "m")]
    return parts[-1] if parts else host


def fmt_source_links(urls: list[str]) -> str:
    """`[ynet](...) · [haaretz](...)`: the sources of an itinerary row as links named after the publication."""
    return " · ".join(f"[{short_host(u)}]({u})" for u in urls)


_RELATIVE_LINK = re.compile(r"(?<!!)\[([^\]]+)\]\((?!(?:https?:|mailto:|#))([^)\s]+)\)")


def absolutize_links(md: str) -> str:
    """Relative links of the Markdown (the case summary's `case-01/<file>`, written for the package) made absolute to the
    repository, so that they work from the PDF; image embeds and fenced code are left alone."""
    out: list[str] = []
    for i, chunk in enumerate(md.split("```")):
        out.append(chunk if i % 2 else _RELATIVE_LINK.sub(lambda m: f"[{m.group(1)}]({repo_url(m.group(2), REPO_ARTICLE_DIR)})", chunk))
    return "```".join(out)


def nbsp(s: Any) -> str:
    """Spaces made non-breaking, so that a multi-word Hebrew name stays whole inside a narrow table cell."""
    return str(s).replace(" ", " ")


def fmt_signed(x: Any) -> str:
    """+2 / −1 / 0, with the typographic minus."""
    v = int(x)
    return (f"+{v}" if v > 0 else f"−{-v}") if v else "0"


def fmt_dmy(iso: Any) -> str:
    """`2026-07-17` or `20260717T…` → `17 July 2026`."""
    t = str(iso).replace("-", "")
    return f"{int(t[6:8])} {MONTHS[int(t[4:6])]} {t[:4]}"


MONTHS = ["", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]


def fmt_delta(x: Any) -> str:
    return f"{float(x):g}"


def en_label(letters: str, knesset: int = 26) -> str:
    """English list name with the ballot letters, e.g. «Likud (מחל)»."""
    return party_label(knesset, letters, "en")


def en_name(letters: str, knesset: int = 26) -> str:
    return party_label(knesset, letters, "en").rsplit(" (", 1)[0]


def _env() -> Environment:
    env = Environment(undefined=StrictUndefined, autoescape=False, trim_blocks=True, lstrip_blocks=True)  # noqa: S701
    env.filters.update({"n": fmt_int, "pct": fmt_pct, "num": fmt_num, "signed": fmt_signed, "d": fmt_delta,
                        "en": en_label, "name": en_name, "links": fmt_links, "nbsp": nbsp,
                        "repo": link_repo, "art": link_art, "srcs": fmt_source_links, "dmy": fmt_dmy})
    return env


CASE_SUMMARY = "case-01-yokneam-public.md"
CASE_DIR = "case-01"


def case_summary_text(path: Path) -> str:
    """The public summary of case file No. 1 as Appendix H: its own title dropped, `## N. …` → `## H.N …`."""
    out: list[str] = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        if ln.startswith("# "):
            continue
        m = re.match(r"^## (\d+)\. (.*)$", ln)
        # the summary's references to its own sections («§ 4», «§§ 2–3») become «§ H.4»; the law's sections («§ 126(2)»,
        # «§ 86», «§§ 384 and 452») have two or more digits or a bracket and are left alone
        ln = re.sub(r"§ ([1-8])(?![\d(])", r"§ H.\1", ln)
        out.append(f"## H.{m.group(1)} {m.group(2)}" if m else ln)
    return "\n".join(out).strip()


def case_photos(manifest: Path) -> list[dict[str, str]]:
    """The photographs of Appendix H in the order of the manifest: the original file, its metadata, the rendering for the PDF."""
    import csv

    with manifest.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def case_sheets(photos: list[dict[str, str]]) -> list[dict[str, Any]]:
    """The contact sheets of Appendix H: nine photographs each, in the order of the manifest."""
    per = SHEET_COLS * SHEET_ROWS
    return [{"no": i // per + 1, "file": f"sheet-{i // per + 1}.jpg", "photos": photos[i:i + per]}
            for i in range(0, len(photos), per)]


def build_contact_sheets(case_dir: Path, tile: int = 800, label: int = 56, pad: int = 24) -> list[Path]:
    """Render the contact sheets of Appendix H from the published originals: every photograph in its displayed
    orientation, fitted into a square tile, with its number and EXIF capture time under it. Renderings for the
    document only; the files themselves are the evidence and are untouched."""
    from PIL import Image, ImageDraw, ImageFont, ImageOps

    photos = case_photos(case_dir / "manifest.csv")
    font = ImageFont.load_default(size=label * 3 // 5)
    out_dir = case_dir / "preview"
    out_dir.mkdir(exist_ok=True)
    written: list[Path] = []
    for sheet in case_sheets(photos):
        rows = (len(sheet["photos"]) + SHEET_COLS - 1) // SHEET_COLS
        width = SHEET_COLS * tile + (SHEET_COLS + 1) * pad
        height = rows * (tile + label) + (rows + 1) * pad
        canvas = Image.new("RGB", (width, height), "white")
        draw = ImageDraw.Draw(canvas)
        for k, p in enumerate(sheet["photos"]):
            im = ImageOps.exif_transpose(Image.open(case_dir / p["file"]))
            if im is None:
                continue
            im = im.convert("RGB")
            im.thumbnail((tile, tile))
            col, row = k % SHEET_COLS, k // SHEET_COLS
            x0, y0 = pad + col * (tile + pad), pad + row * (tile + label + pad)
            canvas.paste(im, (x0 + (tile - im.width) // 2, y0 + (tile - im.height) // 2))
            stamp = p["taken_at"][5:16].replace("T", " ").replace("-", ".")
            draw.text((x0, y0 + tile + 8), f"No. {p['no']}   {stamp}", fill="black", font=font)
        path = out_dir / sheet["file"]
        canvas.save(path, "JPEG", quality=82, optimize=True)
        written.append(path)
    # the evidentiary pair: the last photograph of the posters in place at one site and the bare gate afterwards
    by_no = {int(p["no"]): p for p in photos}
    if all(n in by_no for n in PAIR):
        tile_w = 1200
        canvas = Image.new("RGB", (2 * tile_w + 3 * pad, tile_w + label + 2 * pad), "white")
        draw = ImageDraw.Draw(canvas)
        for k, n in enumerate(PAIR):
            im = ImageOps.exif_transpose(Image.open(case_dir / by_no[n]["file"]))
            if im is None:
                continue
            im = im.convert("RGB")
            im.thumbnail((tile_w, tile_w))
            x0 = pad + k * (tile_w + pad)
            canvas.paste(im, (x0 + (tile_w - im.width) // 2, pad + (tile_w - im.height) // 2))
            stamp = by_no[n]["taken_at"][5:16].replace("T", " ").replace("-", ".")
            draw.text((x0, pad + tile_w + 8), f"No. {n}   {stamp}", fill="black", font=font)
        path = out_dir / "pair.jpg"
        canvas.save(path, "JPEG", quality=84, optimize=True)
        written.append(path)
    for stray in out_dir.glob("*.jpg"):
        if stray not in written:
            stray.unlink()
    return written


def grid_lookup(results: dict[str, Any]) -> dict[tuple[str, float, str], dict[str, Any]]:
    return {(r["coverage"], float(r["delta"]), r["variant"]): r for r in results["grid"]}


def fill_template(results: dict[str, Any], article_dir: Path = ARTICLE_DIR) -> str:
    """The Jinja step alone: placeholders filled, citations still as `[@key]`."""
    env = _env()
    tpl = env.from_string((article_dir / TEMPLATE).read_text(encoding="utf-8"))
    ref = article_dir / "replication" / "reference.py"
    summary, manifest = article_dir / CASE_SUMMARY, article_dir / CASE_DIR / "manifest.csv"
    photos = case_photos(manifest) if manifest.exists() else []
    ctx = {"r": results, "g": grid_lookup(results), "cov": results["coverage"], "f": results["factual"],
           # Appendix H: the public summary of case file No. 1, its published photographs and their contact sheets
           "case1_text": case_summary_text(summary) if summary.exists() else "",
           "case1_photos": photos, "case1_sheets": case_sheets(photos), "repo_url": REPO_URL, "repo_ref": REPO_REF,
           "mc": results["mc"], "mcp": results.get("mc_priors", {}), "bp": results["breakpoints"], "data": results["data"],
           "frames": results.get("frames", {}), "sc": results.get("coverage_scaling"), "it": results.get("itinerary"),
           # Appendix E names the single-file reference implementation and binds the text to it by its SHA-256
           "reference_sha256": hashlib.sha256(ref.read_bytes()).hexdigest() if ref.exists() else "(reference.py not found)",
           "reference_lines": sum(1 for _ in ref.open(encoding="utf-8")) if ref.exists() else 0}
    return absolutize_links(str(tpl.render(**ctx)))


def citeproc(text: str, article_dir: Path = ARTICLE_DIR) -> str:
    """Resolve `[@key]` citations and insert the APA reference list with Pandoc; returns Markdown.

    Without pandoc on the PATH the text is returned unchanged (citations stay as keys) and the caller is told.
    """
    pandoc = shutil.which("pandoc")
    if pandoc is None:
        raise FileNotFoundError("pandoc is not on the PATH; the article needs it for the citations and the reference list")
    src = article_dir / "_render_stub.md"
    src.write_text(text, encoding="utf-8")
    try:
        out = subprocess.run([pandoc, str(src), "-f", "markdown", "-t", MD_OUT, "--standalone", "--wrap=none",  # noqa: S603
                              "--markdown-headings=atx", "--citeproc", "--bibliography", str(article_dir / BIB),
                              "--csl", str(article_dir / CSL)], capture_output=True, text=True, encoding="utf-8", check=True)
    finally:
        src.unlink(missing_ok=True)
    if out.stderr.strip():
        # citeproc warnings (e.g. a key missing from the .bib) must not pass silently
        raise RuntimeError(f"pandoc citeproc warnings:\n{out.stderr}")
    return _strip_bib_metadata(out.stdout)


def _strip_bib_metadata(md: str) -> str:
    """Drop the `bibliography:` / `csl:` / `nocite:` keys (and their indented continuation lines) that pandoc writes back
    into the front matter: the reference list is already in the text, and left in, the publisher's pandoc would emit a
    Typst `#bibliography()` call with a Windows path."""
    if not md.startswith("---"):
        return md
    end = md.find("\n---", 3)
    if end < 0:
        return md
    head, rest = md[:end], md[end:]
    kept: list[str] = []
    skipping = False
    for line in head.splitlines():
        if re.match(r"^(bibliography|csl|nocite):", line):
            skipping = True
            continue
        if skipping and (line.startswith((" ", "-", "\t"))):
            continue
        skipping = False
        kept.append(line)
    return "\n".join(kept) + rest


def word_count(md: str) -> dict[str, int]:
    """Words in the body (title to the end of the statements), the references and appendices apart."""
    main = md.split("\n# References", 1)[0]
    main = re.sub(r"^---.*?---\n", "", main, flags=re.S)
    main_wo_tables = "\n".join(line for line in main.splitlines() if not line.lstrip().startswith("|"))
    return {"total": len(re.findall(r"\S+", md)), "main_body": len(re.findall(r"\S+", main)),
            "main_body_without_tables": len(re.findall(r"\S+", main_wo_tables))}


def render(results: dict[str, Any], article_dir: Path = ARTICLE_DIR, references: bool = True) -> Path:
    if (article_dir / CASE_DIR / "manifest.csv").exists():
        build_contact_sheets(article_dir / CASE_DIR)
    text = fill_template(results, article_dir)
    if references:
        text = citeproc(text, article_dir)
    out = article_dir / ARTICLE
    out.write_text(text, encoding="utf-8", newline="\n")
    return out


def publish_pdf(md: Path, publish_py: Path | None = None, python: str = PUBLISHER_PYTHON, skip_verify: bool = True) -> Path:
    """Compile the Markdown to PDF with the external publisher script (Pandoc → Typst). Raises on failure."""
    if publish_py is None:
        publish_py = Path(PUBLISHER) if PUBLISHER else None
    if publish_py is None or not publish_py.exists():
        raise FileNotFoundError(f"publisher script not found ({publish_py}); set EF_PUBLISHER to the path of publish.py")
    exe = shutil.which(python) or python
    cmd = [exe, *(["-3"] if Path(exe).stem.lower() == "py" else []), str(publish_py), "compile", str(md), "--target", "preview", "--keep-typst"]
    if skip_verify:
        cmd.append("--skip-verify")
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")  # noqa: S603
    md.with_suffix(".typ").unlink(missing_ok=True)
    if res.returncode != 0:
        raise RuntimeError(f"publish.py failed ({res.returncode}):\n{res.stdout}\n{res.stderr}")
    return md.with_suffix(".pdf")
