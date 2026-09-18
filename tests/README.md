# Running the test suite

Quick start:

```bash
pip install -e ".[dev]"
pytest
```

You should see `52 passed, 2 skipped` (or `51 passed, 3 skipped` if you
haven't installed the `docstest` extra below — the extra skip is
`test_mkdocs_build_offline.py::test_minimal_fixture_site_builds_cleanly`,
which skips itself cleanly rather than fail when the full `mkdocs` +
`mkdocs-material` + `mkdocs-jupyter` stack isn't all present).

## How it's organised

The suite is layered by how close each part gets to the real Figshare API,
because Figshare has no sandbox environment for private accounts and no way
to unpublish or delete a published article — see the comments in
`conftest.py` and `tests/live/conftest.py` for the full reasoning.

| File | Covers |
|---|---|
| `test_configdoc_unit.py` | Pure functions — `_md5`, `assign_pngs_to_notebooks`, `_classify_duplicates` — no mocking at all |
| `test_upload_parts.py` | The concurrent part-upload retry/resume logic, mocked at the `requests.put` level |
| `test_figshare_uploader.py` | `FigshareUploader` against `conftest.py`'s in-memory fake Figshare server |
| `test_restore.py` | `restore.py` against a mocked download (`urlretrieve`) |
| `test_run_notebook.py` | `run.py`'s papermill/nbconvert orchestration, with `subprocess.run` stubbed |
| `test_pushit_modes.py` | `pushit.main()` end to end — `--dry-run`, `--skip-figshare`, a full run, both `--check-figshare-*` modes |
| `test_run.py` | `_fix_kernel`'s private-copy-per-PID concurrency contract (the original test, predates the rest of this suite) |
| `test_mkdocs_build_offline.py` | Real `mkdocs build --strict` against a small fixture site — needs the `docstest` extra, see below |
| `live/` | Opt-in, creates and deletes one real private Figshare article — never publishes, see below |

Everything except the last two runs in well under a second, needs no
network access, and never touches a real Figshare account.

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
pytest -v -s tests/test_configdoc_unit.py
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
pytest -v -s --tb=long tests/test_upload_parts.py
```

A sensible order to go through file-by-file, starting with the simplest and
most self-contained and building up:

```bash
pytest -v -s tests/test_configdoc_unit.py
pytest -v -s tests/test_upload_parts.py
pytest -v -s tests/test_figshare_uploader.py
pytest -v -s tests/test_restore.py
pytest -v -s tests/test_run_notebook.py
pytest -v -s tests/test_pushit_modes.py
pytest -v -s tests/test_run.py
```

## Coverage

```bash
coverage run --source=mkfigs -m pytest
coverage report -m
```

## The offline mkdocs build check

`test_mkdocs_build_offline.py` runs a real `mkdocs build --strict` against
`fixtures/minimal_site/` — a small fixture doc tree shaped the way
`pushit.py` actually produces one, using the same `mkdocs-material` +
`mkdocs-jupyter` combination as the real paper-repo sites. It's
deliberately not a clone of the real `access-om3-paper-1` repo, which pulls
in a custom theme, several other plugins, and would make this suite slow
and fragile for reasons that have nothing to do with this repo's own bugs.

Needs the `docstest` extra, kept separate from `dev` so the fast layer
above never needs `mkdocs` installed at all:

```bash
pip install -e ".[dev,docstest]"
pytest tests/test_mkdocs_build_offline.py
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
