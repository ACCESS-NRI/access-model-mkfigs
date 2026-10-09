# access-model-mkfigs

[![CI](https://github.com/ACCESS-NRI/access-model-mkfigs/actions/workflows/ci.yml/badge.svg)](https://github.com/ACCESS-NRI/access-model-mkfigs/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/ACCESS-NRI/access-model-mkfigs/branch/master/graph/badge.svg)](https://codecov.io/gh/ACCESS-NRI/access-model-mkfigs)

Evaluation figure workflow for ACCESS model paper repositories.

* License: Apache-2.0

## Overview

`access-model-mkfigs` runs a paper repo's analysis notebooks against one experiment on NCI's Gadi,
uploads the figures and rendered notebooks to Figshare, and adds the results to the paper repo's
MkDocs site. It is used by:

* [`access-om3-paper-1`](https://github.com/ACCESS-Community-Hub/access-om3-paper-1)
* [`access-cm3-paper-1`](https://github.com/ACCESS-Community-Hub/access-cm3-paper-1)

The two repos are laid out slightly differently:

| | access-om3-paper-1 | access-cm3-paper-1 |
|---|---|---|
| `mkfigs.sh` and notebooks | `notebooks/` | `notebooks/polished-python/` |
| Extra settings in `mkfigs.sh` | none (OM3 is the default) | `MKFIGS_MODEL_NAME`, `MKFIGS_REPO_URL`, `--notebooks-subdir polished-python` (see [Other models](#other-models)) |

Below, **"the `mkfigs.sh` directory"** means `notebooks/` for OM3 and `notebooks/polished-python/` for CM3.

There are three steps:

| Step | Command | Where it runs | What it does |
|---|---|---|---|
| 1. Run | `qsub mkfigs.sh` | PBS job | Runs every notebook listed in `mkfigs.sh` with papermill and saves the results to `mkfigs_output_<ENAME>/` next to `mkfigs.sh` |
| 2. Upload | `python3 -m mkfigs.pushit` | Login node (needs internet) | Uploads results to Figshare and adds pages to the docs site |
| 3. Check | `python3 -m mkfigs.pushit --check-figshare-upload` | Login node | After you publish on Figshare: checks the links work and prints the `git` commands to commit and tag |

## How paper repos get the package

Paper repos do **not** `pip install` this package. It is included as a git submodule at
`external/access-model-mkfigs`, pinned to a commit, and put on the Python path:

* `mkfigs.sh` sets `PYTHONPATH` before running notebooks.
* Notebooks run `import mkfigs_bootstrap`, which does the same for interactive sessions.
* For login-node commands, you set `PYTHONPATH` yourself (below).

This keeps batch runs, interactive notebooks and follow-up commands on the same version. It is a
stop-gap until the package is available in the `conda/analysis3` environment.

## One-off setup

```bash
# 1. Figshare personal token (Figshare > Applications and API > Personal tokens)
echo "<your-token>" > ~/.figshare_token && chmod 600 ~/.figshare_token
#    (or export FIGSHARE_TOKEN=<your-token>)

# 2. Clone the paper repo WITH its submodule (OM3 shown; CM3 is the same)
cd /g/data/$PROJECT/$USER
git clone --recurse-submodules git@github.com:ACCESS-Community-Hub/access-om3-paper-1.git
# git clone --recurse-submodules git@github.com:ACCESS-Community-Hub/access-cm3-paper-1.git
#    already cloned without it? from inside the clone:
#    git submodule update --init --recursive
```

## Login-node environment

All `python3 -m mkfigs.*` commands must be run **from the `mkfigs.sh` directory**
(they read `ENAME`, `ESMDIR` and the notebook list from `mkfigs.sh`):

```bash
module purge
module use /g/data/xp65/public/modules
module load conda/analysis3        # use the same module as in mkfigs.sh (the CM3 paper repo pins conda/analysis3-26.08)
cd /g/data/$PROJECT/$USER/access-om3-paper-1/notebooks
# cd /g/data/$PROJECT/$USER/access-cm3-paper-1/notebooks/polished-python
export PYTHONPATH="$(git rev-parse --show-toplevel)/external/access-model-mkfigs/src:${PYTHONPATH}"
```

## Workflow: first run for an experiment

1. In `mkfigs.sh`, set `ENAME` and `ESMDIR` and edit the notebook `array`.
   If several `ENAME=`/`ESMDIR=` lines are uncommented, **the last one wins** (for both bash and
   `pushit`), so comment out the others to avoid surprises.
2. Make sure the experiment's storage project is in the `#PBS -l storage=` line.
3. Optional (OM3): `python3 check_mkfigs_issues.py` checks every notebook in the array exists and has
   a GitHub issue in `mkfigs_issues.py`.
4. Check, run, and look at what happened:

   ```bash
   git submodule status        # must start with a space; a leading "-" means: git submodule update --init
   cd notebooks                # the directory that contains mkfigs.sh (CM3: notebooks/polished-python)
   qsub mkfigs.sh              # submit from this directory: mkfigs.sh finds the repo root from here
   ```

   **The job exits 0 even if some notebooks failed**, so read the results. The log files are in
   `mkfigs_output_<ENAME>/mkmd/` (not next to the notebooks):

   ```bash
   grep -hE "Succeeded|FAILED" mkfigs.sh.o*     # which notebooks passed and which failed
   grep -A2 '^FAILED:' mkfigs_output_*/mkmd/mkfigs_errors.log | grep -v '^===\|^--'   # the error for each
   ```

   Every notebook gets a `*_rendered.ipynb` in `mkfigs_output_<ENAME>/`, **including failed ones**, so
   its presence does not mean it worked. (`mkfigs.sh.e*` is mostly a `set -x` trace plus Dask shutdown
   noise; `mkfigs_errors.log` and the `.o` summary are what to read.)

5. Upload (in the login-node environment above):

   ```bash
   python3 -m mkfigs.pushit --dry-run                    # optional preview, changes nothing
   python3 -m mkfigs.pushit
   python3 -m mkfigs.pushit --check-figshare-integrity   # optional, before publishing
   ```

   The `--dry-run` table lists each notebook in the `array`:

   | Status | Meaning |
   |---|---|
   | `OK (n PNGs)` | ran and produced figures; will be uploaded |
   | `FAILED (no PNGs or markdown)` | errored (see `mkfigs_errors.log`) **or** ran but saved no figures |
   | `NOT RUN` | in the `array` but no output from this run |
   | `PREV COMMITTED` | not in this run; kept from the last published version |

6. Log in to Figshare and publish the article.
7. `python3 -m mkfigs.pushit --check-figshare-upload`, then run the `git` commands it prints. These
   commit the docs pages and create a tag named `<ENAME>-YYYY.MM.NNN`.

## Workflow: add or re-run notebooks for an existing experiment

```bash
git fetch --tags
git checkout <ENAME>-YYYY.MM.NNN           # tag printed by the last --check-figshare-upload
git submodule update --init --recursive    # in case the pinned commit changed
python3 -m mkfigs.restore                  # downloads the previously published notebooks
```

Then edit the notebook `array` in `mkfigs.sh` (add new notebooks, or keep only the ones to re-run)
and continue from step 4 above. `pushit` merges new results with the restored ones; new results
take priority.

## Command reference

| Command | Purpose |
|---|---|
| `mkfigs.run --ename E --esmdir D --wfolder W [--notebooks-subdir DIR]` | Run the notebooks. Called by `mkfigs.sh`; you rarely run it directly. The notebook list comes from the `MKFIGS_NOTEBOOKS` variable that `mkfigs.sh` sets. |
| `mkfigs.pushit` | Upload results to Figshare and update the docs site. |
| `mkfigs.pushit --dry-run` | Show what would happen without changing anything. |
| `mkfigs.pushit --skip-figshare` | Update the docs site only, no upload. |
| `mkfigs.pushit --check-figshare-integrity [--fix-duplicates]` | Before publishing: check every upload is complete and not duplicated. `--fix-duplicates` removes duplicates that are safe to remove. |
| `mkfigs.pushit --check-figshare-upload` | After publishing: check the public links work and print the `git` commands. |
| `mkfigs.restore [--force]` | Download previously published notebooks so you can add to an experiment. `--force` downloads again even if present. |
| `--ename E` (`pushit`, `restore`) | Use a different experiment name from the one in `mkfigs.sh`. |

`pushit` reads `ENAME`, `ESMDIR`, the notebook `array` and any `MKFIGS_MODEL_NAME`/`MKFIGS_REPO_URL`
exports from `mkfigs.sh`; `restore` reads `ENAME`.

## What a paper repo needs

```
<paper-repo>/
├── CITATION.cff                     # authors, shown on the docs pages
├── documentation/mkdocs.yml         # pushit updates its nav: block
├── documentation/docs/pages/        # pushit writes experiment pages here
├── external/access-model-mkfigs/    # this repo, as a submodule
└── notebooks/                       # OM3: files below sit here
    └── polished-python/             # CM3: files below sit here
        ├── mkfigs.sh                # PBS script: ENAME, ESMDIR, notebook array
        ├── mkfigs_bootstrap.py      # adds the submodule to sys.path in notebooks
        ├── mkfigs_issues.py         # optional: ISSUES dict, notebook -> GitHub issue link(s)
        └── <notebook>.ipynb
```

`pushit` and `restore` find the repo root by walking up from the `mkfigs.sh` directory to the first folder
containing `.git` or `documentation/mkdocs.yml`. `mkfigs_bootstrap.py` uses a fixed path, so it
differs by one `.parent` between the two repos.

Each notebook starts with a cell tagged `parameters` (papermill replaces these values; other
variables such as CM3's `dpi` can be added):

```python
esm_file = "/g/data/.../datastore.json"   # experiment to analyse when run by hand
papermill = False
cwd = None
nbname = None
```

followed by:

```python
if not papermill:
    import nci_ipynb, os
    cwd = nci_ipynb.dir()
    nbname = nci_ipynb.name()
    os.chdir(cwd)
import mkfigs_bootstrap
from mkfigs import MkmdWriter
mkmd = MkmdWriter(esm_file, nbname, str(cwd), pm=papermill)
```

Then, to add a figure or table to the docs page:

```python
mkmd.savefig(fig, "Title", "Caption.", dpi=150)
mkmd.table("Title", df.to_markdown().split("\n"))
```

## Upgrading the pinned version (in a paper repo)

```bash
cd external/access-model-mkfigs
git fetch --tags && git checkout <tag-or-commit>
cd ../..
git add external/access-model-mkfigs
git commit -m "Bump access-model-mkfigs to <tag-or-commit>"
```

Everyone else then runs `git pull && git submodule update --init --recursive`.

## Other models

The defaults are for ACCESS-OM3. Other paper repos set these in `mkfigs.sh`. `pushit` also reads
the two `export` lines from `mkfigs.sh` on the login node, so there is nothing extra to set there:

| Setting | Purpose | Example (`access-cm3-paper-1`) |
|---|---|---|
| `export MKFIGS_MODEL_NAME=...` | Model name in the Figshare article (title, description, keywords) and on each notebook's docs page | `ACCESS-CM3` |
| `export MKFIGS_REPO_URL=...` | Paper repo link on Figshare | `https://github.com/ACCESS-Community-Hub/access-cm3-paper-1` |
| `--notebooks-subdir DIR` (to `mkfigs.run`) | Notebooks are in `notebooks/DIR/` rather than `notebooks/` | `polished-python` |

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
