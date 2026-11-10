"""
Keep the user-facing notebook workflow covered without pinning helper
implementations before the planned refactor: execution, conversion after
failure, and visible errors in mkfigs_errors.log.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from mkfigs import run as run_mod


def _write_notebook(path: Path):
    """Write a minimal notebook with the given kernelspec."""
    kernel_name = "conda-env-analysis3-25.07-py"
    path.write_text(json.dumps({
        "cells": [],
        "metadata": {"kernelspec": {"display_name": kernel_name, "name": kernel_name}},
        "nbformat": 4, "nbformat_minor": 5,
    }))


def test_run_notebook_executes_and_converts_notebook(tmp_path):
    """
    A successful notebook run should execute, convert, and clean up.
    """
    notebooks_dir = tmp_path / "notebooks"
    notebooks_dir.mkdir()

    ofol = notebooks_dir / "mkfigs_output_exp1"
    ofol.mkdir()

    notebook = notebooks_dir / "SST.ipynb"
    _write_notebook(notebook)

    original = notebook.read_bytes()

    with patch.object(run_mod.subprocess, "run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        ok = run_mod.run_notebook("SST", "/fake/esm.json", ofol, notebooks_dir)

        assert ok is True
        assert mock_run.call_count == 2
        assert mock_run.call_args_list[0].args[0][0] == "papermill"
        assert mock_run.call_args_list[1].args[0][:2] == [
            "jupyter", "nbconvert",
        ]

        assert notebook.read_bytes() == original  # original notebook unchanged
        assert list(
            notebooks_dir.glob("*.kernel-fixed.*.ipynb")
        ) == []  # kernel copy cleaned up


def test_run_notebook_reports_failure_and_still_converts(tmp_path):
    """
    Papermill failure must return False, but nbconvert still runs so the
    failed notebook's rendered output remains available for inspection.
    """
    notebooks_dir = tmp_path / "notebooks"
    notebooks_dir.mkdir()
    ofol = notebooks_dir / "mkfigs_output_exp1"
    ofol.mkdir()
    _write_notebook(notebooks_dir / "SST.ipynb")

    with patch.object(run_mod.subprocess, "run") as mock_run:
        mock_run.side_effect = [
            MagicMock(returncode=1),
            MagicMock(returncode=0),
        ]
        ok = run_mod.run_notebook("SST", "/fake/esm.json", ofol, notebooks_dir)

    assert ok is False
    assert mock_run.call_count == 2
    assert mock_run.call_args_list[0].args[0][0] == "papermill"
    assert mock_run.call_args_list[1].args[0][:2] == [
        "jupyter", "nbconvert",
    ]


# ---------------------------------------------------------------------------
# main(): env-driven notebook list, log file, and mkfigs_errors.log on failure
# ---------------------------------------------------------------------------

def test_main_writes_error_log_for_failed_notebooks(tmp_path, monkeypatch):
    """Only the failed notebook should appear in mkfigs_errors.log."""
    wfolder = tmp_path / "paper-repo"
    notebooks_dir = wfolder / "notebooks"
    notebooks_dir.mkdir(parents=True)
    _write_notebook(notebooks_dir / "SST.ipynb")
    _write_notebook(notebooks_dir / "MLD.ipynb")

    monkeypatch.setenv("MKFIGS_NOTEBOOKS", "SST:MLD")
    monkeypatch.setattr(
        "sys.argv",
        ["mkfigs-run", "--ename", "exp1", "--esmdir", "/fake/esm.json", "--wfolder", str(wfolder)],
    )

    def fake_run_notebook(nb, esmdir, ofol, nbdir):
        # simulate SST succeeding (and actually producing a rendered file
        # so _extract_notebook_error has something to look at) and MLD failing
        if nb == "SST":
            return True
        (ofol / f"{nb}_rendered.ipynb").write_text(json.dumps({
            "cells": [{"outputs": [{"output_type": "error", "ename": "RuntimeError",
                                     "evalue": "kaboom", "traceback": []}]}]
        }))
        return False

    monkeypatch.setattr(run_mod, "run_notebook", fake_run_notebook)

    run_mod.main()

    mdfol = notebooks_dir / "mkfigs_output_exp1" / "mkmd"

    errors_log = mdfol / "mkfigs_errors.log"
    assert errors_log.exists()

    content = errors_log.read_text()

    assert "FAILED: MLD" in content
    assert "RuntimeError: kaboom" in content
    assert "FAILED: SST" not in content


# ---------------------------------------------------------------------------
# Execution-time mkfigs version recorded in the rendered notebook (#38)
# ---------------------------------------------------------------------------

def test_stamp_mkfigs_version_adds_key_and_keeps_other_metadata(tmp_path):
    """The stamp adds metadata.mkfigs.version and leaves everything else alone."""
    rendered = tmp_path / "SST_rendered.ipynb"
    rendered.write_text(json.dumps({
        "cells": [{"cell_type": "code", "source": ["1"], "outputs": [],
                   "execution_count": 1, "metadata": {}}],
        "metadata": {
            "kernelspec": {"display_name": "Python 3 (ipykernel)", "name": "python3"},
            "papermill": {"start_time": "2026-11-01T00:00:00"},
            "mkfigs": {"other": "kept"},
        },
        "nbformat": 4, "nbformat_minor": 5,
    }))
    rendered.chmod(0o640)

    assert run_mod.stamp_mkfigs_version(rendered, "v0.2.0-3-gabc1234") is True

    nb = json.loads(rendered.read_text())
    assert nb["metadata"]["mkfigs"] == {"other": "kept", "version": "v0.2.0-3-gabc1234"}
    assert nb["metadata"]["papermill"] == {"start_time": "2026-11-01T00:00:00"}
    assert nb["cells"][0]["source"] == ["1"]
    assert rendered.stat().st_mode & 0o777 == 0o640  # permissions kept
    assert sorted(p.name for p in tmp_path.iterdir()) == ["SST_rendered.ipynb"]  # no temp file left


def test_stamp_mkfigs_version_overwrites_an_older_stamp(tmp_path):
    """Re-running a notebook replaces the previous run's version."""
    rendered = tmp_path / "SST_rendered.ipynb"
    rendered.write_text(json.dumps({"cells": [], "metadata": {"mkfigs": {"version": "old"}}}))

    run_mod.stamp_mkfigs_version(rendered, "new")

    assert json.loads(rendered.read_text())["metadata"]["mkfigs"]["version"] == "new"


