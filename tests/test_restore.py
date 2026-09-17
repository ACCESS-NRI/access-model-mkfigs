"""Tests for mkfigs.restore, following Figshare support's exact
recommendation for the download/restore path: since publishing an article
just to test a real download is off the table, mock the HTTP layer
(here, urllib.request.urlretrieve, which is what restore.py actually
calls) instead of hitting a real public file.
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
    with patch.object(sys, "argv", ["mkfigs-restore", *args]):
        old_cwd = Path.cwd()
        import os
        os.chdir(cwd)
        try:
            restore.main()
        finally:
            os.chdir(old_cwd)


def test_restore_downloads_each_notebook_url_and_copies_markdown(pushed_experiment, monkeypatch):
    notebooks_dir = pushed_experiment / "notebooks"
    downloaded = []

    def fake_urlretrieve(url, dest):
        downloaded.append((url, str(dest)))
        Path(dest).write_text("{}")  # stand-in rendered notebook content

    monkeypatch.setattr(restore.urllib.request, "urlretrieve", fake_urlretrieve)

    _run_restore([], notebooks_dir)

    urls_fetched = {u for u, _ in downloaded}
    assert urls_fetched == {
        "https://ndownloader.figshare.com/files/111",
        "https://ndownloader.figshare.com/files/222",
    }
    ofol = notebooks_dir / "mkfigs_output_test_experiment_01"
    assert (ofol / "SST_rendered.ipynb").exists()
    assert (ofol / "MLD_rendered.ipynb").exists()
    assert (ofol / "mkmd" / "SST.md").read_text() == "# SST\n"
    assert (ofol / "mkmd" / "MLD.md").read_text() == "# MLD\n"


def test_restore_skips_already_present_files_without_force(pushed_experiment, monkeypatch):
    notebooks_dir = pushed_experiment / "notebooks"
    ofol = notebooks_dir / "mkfigs_output_test_experiment_01"
    ofol.mkdir(parents=True)
    (ofol / "SST_rendered.ipynb").write_text("already-here")

    calls = []
    monkeypatch.setattr(
        restore.urllib.request, "urlretrieve",
        lambda url, dest: calls.append(url) or Path(dest).write_text("{}"),
    )

    _run_restore([], notebooks_dir)

    assert "https://ndownloader.figshare.com/files/111" not in calls  # SST skipped
    assert "https://ndownloader.figshare.com/files/222" in calls      # MLD still fetched
    assert (ofol / "SST_rendered.ipynb").read_text() == "already-here"  # untouched


def test_restore_force_redownloads_even_when_present(pushed_experiment, monkeypatch):
    notebooks_dir = pushed_experiment / "notebooks"
    ofol = notebooks_dir / "mkfigs_output_test_experiment_01"
    ofol.mkdir(parents=True)
    (ofol / "SST_rendered.ipynb").write_text("stale")

    calls = []
    monkeypatch.setattr(
        restore.urllib.request, "urlretrieve",
        lambda url, dest: calls.append(url) or Path(dest).write_text("fresh"),
    )

    _run_restore(["--force"], notebooks_dir)

    assert "https://ndownloader.figshare.com/files/111" in calls
    assert (ofol / "SST_rendered.ipynb").read_text() == "fresh"


def test_restore_exits_if_no_urls_json(tmp_path, monkeypatch):
    repo = tmp_path / "paper-repo"
    notebooks = repo / "notebooks"
    notebooks.mkdir(parents=True)
    (notebooks / "mkfigs.sh").write_text("ENAME=nope\nESMDIR=/fake\n")

    with pytest.raises(SystemExit):
        _run_restore([], notebooks)


def test_restore_never_calls_publish_or_upload(pushed_experiment, monkeypatch):
    """restore.py should have no code path that touches Figshare's write
    API at all -- it only ever downloads. Assert requests.request is never
    called, so a future refactor that accidentally wires in an upload
    call here gets caught immediately.
    """
    import requests
    called = []
    monkeypatch.setattr(requests, "request", lambda *a, **k: called.append(1))
    monkeypatch.setattr(
        restore.urllib.request, "urlretrieve",
        lambda url, dest: Path(dest).write_text("{}"),
    )

    _run_restore([], pushed_experiment / "notebooks")

    assert called == []
