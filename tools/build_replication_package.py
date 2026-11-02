"""Assemble the public replication package of the seat-estimate article from a curated allow-list.

The project repository is private and holds material that is not released (the evidence base under `incidents/`,
the case files, the audit trail, the research notes): the article's «Data and code availability» statement therefore
cannot point readers at the repository as it is. This tool builds the package the statement promises — the seat module
and the code it imports, the reference tables it reads, the archived raw files the pinned build used (byte-exact, with
the download manifest), the article's template, bibliography, results, tables and figures, and the tests that reproduce
the official 2022 allocation — as a zip with a manifest of SHA-256 hashes, and refuses to build while any text file in
it carries an e-mail address, a chat link, a machine path or a match of the author's private redaction rules
(`EF_AUDIT_REDACTION`, the same file the audit exporter uses). The package carries its own README (generated here from
`results.json`: citation, data version, contents, how to reproduce, what is excluded), not the project's.

    uv run python -X utf8 tools/build_replication_package.py            # dist/replication-k26-<version>.zip
    uv run python -X utf8 tools/build_replication_package.py --check    # list the files and the scan only

Nothing under `incidents/`, `audit/`, `inbox/`, `logs/`, `docs/cases/`, `docs/research/`, `reports/`, `data/processed/`
or `data/interim/` is ever included; `tests/test_replication_package.py` asserts it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from electionforensics import __version__  # noqa: E402
from electionforensics.seats.article import REPO_URL as PUBLIC_REPO  # noqa: E402

ARTICLE_DIR = ROOT / "docs" / "article"
#: Files and directories that go in whatever the results say (directories recursively, code and text only).
ALWAYS: tuple[str, ...] = (
    "pyproject.toml", "uv.lock", "LICENSE", ".gitattributes",
    "src/electionforensics",
    "taxonomy/parties.yaml", "taxonomy/blocs.yaml", "taxonomy/surplus_agreements.yaml", "taxonomy/druze_localities.yaml",
    "tests/__init__.py", "tests/conftest.py", "tests/test_seats.py", "tests/test_reference_implementation.py",
    "tests/test_itinerary.py", "tests/test_case01_exhibits.py", "tests/test_provenance.py",
    "tools/verify_bib.py", "tools/build_replication_package.py",
    "data/raw/datagov/MANIFEST.json", "data/raw/cbs",
    "docs/article",
)
#: Never in the package, whatever the allow-list says (checked on every collected path).
FORBIDDEN_PREFIXES: tuple[str, ...] = (
    "incidents/", "audit/", "inbox/", "logs/", "docs/cases/", "docs/research/", "docs/analyses/", "docs/flags/",
    "docs/ops/", "docs/design/", "docs/audit/", "docs/adversarial/", "reports/", "data/processed/", "data/interim/",
    ".claude/", ".git/", ".venv/", "CLAUDE.md", "PLAN.md", "docs/DECISIONS.md",
)
SKIP_DIR_NAMES = frozenset({"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "_render_stub.md"})
#: Text files the scan reads; the CSL style file is a third-party artefact whose metadata names its own authors and is skipped.
TEXT_SUFFIXES = frozenset({".py", ".md", ".bib", ".json", ".csv", ".yaml", ".yml", ".toml", ".txt", ".svg", ".lock",
                           ".gitattributes"})
#: Built-in scan rules (name, regex) applied to every text file; the private rules file adds the author's own.
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@(?!(?:[A-Za-z0-9-]+\.)*(?:github\.com|anthropic\.com)(?![A-Za-z0-9-]))"
                   r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
BUILTIN_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("email", EMAIL),
    ("chat_link", re.compile(r"claude\.ai/(?:cowork|chat|code|project)")),
    ("windows_path", re.compile(r"[A-Za-z]:\\\\?(?:Users|mine)\\")),
    ("user_home", re.compile(r"/(?:Users|home)/[A-Za-z]")),
)
REDACTION_ENV = "EF_AUDIT_REDACTION"
DEFAULT_REDACTION_FILE = Path.home() / ".electionforensics" / "audit-redaction.txt"


def _rel(p: Path) -> str:
    return p.relative_to(ROOT).as_posix()


#: Station files of earlier elections that `model.features.build_features` reads for the haredi-by-voting-history
#: labels (the election before the one being featured): the 24th for the 2022 features of the article, the 23rd for
#: the synthetic 24th/25th-Knesset elections of the package's own tests.
HISTORY_KNESSETS: tuple[int, ...] = (23, 24)


def data_files(results_path: Path = ARTICLE_DIR / "results.json") -> list[str]:
    """The raw files the pinned build used, read from results.json (never guessed), plus the history files above."""
    from electionforensics.ingest.registry import ELECTIONS

    r = json.loads(results_path.read_text(encoding="utf-8"))
    cur, prev = r["data"]["current"], r["data"]["previous"]
    out = [f"data/raw/knesset{cur['knesset']}/snapshots/{cur['file']}"]
    prev_candidates = [f"data/raw/datagov/{prev['file']}", f"data/raw/knesset{prev['knesset']}/{prev['file']}"]
    out += [c for c in prev_candidates if (ROOT / c).exists()][:1]
    out += [f"data/raw/{ELECTIONS[k].stations_file}" for k in HISTORY_KNESSETS]
    return out


def tracked_files(root: Path) -> set[Path] | None:
    """The files git tracks under `root`, or None when `root` is not a git checkout (a test's temporary tree): the
    package is assembled from the repository, never from stray files of a working copy (a reviewer's copy of the
    PDF, an empty note) that happen to sit inside an allow-listed directory."""
    git = shutil.which("git")
    if git is None:
        return None
    try:
        out = subprocess.run([git, "-C", str(root), "ls-files", "-z"], capture_output=True, check=True)  # noqa: S603
    except (OSError, subprocess.CalledProcessError):
        return None
    return {root / rel for rel in out.stdout.decode("utf-8").split("\0") if rel}


def collect(root: Path = ROOT, extra: list[str] | None = None) -> list[Path]:
    """Every file of the package, sorted; raises if a forbidden path slipped in. Inside an allow-listed directory only
    git-tracked files are taken (see `tracked_files`)."""
    wanted = list(ALWAYS) + (extra if extra is not None else data_files())
    tracked = tracked_files(root)
    files: set[Path] = set()
    for rel in wanted:
        p = root / rel
        if p.is_dir():
            for q in p.rglob("*"):
                if q.is_file() and not (set(q.relative_to(root).parts) & SKIP_DIR_NAMES) and (tracked is None or q in tracked):
                    files.add(q)
        elif p.is_file():
            files.add(p)
        else:
            raise FileNotFoundError(f"replication package: {rel} is not in the repository")
    bad = [_rel(f) for f in files if _rel(f).startswith(FORBIDDEN_PREFIXES) or _rel(f) in FORBIDDEN_PREFIXES]
    if bad:
        raise RuntimeError(f"replication package: forbidden paths collected: {bad}")
    return sorted(files)


def load_private_rules(path: Path | None = None) -> list[tuple[str, re.Pattern[str]]]:
    """`name=regex` per line, as the audit exporter reads it; missing file → no extra rules (reported by the caller)."""
    path = path or Path(os.environ.get(REDACTION_ENV, DEFAULT_REDACTION_FILE))
    if not path.exists():
        return []
    rules: list[tuple[str, re.Pattern[str]]] = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        name, sep, rx = ln.partition("=")
        if sep and name.strip() and rx.strip():
            rules.append((name.strip(), re.compile(rx.strip())))
    return rules


def scan(files: list[Path], rules: list[tuple[str, re.Pattern[str]]] | None = None) -> list[tuple[str, str, str]]:
    """(path, rule, excerpt) for every match in a text file; the excerpt is the line with the match, trimmed."""
    all_rules = list(BUILTIN_RULES) + (load_private_rules() if rules is None else rules)
    hits: list[tuple[str, str, str]] = []
    for f in files:
        if f.suffix.lower() not in TEXT_SUFFIXES and f.name not in TEXT_SUFFIXES:
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for name, rx in all_rules:
            for m in rx.finditer(text):
                line_start = text.rfind("\n", 0, m.start()) + 1
                line_end = text.find("\n", m.end())
                excerpt = text[line_start: line_end if line_end >= 0 else None].strip()
                hits.append((_rel(f), name, excerpt[:160]))
    return hits


def package_readme(results_path: Path = ARTICLE_DIR / "results.json") -> str:
    """The package's own README (the project's README describes the whole project, most of which is not released)."""
    r = json.loads(results_path.read_text(encoding="utf-8"))
    cur, prev, cbs = r["data"]["current"], r["data"]["previous"], r["data"]["cbs"]
    return f"""# Seats at Stake — replication package

