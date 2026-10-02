# Running the test suite

Quick start:

```bash
pip install -e ".[dev]"
pytest
```

This is deliberately a small safety test suite around the major existing workflows.

The baseline covers:

- notebook execution and failure reporting
- Figshare article/file upload and reconciliation
- restoring previously pushed notebook output
- the main `pushit` workflow
- safety around the optional real-Figshare smoke tests

## How it's organised

| File | Covers |
|---|---|
| `test_pushit_modes.py` | The main `pushit` command end to end — a normal run, dry runs, and the two safety-check modes |
| `test_figshare_uploader.py` | Core Figshare article/file upload, reconciliation, and markdown rewriting |
| `test_restore.py` | Rebuilding previously pushed local output and overwrite behaviour |
| `test_run_notebook.py` | The main notebook execution path and surfacing notebook failures |
| `test_live_safety.py` | Ensures real-Figshare tests require explicit opt-in |
| `live/` | Optional smoke tests against real private Figshare articles |

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

`tests/live/` contains a small set of smoke tests that exercise the real Figshare API against newly created **private** articles. They currently verify that:

- a real private file upload completes and Figshare reports the expected checksum
- uploading identical content again reuses the existing remote file rather than creating a duplicate.

The live tests never publish an article. Each test uses a unique private article and cleanup removes it afterwards.

They are skipped by default. Running them requires both:

```bash
export MKFIGS_LIVE_FIGSHARE_TESTS=1
export FIGSHARE_TOKEN=...   # a real personal Figshare token
pytest tests/live/ -v
```

Having a Figshare token configured by itself is not enough to enable the tests. `tests/test_live_safety.py` verifies that both the explicit opt-in flag and a token are required.

These tests should not run as part of ordinary per-PR CI. If automated, they are better suited to a manually triggered or scheduled workflow.
