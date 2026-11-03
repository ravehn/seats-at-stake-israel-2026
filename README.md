# Seats at Stake — replication package

Replication package of: *Seats at Stake: A Conditional Estimate of the Knesset Seats Movable by a One-Sided Replacement
of Outdoor Campaign Advertising, Israel 2026* — an article edited by Raveh Neeman (independent researcher);
Deposited at Zenodo: https://doi.org/10.5281/zenodo.23879734 (all versions).

**Read the article:** [open the PDF](https://ravehn.github.io/seats-at-stake-israel-2026/docs/article/seat-estimate-k26.pdf)
(GitHub Pages, always the current `main`) · [download the PDF of the latest release](https://github.com/ravehn/seats-at-stake-israel-2026/releases/latest/download/seat-estimate-k26.pdf)
· [PDF on Zenodo](https://doi.org/10.5281/zenodo.23879734) (latest version, with preview) · [Markdown](docs/article/seat-estimate-k26.md).
(GitHub's inline preview of `docs/article/seat-estimate-k26.pdf` may show «Unable to render code block»; the file itself is
intact — use the links above.)

Public repository of this package: https://github.com/ravehn/seats-at-stake-israel-2026 (the same files; releases are tagged). Zenodo: DOI
[10.5281/zenodo.23879734](https://doi.org/10.5281/zenodo.23879734) for all versions (each release has its own DOI on the record; v1.0.0:
[10.5281/zenodo.23879735](https://doi.org/10.5281/zenodo.23879735)). Built from the author's private working repository, package version
1.0.4-dev, results generated 2026-11-03T13:14:51+00:00.
Data version of the build: CEC station file `expb_20261030T121730Z.csv` (snapshot 20261030T121730Z, SHA-256 `858b87d94c409759ea6ab2d22cc1c61fc9ff8bf3b77be669ba83e498c2cea021`),
2022 station file `cc223336-07bc-485d-b160-62df92967c0a.csv` (SHA-256 `07c71db1bf844d40cbc4d96c606550f2ada3c9f258287d08679180d0b9a413bc`), CBS localities file `bycode2024_idi_mirror.xlsx` (SHA-256
`de33d0ef836051a8769afa202b43df999114053001bce46164bf5a6976ea5dc1`), code fingerprint `67b801fc5cb04b8a66c069d4e25880580a798072bde542164ebed746ff472291`. `MANIFEST.json` lists the SHA-256 of every file here: the
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
  was assembled from the project repository: allow-list, forbidden paths, leak scan); `.zenodo.json` — the metadata of
  the Zenodo record made from a release of the public repository.

## Reproduce

Python 3.12 and [uv](https://docs.astral.sh/uv/) are required; pandoc for the Markdown with citations; the PDF is
optional (an external Pandoc → Typst publisher named by `EF_PUBLISHER`).

```
uv sync --extra dev
uv run pytest tests/test_seats.py tests/test_reference_implementation.py tests/test_itinerary.py tests/test_provenance.py -q
uv run python -X utf8 docs/article/replication/reference.py                  # Appendix E on the published tables
uv run ef seats allocate -k 25                                               # the official 2022 allocation
uv run ef seats allocate                                                     # the 2026 snapshot, margin of the last seat
uv run ef seats build --snapshot data/raw/knesset26/snapshots/expb_20261030T121730Z.csv  # results.json, tables/, figures/, the text
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