def test_stamp_mkfigs_version_never_raises(tmp_path):
    """A missing or corrupt rendered notebook is reported, not raised."""
    assert run_mod.stamp_mkfigs_version(tmp_path / "missing.ipynb", "v1") is False

    corrupt = tmp_path / "corrupt_rendered.ipynb"
    corrupt.write_text("{not json")
    assert run_mod.stamp_mkfigs_version(corrupt, "v1") is False
    assert corrupt.read_text() == "{not json"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["corrupt_rendered.ipynb"]


def test_main_stamps_every_rendered_notebook_with_run_version(tmp_path, monkeypatch):
    """main() stamps the version it ran with into OK and FAILED notebooks alike."""
    wfolder = tmp_path / "paper-repo"
    notebooks_dir = wfolder / "notebooks"
    notebooks_dir.mkdir(parents=True)
    for nb in ("SST", "MLD", "AMOC"):
        _write_notebook(notebooks_dir / f"{nb}.ipynb")

    monkeypatch.setenv("MKFIGS_NOTEBOOKS", "SST:MLD:AMOC")
    monkeypatch.setattr(
        "sys.argv",
        ["mkfigs-run", "--ename", "exp1", "--esmdir", "/fake/esm.json", "--wfolder", str(wfolder)],
    )
    monkeypatch.setattr(run_mod, "get_mkfigs_version", lambda: "v0.2.0-3-gabc1234")

    def fake_run_notebook(nb, esmdir, ofol, nbdir):
        # SST succeeds, MLD fails after papermill wrote its output, and AMOC
        # fails before any rendered notebook exists.
        if nb == "AMOC":
            return False
        _write_notebook(ofol / f"{nb}_rendered.ipynb")
        return nb == "SST"

    monkeypatch.setattr(run_mod, "run_notebook", fake_run_notebook)

    run_mod.main()

    ofol = notebooks_dir / "mkfigs_output_exp1"
    for nb in ("SST", "MLD"):
        meta = json.loads((ofol / f"{nb}_rendered.ipynb").read_text())["metadata"]
        assert meta["mkfigs"]["version"] == "v0.2.0-3-gabc1234"
    assert not (ofol / "AMOC_rendered.ipynb").exists()  # nothing created for it
