# access-model-mkfigs

[![CI](https://github.com/ACCESS-NRI/access-model-mkfigs/actions/workflows/ci.yml/badge.svg)](https://github.com/ACCESS-NRI/access-model-mkfigs/actions/workflows/ci.yml)

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

The testing is layered by how close each part gets to the real Figshare API
(which has no sandbox for private accounts and no way to unpublish, so most
of it runs against an in-memory fake rather than the live service):

| Layer | What it covers | Run |
|---|---|---|
| Fast / mocked | Figshare upload/resume/dedup logic, both `--check-figshare-*` modes, `pushit.py`'s docs-tree building, `restore.py`, `run.py`'s papermill orchestration | `pytest` |
| Offline site render | Builds a small fixture site with `mkdocs build --strict` | `pip install -e ".[dev,docstest]"` then `pytest tests/test_mkdocs_build_offline.py` |
| Live Figshare (opt-in) | Creates and deletes one real private article — never publishes | `MKFIGS_LIVE_FIGSHARE_TESTS=1 FIGSHARE_TOKEN=... pytest tests/live/` |

Coverage report:

```bash
coverage run --source=mkfigs -m pytest
coverage report -m
```

