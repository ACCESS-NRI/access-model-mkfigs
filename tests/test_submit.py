"""
mkfigs.submit: one PBS job per notebook plus a summary job, and the
run-only / report-only modes of mkfigs.run that those jobs use.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from mkfigs import run as run_mod

MKFIGS_SH = """#!/bin/bash
#PBS -q normal
ENAME=exp1
ESMDIR=/fake/esm.json
array=(
    SST
#    MLD_max
    51_si_drift_osi455  # needs obs
    MLD
)
printf -v _all '%s:' "${array[@]}"
MKFIGS_NOTEBOOKS="${MKFIGS_NOTEBOOKS:-${_all%:}}"
export MKFIGS_NOTEBOOKS
"""


@pytest.fixture
def submit(tmp_path, monkeypatch):
    """A paper-repo notebooks dir with mkfigs.sh as CWD; returns (mkfigs.submit, notebooks dir)."""
    nbdir = tmp_path / "repo" / "notebooks"
    nbdir.mkdir(parents=True)
    (nbdir / "mkfigs.sh").write_text(MKFIGS_SH)
    monkeypatch.chdir(nbdir)
    # pushit resolves its notebooks dir from the CWD at import time; point it here for this test
    import mkfigs.pushit
    import mkfigs.submit
    monkeypatch.setattr(mkfigs.pushit, "HERE", nbdir)
    return mkfigs.submit, nbdir


def _qsub_calls(submit_mod, monkeypatch, argv):
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return MagicMock(stdout=f"{1000 + len(calls)}.gadi-pbs\n")

    monkeypatch.setattr(submit_mod.subprocess, "run", fake_run)
    submit_mod.main(argv)
    return calls


def test_one_job_per_notebook_then_summary(submit, monkeypatch):
    submit_mod, _ = submit
    calls = _qsub_calls(submit_mod, monkeypatch, [])

    assert len(calls) == 4  # SST, 51_si..., MLD (MLD_max is commented out) + summary
    for cmd, nb in zip(calls, ["SST", "51_si_drift_osi455", "MLD"]):
        assert cmd[0] == "qsub" and cmd[-1] == "mkfigs.sh"
        assert f"MKFIGS_NOTEBOOKS={nb},MKFIGS_SKIP_REPORT=1" in cmd
        assert cmd[cmd.index("-N") + 1][0].isalpha()  # PBS names must start with a letter
    summary = calls[-1]
    assert "depend=afterany:1001.gadi-pbs:1002.gadi-pbs:1003.gadi-pbs" in summary
    assert "MKFIGS_NOTEBOOKS=SST:51_si_drift_osi455:MLD,MKFIGS_REPORT_ONLY=1" in summary


def test_subset_and_unknown_notebooks(submit, monkeypatch):
    submit_mod, _ = submit
    calls = _qsub_calls(submit_mod, monkeypatch, ["MLD"])
    assert len(calls) == 2
    with pytest.raises(SystemExit):
        _qsub_calls(submit_mod, monkeypatch, ["MLD_max"])  # commented out in the array


def test_dry_run_submits_nothing(submit, monkeypatch):
    submit_mod, _ = submit
    monkeypatch.setattr(submit_mod.subprocess, "run", MagicMock(side_effect=AssertionError("qsub called")))
    submit_mod.main(["--dry-run"])


def test_refuses_mkfigs_sh_that_overwrites_the_notebook_list(submit, monkeypatch):
    submit_mod, nbdir = submit
    (nbdir / "mkfigs.sh").write_text(MKFIGS_SH.replace("${MKFIGS_NOTEBOOKS:-${_all%:}}", "${_all%:}"))
    monkeypatch.setattr(submit_mod.subprocess, "run", MagicMock(side_effect=AssertionError("qsub called")))
    with pytest.raises(SystemExit):
        submit_mod.main([])


def test_clears_stale_status_of_resubmitted_notebooks(submit, monkeypatch):
    submit_mod, nbdir = submit
    status = nbdir / "mkfigs_output_exp1" / "mkmd" / "status"
    status.mkdir(parents=True)
    (status / "MLD.txt").write_text("OK\n")
    (status / "SST.txt").write_text("OK\n")
    _qsub_calls(submit_mod, monkeypatch, ["MLD"])
    assert not (status / "MLD.txt").exists()
    assert (status / "SST.txt").exists()


# ---------------------------------------------------------------------------
# mkfigs.run modes used by the submitted jobs
# ---------------------------------------------------------------------------

def _run_main(tmp_path, monkeypatch, notebooks, fake_results, **env):
    wfolder = tmp_path / "paper-repo"
    (wfolder / "notebooks").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MKFIGS_NOTEBOOKS", ":".join(notebooks))
    for k in ("MKFIGS_SKIP_REPORT", "MKFIGS_REPORT_ONLY"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr("sys.argv", ["mkfigs-run", "--ename", "exp1", "--esmdir", "/fake/esm.json",
                                     "--wfolder", str(wfolder)])
    ran = []

    def fake_run_notebook(nb, esmdir, ofol, nbdir):
        ran.append(nb)
        if not fake_results[nb]:
            (ofol / f"{nb}_rendered.ipynb").write_text(json.dumps({"cells": [{"outputs": [
                {"output_type": "error", "ename": "RuntimeError", "evalue": f"{nb} broke", "traceback": []}]}]}))
        return fake_results[nb]

    monkeypatch.setattr(run_mod, "run_notebook", fake_run_notebook)
    run_mod.main()
    return ran, wfolder / "notebooks" / "mkfigs_output_exp1" / "mkmd"


def test_per_notebook_jobs_then_summary(tmp_path, monkeypatch):
    results = {"SST": True, "MLD": False}
    # two notebook jobs: they run and record status, but write no error log
    for nb in ["SST", "MLD"]:
        ran, mdfol = _run_main(tmp_path, monkeypatch, [nb], results, MKFIGS_SKIP_REPORT="1")
        assert ran == [nb]
    assert not (mdfol / "mkfigs_errors.log").exists()
    assert (mdfol / "status" / "SST.txt").read_text() == "OK\n"
    assert (mdfol / "status" / "MLD.txt").read_text() == "FAILED\n"

    # summary job: runs nothing, reports on all three (Transects' job never finished)
    ran, mdfol = _run_main(tmp_path, monkeypatch, ["SST", "MLD", "Transects"], results, MKFIGS_REPORT_ONLY="1")
    assert ran == []
    errors = (mdfol / "mkfigs_errors.log").read_text()
    assert "FAILED: MLD" in errors and "MLD broke" in errors
    assert "FAILED: Transects" in errors and "did not finish" in errors
    assert "FAILED: SST" not in errors
