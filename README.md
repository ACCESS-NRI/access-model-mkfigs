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

## Figure explorer data

Every figure saved with `MkmdWriter.savefig` also writes its plotted line data
to `<notebook>_<NN>.lines.json` next to the PNG (see `mkfigs/linedata.py`).
`mkfigs-pushit` bundles these into `figure-data_<run>.json.gz`, uploads the
bundle with the PNGs, and adds the run to `documentation/figure-data.json`,
which is committed with the run's pages. The access-om3-live figure explorer
reads that index.

To create the data for runs already on the
[Experiments page](https://access-om3-configs.access-hive.org.au/latest/Experiments/),
run from the paper repo's `notebooks/` folder on Gadi:

```
mkfigs-backfill list                  # runs, forcing, tags and Figshare articles
mkfigs-backfill submit [--only RUN]   # one mkfigs.sh job per run, line-plot notebooks only
mkfigs-backfill upload [--only RUN]   # bundle, upload, check, publish, add to the index
```

`upload` publishes a new version of each run's Figshare article only after
Figshare's MD5 of the bundle matches the local file; `--no-publish` stops
before publishing and `--dry-run` changes nothing.

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
