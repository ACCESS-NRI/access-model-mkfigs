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
# Extra papermill parameters (MKFIGS_PARAMETERS, e.g. NYEARS=5)
# ---------------------------------------------------------------------------
import pytest  # noqa: E402


def _papermill_args(tmp_path, monkeypatch, parameters):
    notebooks_dir = tmp_path / "notebooks"
    notebooks_dir.mkdir()
    ofol = notebooks_dir / "mkfigs_output_exp1"
    ofol.mkdir()
    _write_notebook(notebooks_dir / "SST.ipynb")
    if parameters is not None:
        monkeypatch.setenv("MKFIGS_PARAMETERS", parameters)
    with patch.object(run_mod.subprocess, "run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        run_mod.run_notebook("SST", "/fake/esm.json", ofol, notebooks_dir)
    return mock_run.call_args_list[0].args[0]


def test_extra_parameters_are_passed_to_papermill(tmp_path, monkeypatch):
    args = _papermill_args(tmp_path, monkeypatch, "NYEARS=5 start_year=1990-01-01")
    assert args[-6:] == ["-p", "NYEARS", "5", "-p", "start_year", "1990-01-01"]
    assert args[args.index("esm_file") + 1] == "/fake/esm.json"   # mkfigs' own still there


def test_no_extra_parameters_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("MKFIGS_PARAMETERS", raising=False)
    args = _papermill_args(tmp_path, monkeypatch, None)
    assert args[-2:] == ["nbname", "SST.ipynb"]


@pytest.mark.parametrize("text", [
    "NYEARS",                 # no value
    "NYEARS=",                # empty value
    "1YEARS=5",               # bad name
    "NYEARS=5;id",            # shell metacharacter
    "esm_file=/g/data/x.json",  # set by mkfigs
    "NYEARS=5 NYEARS=6",      # twice
])
def test_bad_parameters_are_rejected(text):
    with pytest.raises(ValueError):
        run_mod.parse_parameters(text)


def test_parse_parameters():
    assert run_mod.parse_parameters("") == []
    assert run_mod.parse_parameters("  NYEARS=5\tdpi=150 ") == [("NYEARS", "5"), ("dpi", "150")]
