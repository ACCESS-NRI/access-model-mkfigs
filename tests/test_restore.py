"""
Below tests cover rebuilding previously pushed output and the policy
for preserving or replacing existing local work.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from mkfigs import restore


@pytest.fixture
def pushed_experiment(tmp_path: Path):
    """Build a repo tree as it would look right after mkfigs-pushit has
    already run once: notebooks/mkfigs.sh + notebooks_urls.json/summary
    markdown already committed into the docs tree, but the local
    mkfigs_output_<ename>/ working directory absent (e.g. a fresh clone,
    or a new machine) -- exactly the situation mkfigs-restore exists for.
    """
    repo = tmp_path / "paper-repo"
    notebooks = repo / "notebooks"
    notebooks.mkdir(parents=True)
    (notebooks / "mkfigs.sh").write_text("ENAME=test_experiment_01\nESMDIR=/fake\n")

    exp_docs = repo / "documentation" / "docs" / "pages" / "experiments" / "test_experiment_01"
    exp_docs.mkdir(parents=True)
    (exp_docs / "notebooks_urls.json").write_text(json.dumps({
        "SST": "https://ndownloader.figshare.com/files/111",
        "MLD": "https://ndownloader.figshare.com/files/222",
        "_run_times": {"SST": "2026-01-01 00:00 UTC"},  # must be skipped, not "restored"
    }))
    (exp_docs / "SST.md").write_text("# SST\n")
    (exp_docs / "MLD.md").write_text("# MLD\n")
    return repo


def _run_restore(args, cwd):
    """Run restore.main() with a fake argv from a given working directory."""
    with patch.object(sys, "argv", ["mkfigs-restore", *args]):
        old_cwd = Path.cwd()
        import os
        os.chdir(cwd)
        try:
            restore.main()
        finally:
            os.chdir(old_cwd)


def test_restore_rebuilds_output_from_committed_content(pushed_experiment, monkeypatch):
    """
    Restore rendered notebooks and markdown from committed metadata.
    """
    notebooks_dir = pushed_experiment / "notebooks"
    downloaded = {}

    def fake_urlretrieve(url, dest):
        content = {
            "https://ndownloader.figshare.com/files/111": '{"notebook": "SST"}',
            "https://ndownloader.figshare.com/files/222": '{"notebook": "MLD"}',
        }[url]
        Path(dest).write_text(content)
        downloaded[url] = Path(dest)

    monkeypatch.setattr(restore.urllib.request, "urlretrieve", fake_urlretrieve)

    _run_restore([], notebooks_dir)

    ofol = notebooks_dir / "mkfigs_output_test_experiment_01"

    assert set(downloaded) == {
        "https://ndownloader.figshare.com/files/111",
        "https://ndownloader.figshare.com/files/222",
    }

    assert (ofol / "SST_rendered.ipynb").read_text() == '{"notebook": "SST"}'
    assert (ofol / "MLD_rendered.ipynb").read_text() == '{"notebook": "MLD"}'

    assert (ofol / "mkmd" / "SST.md").read_text() == "# SST\n"
    assert (ofol / "mkmd" / "MLD.md").read_text() == "# MLD\n"


def test_restore_preserves_existing_notebook_by_default(pushed_experiment, monkeypatch):
    """
    Restore must not overwrite an existing rendered notebook by default
    """
    notebooks_dir = pushed_experiment / "notebooks"
    ofol = notebooks_dir / "mkfigs_output_test_experiment_01"
    ofol.mkdir(parents=True)
    sst = ofol / "SST_rendered.ipynb"
    sst.write_text("locall SST")

    def fake_urlretrieve(url, dest):
        Path(dest).write_text(f"downloaded from {url}")

    monkeypatch.setattr(restore.urllib.request, "urlretrieve", fake_urlretrieve)

    _run_restore([], notebooks_dir)

    # Existing local work is preserved
    assert sst.read_text() == "locall SST"

    # Missing notebooks are still restored normally
    assert (ofol / "MLD_rendered.ipynb").read_text() == "downloaded from https://ndownloader.figshare.com/files/222"


def test_restore_force_replaces_existing_notebook(pushed_experiment, monkeypatch):
    """
    --force should replace an existing rendered notebook
    """
    notebooks_dir = pushed_experiment / "notebooks"
    ofol = notebooks_dir / "mkfigs_output_test_experiment_01"
    ofol.mkdir(parents=True)
    sst = ofol / "SST_rendered.ipynb"
    sst.write_text("locall SST")

    def fake_urlretrieve(url, dest):
        Path(dest).write_text(f"downloaded from {url}")

    monkeypatch.setattr(restore.urllib.request, "urlretrieve", fake_urlretrieve)

    _run_restore(["--force"], notebooks_dir)

    # Existing local work is replaced
    assert sst.read_text() == "downloaded from https://ndownloader.figshare.com/files/111"
