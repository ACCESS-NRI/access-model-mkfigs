# Running the test suite

Quick start:

```bash
pip install -e ".[dev]"
pytest
```

You should see `52 passed, 2 skipped` if this environment happens to have
the full `mkdocs` + `mkdocs-material` + `mkdocs-jupyter` stack available
(however it got there — see "The mkdocs build check" below), or
`51 passed, 3 skipped` if it doesn't. Either outcome is fine — nothing to
worry about, and no separate command needed either way; plain `pytest`
always picks it up.

## How it's organised

The suite is layered by how close each part gets to the real Figshare API,
because Figshare has no sandbox environment for private accounts and no way
to unpublish or delete a published article — see the comments in
`conftest.py` and `tests/live/conftest.py` for the full reasoning.

| File | Covers |
|---|---|
| `test_configdoc_unit.py` | Small helper functions, tested on their own |
| `test_upload_parts.py` | What happens if an upload to Figshare is interrupted or fails partway through, and how it picks back up |
| `test_figshare_uploader.py` | Uploading files to Figshare — including reusing files already there, and replacing ones that changed |
| `test_restore.py` | Downloading previously-uploaded figures and notebooks onto a fresh machine |
| `test_run_notebook.py` | Running a notebook and handling failures |
| `test_pushit_modes.py` | The main `pushit` command end to end — a normal run, dry runs, and the two safety-check modes |
| `test_run.py` | A fix for a bug where running notebooks at the same time could clash with each other |
| `test_mkdocs_build_offline.py` | Building the documentation website, to catch broken pages before they go live |
| `live/` | The one test that talks to the real Figshare — see below |

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

Not every file leaves behind files worth inspecting afterwards — some are
pure in-memory functions with nothing on disk to look at once the test
finishes:

| File | Worth inspecting the files it creates? |
|---|---|
| `test_configdoc_unit.py` | No |
| `test_upload_parts.py` | No — watch it with `-s` instead |
| `test_run.py` | No |
| `test_figshare_uploader.py` | Yes — rewritten markdown with real (fake) Figshare URLs, a `figshare_manifest.json` |
| `test_restore.py` | Yes — a whole fake repo tree with downloaded notebooks and copied `.md` files |
| `test_run_notebook.py` | Yes — `mkfigs_run.log`/`mkfigs_errors.log`, plus the CLI's own "next steps" instructions via `-s` |
| `test_pushit_modes.py` | Yes, the most — a full fake paper-repo tree: docs markdown, `notebooks_urls.json`, an updated `mkdocs.yml`, copied-back notebooks |

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

## The mkdocs build check

`test_mkdocs_build_offline.py` runs a real `mkdocs build --strict` against
`fixtures/minimal_site/` — a small fixture doc tree shaped the way
`pushit.py` actually produces one, using the same `mkdocs-material` +
`mkdocs-jupyter` combination as the real paper-repo sites. It's
deliberately not a clone of the real `access-om3-paper-1` repo, which pulls
in a custom theme, several other plugins, and would make this suite slow
and fragile for reasons that have nothing to do with this repo's own bugs.

It skips itself automatically — no flag or special invocation needed — if
`mkdocs`, `mkdocs-material`, and `mkdocs-jupyter` aren't all importable in
whatever environment you're running `pytest` from, and it runs as part of
a plain `pytest` once they are. The `docstest` extra below is just one way
to get them there; if your environment already has them for some other
reason (e.g. you also use it for real docs work), this test runs
automatically without you doing anything further:

```bash
pip install -e ".[dev,docstest]"
pytest
```

(or `pytest tests/test_mkdocs_build_offline.py` to run just this file)

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
