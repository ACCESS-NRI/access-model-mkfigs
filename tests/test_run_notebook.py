"""Tests for mkfigs.run: the papermill/nbconvert orchestration in
run_notebook() and main(), plus _extract_notebook_error(). Complements the
existing test_run.py, which already covers _fix_kernel's concurrency
contract in detail.

papermill/jupyter are never actually invoked here -- subprocess.run is
stubbed so these stay fast and need neither package installed.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from mkfigs import run as run_mod


def _write_notebook(path: Path, kernel_name="conda-env-analysis3-25.07-py"):
    """Write a minimal notebook with the given kernelspec."""
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
    A failed notebook execution should be reported and still converted.
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
