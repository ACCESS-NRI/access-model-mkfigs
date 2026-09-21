"""The one end-to-end test for mkfigs.pushit.main(): the full happy path.

Exercises the full pushit.py flow (notebook classification -> Figshare
upload -> docs-tree copy -> mkdocs.yml nav update -> pages/index.md update)
against fake_paper_repo + fake_figshare, with pushit's own subprocess calls
(jupyter nbconvert) stubbed out so the test needs neither jupyter nor a
network connection installed to run.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

from mkfigs import pushit

from .conftest import make_png, make_rendered_notebook


def _seed_notebook_outputs(repo: Path, ename: str, notebooks: list[str]):
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


def _run_pushit(args, no_subprocess=True):
    """Invoke pushit.main() with a fake argv, stubbing subprocess.run by default."""
    with patch.object(sys, "argv", ["mkfigs-pushit", *args]):
        if no_subprocess:
            with patch.object(pushit.subprocess, "run") as mock_run:
                pushit.main()
                return mock_run
        pushit.main()


def test_full_run_uploads_and_builds_docs_tree(patch_repo_paths, fake_figshare, figshare_token):
    """The full happy path end to end: upload, docs tree, nav, index.md."""
    repo = patch_repo_paths
    ename = "test_experiment_01"
    _seed_notebook_outputs(repo, ename, ["SST", "MLD"])

    mock_run = _run_pushit([])

    exp_docs = repo / "documentation" / "docs" / "pages" / "experiments" / ename

    # Per-notebook markdown copied into the docs tree
    assert (exp_docs / "SST.md").exists()
    assert (exp_docs / "MLD.md").exists()

    # notebooks_urls.json has a real figshare URL for each notebook
    urls = json.loads((exp_docs / "notebooks_urls.json").read_text())
    assert urls["SST"].startswith("https://ndownloader.figshare.com/files/")
    assert urls["MLD"].startswith("https://ndownloader.figshare.com/files/")

    # image URL was rewritten in the copied markdown (not left as a local path)
    assert "/assets/experiments/" not in (exp_docs / "SST.md").read_text()

    # mkdocs.yml nav grew a Summary + Notebook entry per notebook
    mkdocs_yml = (repo / "documentation" / "mkdocs.yml").read_text()
    assert "SST" in mkdocs_yml and "MLD" in mkdocs_yml

    # pages/index.md has an experiment block for this run
    index_md = (repo / "documentation" / "docs" / "pages" / "index.md").read_text()
    assert f"<!-- experiment:{ename} -->" in index_md
    assert "SST" in index_md and "MLD" in index_md

    # rendered notebooks copied back into notebooks/, and nbconvert was
    # invoked to strip outputs from that copy (stubbed, so just check the call)
    assert (repo / "notebooks" / "SST.ipynb").exists()
    nbconvert_calls = [c for c in mock_run.call_args_list if "nbconvert" in c.args[0]]
    assert len(nbconvert_calls) == 2  # once per successful notebook
