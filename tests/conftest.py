"""Shared fixtures for testing mkfigs-pushit --local and mkfigs-placeholder.

Deliberately self-contained (no Figshare mocking, no shared fixture
library) -- --local structurally never reaches any Figshare code path,
so there's nothing to mock. If a future PR adds a project-wide
conftest.py with its own fake_paper_repo/patch_repo_paths fixtures,
expect a straightforward merge conflict here (two independently-created
fixture sets), not a functional dependency -- keep whichever version is
more complete and drop the other.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


def make_png(path: Path, payload: bytes = b"not-a-real-png-but-thats-fine") -> None:
    path.write_bytes(payload)


def make_rendered_notebook(path: Path, cell_text: str = "print('ok')") -> None:
    nb = {
        "cells": [{"cell_type": "code", "source": [cell_text], "outputs": [],
                   "execution_count": 1, "metadata": {}}],
        "metadata": {"kernelspec": {"display_name": "Python 3 (ipykernel)", "name": "python3"}},
        "nbformat": 4, "nbformat_minor": 5,
    }
    path.write_text(json.dumps(nb))


@pytest.fixture
def fake_paper_repo(tmp_path: Path) -> Path:
    """A minimal, real-shaped single-level paper repo (access-om3-paper-1's
    layout: mkfigs.sh directly in notebooks/), with just enough of
    documentation/ for pushit.py to operate on.
    """
    repo = tmp_path / "paper-repo"
    notebooks = repo / "notebooks"
    notebooks.mkdir(parents=True)

    (repo / "CITATION.cff").write_text(
        "cff-version: 1.2.0\nauthors:\n  - family-names: \"Bull\"\n    given-names: \"Chris\"\n"
    )

    (notebooks / "mkfigs.sh").write_text(
        "#!/bin/bash\n"
        "ENAME=test_experiment_01\n"
        "ESMDIR=/g/data/tm70/fake/${ENAME}/datastore.json\n"
        "array=(\n"
        "  SST\n"
        "  MLD\n"
        ")\n"
    )

    docs = repo / "documentation"
    (docs / "docs" / "pages").mkdir(parents=True)
    (docs / "docs" / "pages" / "index.md").write_text("# Placeholder\n\n<!-- experiments -->\n")
    (docs / "mkdocs.yml").write_text(
        "site_name: fake-paper-1\nnav:\n  - Home: pages/index.md\nplugins:\n  - search\n"
    )
    return repo


@pytest.fixture
def patch_repo_paths(monkeypatch, fake_paper_repo: Path):
    """Point mkfigs.pushit's module-level path globals at fake_paper_repo."""
    from mkfigs import pushit

    notebooks_dir = fake_paper_repo / "notebooks"
    monkeypatch.setattr(pushit, "HERE", notebooks_dir)
    monkeypatch.setattr(pushit, "REPO", fake_paper_repo)
    monkeypatch.setattr(pushit, "DOCS_ROOT", fake_paper_repo / "documentation" / "docs")
    monkeypatch.setattr(pushit, "DOCS_PAGES", fake_paper_repo / "documentation" / "docs" / "pages")
    monkeypatch.setattr(pushit, "MKDOCS_YML", fake_paper_repo / "documentation" / "mkdocs.yml")
    return fake_paper_repo


@pytest.fixture(autouse=True)
def _no_nci_gate(monkeypatch):
    """Neutralise the _check_nci_environment() guard so tests don't need
    the real NCI conda module environment.
    """
    from mkfigs import pushit
    monkeypatch.setattr(pushit, "_check_nci_environment", lambda: None)


def seed_notebook_outputs(repo: Path, ename: str, notebooks: list[str]):
    """Populate mkfigs_output_<ename>/ as if mkfigs-run had already
    executed successfully for each notebook in *notebooks*.
    """
    ofol = repo / "notebooks" / f"mkfigs_output_{ename}"
    mdfol = ofol / "mkmd"
    mdfol.mkdir(parents=True)
    for nb in notebooks:
        make_rendered_notebook(ofol / f"{nb}_rendered.ipynb")
        make_png(mdfol / f"{nb}_01.png")
        (mdfol / f"{nb}.md").write_text(
            f"# {nb}\n\n![fig](/assets/experiments/{ename}/{nb}_01.png)\n"
        )
    return ofol, mdfol
