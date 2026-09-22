# Running the test suite

Quick start:

```bash
pip install -e ".[dev]"
pytest
```

You should see `35 passed`. No flags, no environment variables, no optional
extras needed — plain `pytest` runs the whole thing every time.

## How it's organised

This suite deliberately covers only the four files closest to the real
`pushit` workflow, mocked against an in-memory fake Figshare rather than a
real account — Figshare has no sandbox environment for private accounts and
no way to unpublish or delete a published article, see the comments in
`conftest.py` for the full reasoning.

| File | Covers |
|---|---|
| `test_pushit_modes.py` | The main `pushit` command end to end — a normal run, dry runs, and the two safety-check modes |
| `test_figshare_uploader.py` | Uploading files to Figshare — including reusing files already there, and replacing ones that changed |
| `test_restore.py` | Downloading previously-uploaded figures and notebooks onto a fresh machine |
| `test_run_notebook.py` | Running a notebook and handling failures |
| `live/` | The one test that talks to the real Figshare — see below |

Everything except the last runs in well under a second, needs no network
access, and never touches a real Figshare account.

## Running one test at a time

List every test without running anything — a good first step to see the
shape of the suite:

```bash
pytest --collect-only -q
```

Run one file, verbose, with any print output visible (`-v` names each test
as it runs; `-s` shows anything the test or the code under test prints —
useful here, since a lot of `configdoc.py`/`pushit.py` prints
`[figshare] ...`-style progress messages as they go):

```bash
pytest -v -s tests/test_pushit_modes.py
```

Run a single test function — copy the `::`-qualified name straight out of
`--collect-only` or `-v` output:

```bash
pytest -v -s tests/test_figshare_uploader.py::test_get_or_create_article_finds_existing_by_title_search
```

Stop at the first failure instead of running everything and drowning in
output:

```bash
pytest -x -v -s tests/test_pushit_modes.py
```

Full tracebacks, if the default summary isn't enough:

```bash
pytest -v -s --tb=long tests/test_restore.py
```

A sensible order to go through file-by-file, starting with the simplest and
most self-contained and building up — and whether it's worth inspecting the
actual files each one creates afterwards (see `--basetemp` below for how to
browse them):

| Run | Worth inspecting the files it creates? |
|---|---|
| `pytest -v -s tests/test_figshare_uploader.py` | Yes — rewritten markdown with real (fake) Figshare URLs, a `figshare_manifest.json` |
| `pytest -v -s tests/test_restore.py` | Yes — a whole fake repo tree with downloaded notebooks and copied `.md` files |
| `pytest -v -s tests/test_run_notebook.py` | Yes — `mkfigs_run.log`/`mkfigs_errors.log`, plus the CLI's own "next steps" instructions via `-s` |
| `pytest -v -s tests/test_pushit_modes.py` | Yes, the most — a full fake paper-repo tree: docs markdown, `notebooks_urls.json`, an updated `mkdocs.yml`, copied-back notebooks |

To actually browse what one of these produces, point `--basetemp` at a
fixed location for the whole file rather than a single test — each test
function gets its own subfolder underneath, plus a `<test_name>_current`
symlink that always points at its most recent run (handy since the exact
subfolder name gets a random numeric suffix that changes between runs):

```bash
pytest -s --basetemp=/tmp/mkfigs-inspect tests/test_pushit_modes.py
ls /tmp/mkfigs-inspect/            # find the *_current symlink you want
find /tmp/mkfigs-inspect/<name>_current -type f
```

`--basetemp` wipes its contents at the *start* of the next run pointed at
the same path, so look before you run again, or use a different path
(`/tmp/mkfigs-inspect-2`, etc.) to keep more than one run's output around
at once.

## Coverage

```bash
coverage run --source=mkfigs -m pytest
coverage report -m
```

## The opt-in live Figshare test

`tests/live/` is the one place in this suite allowed to talk to real
Figshare. It creates a genuinely new **private** article, uploads a small
fixture file to it through the real `FigshareUploader.upload()` path,
verifies it via the private-state API, then deletes the article — every
time, whether the test passes or fails. It never calls the publish
endpoint.

Skipped by default. Needs **both** of:

```bash
export MKFIGS_LIVE_FIGSHARE_TESTS=1
export FIGSHARE_TOKEN=...   # a real personal Figshare token
pytest tests/live/ -v
```

Deliberately gated behind two flags rather than one, so a token merely
being present on a dev machine (e.g. for normal NCI use) can't cause silent
production API calls on every ordinary `pytest` run. This should only ever
run in a manually-triggered or scheduled CI job, never on every push.
