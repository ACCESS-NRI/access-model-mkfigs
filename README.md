# access-model-mkfigs

[![CI](https://github.com/ACCESS-NRI/access-model-mkfigs/actions/workflows/ci.yml/badge.svg)](https://github.com/ACCESS-NRI/access-model-mkfigs/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/ACCESS-NRI/access-model-mkfigs/branch/master/graph/badge.svg)](https://codecov.io/gh/ACCESS-NRI/access-model-mkfigs)

Evaluation figure workflow tools for ACCESS model paper repositories.

* License: Apache-2.0

## Overview

`access-model-mkfigs` provides the `mkfigs` Python package and three command-line tools
used by ACCESS model paper repositories (e.g. `access-om3-paper-1`, `access-cm3-paper-1`)
to run analysis notebooks via papermill, upload figures and rendered notebooks to Figshare,
and build a MkDocs documentation site.

## Installation

```bash
pip install git+https://github.com/ACCESS-NRI/access-model-mkfigs.git
```

## Usage

In paper repo notebooks:

```python
from mkfigs import MkmdWriter
mkmd = MkmdWriter(esm_file, nbname, str(cwd), pm=papermill)
```

CLI entry points (called from `mkfigs.sh` or interactively on a login node):

```bash
mkfigs-run     --ename ENAME --esmdir ESMDIR --wfolder WFOLDER
mkfigs-pushit  [--dry-run] [--skip-figshare] [--check-figshare-upload]
mkfigs-restore [--ename ENAME] [--force]
```

## Previewing the website without Figshare

`mkfigs-preview [--ename E] [--serve] [--preview-dir DIR] [REPO]` renders the site from a run's `mkfigs_output_<ENAME>/` (finished
or still running) without uploading anything and without writing to your repo: it works on a copy of `documentation/` (uncommitted
edits included).
`pushit` runs from the paper repo's pinned engine (`external/access-model-mkfigs`, as in production) if that submodule is
initialised, otherwise from the installed package. It needs `mkdocs` and the docs plugins (`documentation/requirements.txt`) in
the current environment. It works in `<repo>-preview/` (or `--preview-dir`, which must lie outside the repo), kept between
runs so re-runs are fast. Without `--serve` it builds the site (`<repo>-preview/site`); with `--serve` it serves it on
http://127.0.0.1:8000/ and re-renders about a second after you edit the repo's `documentation/` or the run output (reload the
page). Earlier experiments' notebook pages are downloaded from Figshare in the real build, so their links show up as warnings in
`<repo>-preview/mkdocs.log`.

`mkfigs-preview-branch --fork <YOUR FORK URL> [--branch preview/NAME] [--ename E] [REPO]` prepares a throwaway `preview/...` branch so
that ReadTheDocs can render the site online (point a ReadTheDocs project at your fork and activate the branch). It commits only
`documentation/` on top of your committed HEAD, in `<repo>-preview-branch/clone`, makes the figure links relative (ReadTheDocs serves
under `/<version>/`), keeps the real Figshare manifests, refuses organisation repos, files over 50 MB and branches not named
`preview/...`, and prints the push command instead of pushing. Never merge such a branch; delete it afterwards.

## Testing

```bash
pip install -e ".[dev]"
pytest
```

The testing is layered by how close each part gets to the real Figshare API
(which has no sandbox for private accounts and no way to unpublish, so most
of it runs against an in-memory fake rather than the live service):

| Layer | What it covers | Run |
|---|---|---|
| Fast / mocked | Figshare upload/reuse logic, `pushit.py`'s end-to-end flow and its `--check-figshare-*` modes, `restore.py`, `run.py`'s papermill orchestration | `pytest` |
| Live Figshare (opt-in) | Creates and deletes one real private article — never publishes | `MKFIGS_LIVE_FIGSHARE_TESTS=1 FIGSHARE_TOKEN=... pytest tests/live/` |

Coverage report:

```bash
coverage run --source=mkfigs -m pytest
coverage report -m
```

See [`tests/README.md`](tests/README.md) for a detailed walkthrough — running
individual tests, the layered structure, and the opt-in live Figshare layer.
