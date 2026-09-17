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

import pytest

from mkfigs import run as run_mod


def _write_notebook(path: Path, kernel_name="conda-env-analysis3-25.07-py"):
    path.write_text(json.dumps({
        "cells": [],
        "metadata": {"kernelspec": {"display_name": kernel_name, "name": kernel_name}},
        "nbformat": 4, "nbformat_minor": 5,
    }))


def test_run_notebook_invokes_papermill_with_expected_args_and_cleans_up_kernel_copy(tmp_path):
    notebooks_dir = tmp_path / "notebooks"
    notebooks_dir.mkdir()
    ofol = notebooks_dir / "mkfigs_output_exp1"
    ofol.mkdir()
    _write_notebook(notebooks_dir / "SST.ipynb")

    with patch.object(run_mod.subprocess, "run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        ok = run_mod.run_notebook("SST", "/fake/esm.json", ofol, notebooks_dir)

    assert ok is True
    papermill_call = mock_run.call_args_list[0]
    cmd = papermill_call.args[0]
    assert cmd[0] == "papermill"
    assert "-p" in cmd and "esm_file" in cmd and "/fake/esm.json" in cmd
    assert papermill_call.kwargs["cwd"] == str(notebooks_dir)

    nbconvert_call = mock_run.call_args_list[1]
    assert nbconvert_call.args[0][:3] == ["jupyter", "nbconvert", "--to"]

    # the private, PID-suffixed kernel-fixed copy must not survive the call
    leftovers = list(notebooks_dir.glob("*.kernel-fixed.*.ipynb"))
    assert leftovers == []
    # and the original shared notebook must be untouched
    assert (notebooks_dir / "SST.ipynb").exists()


def test_run_notebook_returns_false_on_papermill_failure_but_still_runs_nbconvert(tmp_path):
    notebooks_dir = tmp_path / "notebooks"
    notebooks_dir.mkdir()
    ofol = notebooks_dir / "mkfigs_output_exp1"
    ofol.mkdir()
    _write_notebook(notebooks_dir / "SST.ipynb")

    with patch.object(run_mod.subprocess, "run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1)  # papermill failed
        ok = run_mod.run_notebook("SST", "/fake/esm.json", ofol, notebooks_dir)

    assert ok is False
    assert mock_run.call_count == 2  # nbconvert still attempted, to surface error output


def test_run_notebook_cleans_up_kernel_copy_even_if_papermill_raises(tmp_path):
    notebooks_dir = tmp_path / "notebooks"
    notebooks_dir.mkdir()
    ofol = notebooks_dir / "mkfigs_output_exp1"
    ofol.mkdir()
    _write_notebook(notebooks_dir / "SST.ipynb")

    with patch.object(run_mod.subprocess, "run", side_effect=OSError("boom")):
        with pytest.raises(OSError):
            run_mod.run_notebook("SST", "/fake/esm.json", ofol, notebooks_dir)

    assert list(notebooks_dir.glob("*.kernel-fixed.*.ipynb")) == []


# ---------------------------------------------------------------------------
# _extract_notebook_error
# ---------------------------------------------------------------------------

def test_extract_notebook_error_strips_ansi_and_finds_last_error(tmp_path):
    nb = {
        "cells": [
            {"outputs": [{"output_type": "error", "ename": "ValueError",
                          "evalue": "first", "traceback": ["\x1b[31mfirst tb\x1b[0m"]}]},
            {"outputs": [{"output_type": "error", "ename": "KeyError",
                          "evalue": "second", "traceback": ["\x1b[31msecond tb\x1b[0m"]}]},
        ]
    }
    p = tmp_path / "rendered.ipynb"
    p.write_text(json.dumps(nb))

    msg = run_mod._extract_notebook_error(p)

    assert msg.startswith("KeyError: second")  # last error cell wins
    assert "\x1b[" not in msg  # ANSI codes stripped


def test_extract_notebook_error_returns_none_when_no_error_or_missing_file(tmp_path):
    ok_nb = {"cells": [{"outputs": [{"output_type": "stream", "text": "hi"}]}]}
    p = tmp_path / "rendered.ipynb"
    p.write_text(json.dumps(ok_nb))
    assert run_mod._extract_notebook_error(p) is None
    assert run_mod._extract_notebook_error(tmp_path / "missing.ipynb") is None


# ---------------------------------------------------------------------------
# main(): env-driven notebook list, log file, and mkfigs_errors.log on failure
# ---------------------------------------------------------------------------

def test_main_writes_error_log_for_failed_notebooks(tmp_path, monkeypatch):
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
    assert (mdfol / "mkfigs_run.log").exists()
    errors_log = (mdfol / "mkfigs_errors.log").read_text()
    assert "FAILED: MLD" in errors_log
    assert "RuntimeError: kaboom" in errors_log
    assert "SST" not in errors_log.split("FAILED: MLD")[0]  # SST's success not logged as an error


def test_main_exits_if_mkfigs_notebooks_env_var_missing(tmp_path, monkeypatch):
    wfolder = tmp_path / "paper-repo"
    (wfolder / "notebooks").mkdir(parents=True)
    monkeypatch.delenv("MKFIGS_NOTEBOOKS", raising=False)
    monkeypatch.setattr(
        "sys.argv",
        ["mkfigs-run", "--ename", "exp1", "--esmdir", "/fake/esm.json", "--wfolder", str(wfolder)],
    )
    with pytest.raises(SystemExit):
        run_mod.main()
