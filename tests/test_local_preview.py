"""Tests for mkfigs-pushit --local and mkfigs-placeholder."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from mkfigs import pushit, placeholder

from .conftest import make_rendered_notebook, seed_notebook_outputs


def _run_pushit(args):
    with patch.object(sys, "argv", ["mkfigs-pushit", *args]):
        with patch.object(pushit.subprocess, "run"):
            pushit.main()


# ---------------------------------------------------------------------------
# --local
# ---------------------------------------------------------------------------

def test_local_copies_real_assets_and_never_touches_figshare(patch_repo_paths):
    repo = patch_repo_paths
    ename = "test_experiment_01"
    ofol, mdfol = seed_notebook_outputs(repo, ename, ["SST"])
    make_rendered_notebook(ofol / "SST_rendered.ipynb", cell_text="OUTPUT_BEARING_MARKER")

    _run_pushit(["--local"])

    exp_docs = repo / "documentation" / "docs" / "pages" / "experiments" / ename

    assert (repo / "documentation" / "docs" / "assets" / "experiments" / ename / "SST_01.png").exists()
    assert (exp_docs / "SST.md").exists()

    local_nb = exp_docs / "notebooks" / "SST.ipynb"
    assert local_nb.exists()
    assert "OUTPUT_BEARING_MARKER" in local_nb.read_text()

    assert not (exp_docs / "notebooks_urls.json").exists()


def test_local_does_not_clobber_an_existing_notebooks_urls_json(patch_repo_paths):
    repo = patch_repo_paths
    ename = "test_experiment_01"
    seed_notebook_outputs(repo, ename, ["SST"])
    exp_docs = repo / "documentation" / "docs" / "pages" / "experiments" / ename
    exp_docs.mkdir(parents=True, exist_ok=True)
    real_urls = {"SST": "https://ndownloader.figshare.com/files/999"}
    (exp_docs / "notebooks_urls.json").write_text(json.dumps(real_urls))

    _run_pushit(["--local"])

    assert json.loads((exp_docs / "notebooks_urls.json").read_text()) == real_urls


def test_local_still_updates_nav_and_index(patch_repo_paths):
    repo = patch_repo_paths
    ename = "test_experiment_01"
    seed_notebook_outputs(repo, ename, ["SST", "MLD"])

    _run_pushit(["--local"])

    mkdocs_yml = (repo / "documentation" / "mkdocs.yml").read_text()
    assert "SST" in mkdocs_yml and "MLD" in mkdocs_yml
    index_md = (repo / "documentation" / "docs" / "pages" / "index.md").read_text()
    assert ename in index_md


# ---------------------------------------------------------------------------
# mkfigs-placeholder
# ---------------------------------------------------------------------------

def test_make_placeholder_output_writes_expected_shape(tmp_path):
    notebooks_dir = tmp_path / "notebooks"
    notebooks_dir.mkdir()

    ofol = placeholder.make_placeholder_output("test_exp", ["SST", "MLD"], notebooks_dir)

    assert ofol == notebooks_dir / "mkfigs_output_test_exp"
    for nb in ["SST", "MLD"]:
        png = ofol / "mkmd" / f"{nb}_01.png"
        md = ofol / "mkmd" / f"{nb}.md"
        rendered = ofol / f"{nb}_rendered.ipynb"
        assert png.exists() and png.stat().st_size > 0
        assert md.exists()
        assert f"/assets/experiments/test_exp/{nb}_01.png" in md.read_text()
        assert rendered.exists()
        assert "Placeholder" in rendered.read_text()


def test_placeholder_main_parses_mkfigs_sh(patch_repo_paths, capsys):
    repo = patch_repo_paths

    with patch.object(sys, "argv", ["mkfigs-placeholder"]):
        placeholder.main()

    ofol = repo / "notebooks" / "mkfigs_output_test_experiment_01"
    assert (ofol / "mkmd" / "SST_01.png").exists()
    assert (ofol / "mkmd" / "MLD_01.png").exists()
    assert "mkfigs-pushit --local" in capsys.readouterr().out


def test_placeholder_then_local_produces_a_full_preview(patch_repo_paths):
    """End-to-end: placeholder output alone, fed straight into --local, with
    no real papermill run and no Figshare, produces a complete site preview.
    """
    repo = patch_repo_paths
    ename = "test_experiment_01"

    with patch.object(sys, "argv", ["mkfigs-placeholder"]):
        placeholder.main()

    _run_pushit(["--local"])

    exp_docs = repo / "documentation" / "docs" / "pages" / "experiments" / ename
    assert (exp_docs / "SST.md").exists()
    assert (exp_docs / "notebooks" / "SST.ipynb").exists()
    assert (repo / "documentation" / "docs" / "assets" / "experiments" / ename / "SST_01.png").exists()
    assert not (exp_docs / "notebooks_urls.json").exists()

    mkdocs_yml = (repo / "documentation" / "mkdocs.yml").read_text()
    assert "SST" in mkdocs_yml and "MLD" in mkdocs_yml