Replication package of: *Seats at Stake: A Conditional Estimate of the Knesset Seats Movable by a One-Sided Replacement
of Outdoor Campaign Advertising, Israel 2026* — an article edited by Raveh Neeman (independent researcher);
DOI of this package to be assigned at the time of deposit.

Public repository of this package: {PUBLIC_REPO} (the same files; releases are tagged). Built from the author's private
working repository, package version {r["package_version"]}, results generated {r["generated_at"]}.
Data version of the build: CEC station file `{cur["file"]}` (snapshot {cur["snapshot_ts"]}, SHA-256 `{cur["sha256"]}`),
2022 station file `{prev["file"]}` (SHA-256 `{prev["sha256"]}`), CBS localities file `{cbs["file"]}` (SHA-256
`{cbs["sha256"]}`), code fingerprint `{r["code_sha256"]}`. `MANIFEST.json` lists the SHA-256 of every file here: the
archived data under `data/raw/` and every CSV table byte-exact, the other text files with LF line endings (a Windows
checkout with `core.autocrlf` shows CRLF; compare against `git show HEAD:<path>` or against the zip).

## Contents

- `src/electionforensics/` — the Python package; the seat module is `src/electionforensics/seats/` (`allocation`: exact
  Bader–Ofer with surplus-vote agreements; `coverage`: readings A, B, B⁺ and the appendix readings; `itinerary`: the
  prime minister's campaign itinerary and the threshold of reading I; `counterfactual`: the transfer of δ; `scaling`:
  seats against the covered share; `estimate`: the grid, the step curves, the Monte-Carlo, provenance; `figures`;
  `article`: the Markdown rendered from the template).
