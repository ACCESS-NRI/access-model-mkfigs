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

## Testing

```bash
pip install -e ".[dev]"
pytest
```

The fast suite is a single end-to-end test of the `pushit` happy path
(notebook classification -> Figshare upload -> docs-tree copy -> mkdocs.yml
nav update -> pages/index.md update), run against an in-memory fake
Figshare rather than the live service (which has no sandbox for private
accounts and no way to unpublish):

| Layer | What it covers | Run |
|---|---|---|
| Fast / mocked | `pushit.py`'s full happy-path flow, end to end | `pytest` |
| Live Figshare (opt-in) | Creates and deletes one real private article — never publishes | `MKFIGS_LIVE_FIGSHARE_TESTS=1 FIGSHARE_TOKEN=... pytest tests/live/` |

Coverage report:

```bash
coverage run --source=mkfigs -m pytest
coverage report -m
```

See [`tests/README.md`](tests/README.md) for more, including the opt-in
live Figshare layer.

