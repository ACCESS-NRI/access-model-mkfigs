# Running the test suite

Quick start:

```bash
pip install -e ".[dev]"
pytest
```

You should see `1 passed`. No flags, no environment variables, no optional
extras needed.

## How it's organised

This suite covers only the single happy-path run of the `pushit` command
— the workflow the rest of the tooling exists to support — mocked against
an in-memory fake Figshare rather than a real account. Figshare has no
sandbox environment for private accounts and no way to unpublish or delete
a published article; see the comments in `conftest.py` for the full
reasoning.

| File | Covers |
|---|---|
| `test_pushit_modes.py` | One test: a full `pushit` run — notebook classification, Figshare upload, docs-tree copy, mkdocs.yml nav update, pages/index.md update |
| `live/` | The one test that talks to the real Figshare — see below |

The fast test runs in well under a second, needs no network access, and
never touches a real Figshare account.

## Running it

```bash
pytest -v -s tests/test_pushit_modes.py
```

(`-v` names the test as it runs; `-s` shows anything the test or the code
under test prints — useful here, since a lot of `configdoc.py`/`pushit.py`
prints `[figshare] ...`-style progress messages as they go.)

Full traceback, if the default summary isn't enough:

```bash
pytest -v -s --tb=long tests/test_pushit_modes.py
```

Worth inspecting the files it creates afterwards — a full fake paper-repo
tree: docs markdown, `notebooks_urls.json`, an updated `mkdocs.yml`,
copied-back notebooks. Point `--basetemp` at a fixed location to browse it
— the test gets its own subfolder underneath, plus a `<test_name>_current`
symlink that always points at its most recent run (handy since the exact
subfolder name gets a random numeric suffix that changes between runs):

```bash
pytest -s --basetemp=/tmp/mkfigs-inspect tests/test_pushit_modes.py
ls /tmp/mkfigs-inspect/            # find the *_current symlink
find /tmp/mkfigs-inspect/*_current -type f
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

Coverage from this one test is necessarily partial — it exercises the
happy path through `pushit.py`, `configdoc.py`'s upload/rewrite logic, and
enough of `restore.py`/`run.py` to be invoked indirectly, but none of
their edge cases or failure modes.

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