- `data/raw/` — the archived official files used in the build, byte-exact as downloaded: the 2026 snapshot under
  `knesset26/snapshots/`; under `datagov/` the 2022 station file and the 2020 and 2021 station files, which the
  feature builder reads for the haredi-by-voting-history labels of the previous election (the tests' synthetic
  elections are numbered 24 and 25), with the download manifest `datagov/MANIFEST.json`; the CBS localities file
  under `cbs/`.
- `taxonomy/` — parties per election, blocs (and the alternative frame of Appendix C, reported cell by cell in the `frame_*`
  columns of the grid tables), the surplus-vote agreements of 2026
  (marked unverified against the Committee's own notice), Druze localities.
- `docs/article/` — the article (`seat-estimate-k26.md`, `.pdf`), its template, bibliography and CSL style,
  `results.json`, `tables/`, `figures/`, the Crossref check of the references, the public summary of case file No. 1
  (`case-01-yokneam-public.md`, Appendix H) with its twenty-three photographs published in full (`case-01/`: the original
  files byte-exact with all their metadata, `manifest.csv` with time, GPS, device, size and SHA-256 of each, and the
  contact sheets the PDF shows under `preview/`, rebuilt from the originals at every build), the press-sourced table of the prime minister's campaign itinerary behind reading I
  (`itinerary_sources_k26.csv`, Appendix G; joined with the computed shares in `tables/itinerary_k26.csv`) and the
  single-file reference implementation of every operation of the article (`replication/reference.py`, Appendix E),
  which reads the tables alone.
- `tests/` — including the reproduction of the official 2022 allocation and of the pinned 2026 snapshot, the comparison
  of the package with the reference implementation on every number of the article (Appendix F) and the checks of the
  itinerary table and of the threshold it sets.
- `tools/verify_bib.py` (Crossref check of the bibliography) and `tools/build_replication_package.py` (how this package
  was assembled from the project repository: allow-list, forbidden paths, leak scan).

## Reproduce

Python 3.12 and [uv](https://docs.astral.sh/uv/) are required; pandoc for the Markdown with citations; the PDF is
optional (an external Pandoc → Typst publisher named by `EF_PUBLISHER`).

```
uv sync --extra dev
uv run pytest tests/test_seats.py tests/test_reference_implementation.py tests/test_itinerary.py tests/test_provenance.py -q
uv run python -X utf8 docs/article/replication/reference.py                  # Appendix E on the published tables
uv run ef seats allocate -k 25                                               # the official 2022 allocation
uv run ef seats allocate                                                     # the 2026 snapshot, margin of the last seat
uv run ef seats build --snapshot data/raw/knesset26/snapshots/{cur["file"]}  # results.json, tables/, figures/, the text
uv run python -X utf8 tools/verify_bib.py docs/article/references.bib        # Crossref check of the references
```

The command-line tool reconfigures its output to UTF-8; ad-hoc scripts on Windows run with `python -X utf8`.

## What is not here

The project's evidence base (witness statements, photographs, incident sources), the case files beyond the public
summary, the research notes, the project's planning and decision records and the audit trail of the AI sessions are
retained by the editor and are not released. The package is assembled from the private working repository by
`tools/build_replication_package.py`: an allow-list of paths, forbidden prefixes checked on every file, only git-tracked
files, and a scan of every text file for e-mail addresses, chat links, machine paths and the author's private redaction
rules; the public repository named above is filled from the same build and holds nothing else.

## Licence

Code: MIT (see `LICENSE`). The archived data are public records of the Central Elections Committee (station-level
results) and of the Central Bureau of Statistics (localities file), reproduced byte-exact for replication.
"""


#: Paths kept byte-exact (the repository's `.gitattributes` marks them `-text`): the archived data and every CSV table.
EXACT_PREFIXES = ("data/raw/",)
EXACT_SUFFIXES = (".csv",)
#: Text files packed with LF: the scanned text types plus the CSL style and the licence (text to git, not scanned).
LF_SUFFIXES = TEXT_SUFFIXES | {".csl"}
LF_NAMES = frozenset({"LICENSE", ".gitattributes"})


def normalize_bytes(rel: str, data: bytes) -> bytes:
    """The bytes a file takes in the package: byte-exact for the archived data and the CSV tables, LF line endings for
    every other text file, so that the manifest hashes hold on any platform's checkout of the package repository
    (a Windows working copy with `core.autocrlf` shows CRLF; compare against `git show HEAD:<path>` or the zip)."""
    if rel.startswith(EXACT_PREFIXES) or rel.endswith(EXACT_SUFFIXES):
        return data
    name = rel.rsplit("/", 1)[-1]
    if Path(name).suffix.lower() in LF_SUFFIXES or name in LF_NAMES:
        return data.replace(b"\r\n", b"\n")
    return data


def package_bytes(f: Path) -> bytes:
    """`normalize_bytes` of a file of the working copy."""
    return normalize_bytes(_rel(f), f.read_bytes())


def committed_bytes(files: list[Path], ref: str = "HEAD") -> dict[str, bytes]:
    """Published path → bytes of the files as committed at `ref` (`git archive`), normalised like the working-copy ones:
    what the public repository is filled from, so that a locked or edited working copy never leaks in."""
    import io
    import tarfile

    git = shutil.which("git")
    if git is None:
        raise RuntimeError("git is needed to read the committed tree")
    rels = [_rel(f) for f in files]
    out = subprocess.run([git, "-C", str(ROOT), "archive", "--format=tar", ref, "--", *rels], capture_output=True, check=True)  # noqa: S603
    data: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(out.stdout), mode="r:") as tar:
        for member in tar.getmembers():
            if member.isfile():
                fh = tar.extractfile(member)
                if fh is not None:
                    data[member.name] = normalize_bytes(member.name, fh.read())
    missing = [r for r in rels if r not in data]
    if missing:
        raise RuntimeError(f"not committed at {ref}: {missing[:5]}{' …' if len(missing) > 5 else ''}")
    return data


def package_contents(files: list[Path], committed: str | None = None) -> dict[str, bytes]:
    """Published path → bytes, for every file of the package plus the generated README: from the working copy (the
    zip), or, with `committed` set to a ref, from the committed tree (the public repository)."""
    body = committed_bytes(files, committed) if committed else {_rel(f): package_bytes(f) for f in files}
    return body | {"README.md": package_readme().encode("utf-8")}


def package_manifest(contents: dict[str, bytes], name: str, built_at: bool = True) -> str:
    """`MANIFEST.json`: the SHA-256 and size of every file of the package (the build time only in the zip, so that the
    public repository's manifest changes only when a file does)."""
    manifest: dict[str, object] = {"package": name, "version": __version__}
    if built_at:
        manifest["built_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    manifest["line_endings"] = "LF for text files; data/raw/** and *.csv byte-exact as downloaded or written"
    manifest["files"] = {rel: {"sha256": hashlib.sha256(b).hexdigest(), "bytes": len(b)} for rel, b in contents.items()}
    return json.dumps(manifest, ensure_ascii=False, indent=1)


def build(out: Path, files: list[Path]) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    contents = package_contents(files)
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for rel, b in contents.items():
            z.writestr(rel, b)
        z.writestr("MANIFEST.json", package_manifest(contents, out.name))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=ROOT / "dist" / f"replication-k26-{__version__}.zip")
    ap.add_argument("--check", action="store_true", help="list the files and run the scan; build nothing")
    args = ap.parse_args(argv)
    files = collect()
    private = load_private_rules()
    hits = scan(files, list(BUILTIN_RULES) + private)
    print(f"{len(files)} files, {sum(f.stat().st_size for f in files):,} bytes; private rules: {len(private)}"
          + ("" if private else f" (none: set {REDACTION_ENV} or create {DEFAULT_REDACTION_FILE})"))
    if args.check:
        for f in files:
            print(" ", _rel(f))
        print("  README.md (generated)")
    for path, rule, excerpt in hits:
        print(f"LEAK {rule:<13} {path}: {excerpt}")
    if hits:
        print(f"{len(hits)} match(es): the package is not built")
        return 1
    if args.check:
        return 0
    out = build(args.out, files)
    print(f"package -> {out} ({out.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
