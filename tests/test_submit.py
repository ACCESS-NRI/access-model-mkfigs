"""
mkfigs.submit: one PBS job per notebook plus a summary job, and the
run-only / report-only modes of mkfigs.run that those jobs use.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from mkfigs import run as run_mod
from mkfigs._paths import SUBMISSION_LOCK

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
    nbdir = tmp_path / "paper-repo" / "notebooks"
    nbdir.mkdir(parents=True)
    (nbdir / "mkfigs.sh").write_text(MKFIGS_SH)
    monkeypatch.chdir(nbdir)
    monkeypatch.setenv("PROJECT", "ab12")
    # pushit resolves its notebooks dir from the CWD at import time; point it here for this test
    import mkfigs.pushit
    import mkfigs.submit
    monkeypatch.setattr(mkfigs.pushit, "HERE", nbdir)
    return mkfigs.submit, nbdir


def _qsub_calls(submit_mod, monkeypatch, argv):
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return MagicMock(returncode=0, stdout=f"{1000 + len(calls)}.gadi-pbs\n", stderr="")

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
    variables = summary[summary.index("-v") + 1].split(",")
    assert variables == [
        "MKFIGS_NOTEBOOKS=SST:51_si_drift_osi455:MLD",
        "MKFIGS_REPORT_ONLY=1",
        "MKFIGS_RELEASE_SUBMISSION_LOCK=1",
    ]


def test_project_passed_explicitly_unless_mkfigs_sh_sets_one(submit, monkeypatch):
    # Gadi's qsub ignores $PROJECT once -v is given, so it has to be passed as -P
    submit_mod, nbdir = submit
    calls = _qsub_calls(submit_mod, monkeypatch, ["SST"])
    assert all(cmd[1:3] == ["-P", "ab12"] for cmd in calls)
    script = MKFIGS_SH.replace("#PBS -q normal", "#PBS -q normal\n#PBS -P cd34")
    # A different experiment can submit while the first summary is pending.
    (nbdir / "mkfigs.sh").write_text(script.replace("ENAME=exp1", "ENAME=exp2"))
    calls = _qsub_calls(submit_mod, monkeypatch, ["SST"])
    assert all("-P" not in cmd for cmd in calls)


def test_qsub_failure_stops_with_its_message(submit, monkeypatch):
    submit_mod, nbdir = submit
    monkeypatch.setattr(submit_mod.subprocess, "run",
                        lambda cmd, **kw: MagicMock(returncode=32, stdout="", stderr="qsub: Error: no project"))
    with pytest.raises(SystemExit, match="no project"):
        submit_mod.main(["SST"])

    assert not (nbdir / "mkfigs_output_exp1" / SUBMISSION_LOCK).exists()


def test_failed_summary_submission_keeps_lock(submit, monkeypatch):
    submit_mod, nbdir = submit
    fake_run = MagicMock(side_effect=[
        MagicMock(returncode=0, stdout="1001.gadi-pbs\n"),
        MagicMock(returncode=1, stderr="summary rejected"),
    ])
    monkeypatch.setattr(submit_mod.subprocess, "run", fake_run)

    with pytest.raises(SystemExit, match="summary rejected"):
        submit_mod.main(["SST"])
    lock = nbdir / "mkfigs_output_exp1" / SUBMISSION_LOCK
    assert lock.is_dir()
    assert list(lock.iterdir()) == []
    with pytest.raises(SystemExit, match="output for exp1 is locked"):
        submit_mod.main(["SST"])
    assert fake_run.call_count == 2


def test_subset_and_unknown_notebooks(submit, monkeypatch):
    submit_mod, _ = submit
    calls = _qsub_calls(submit_mod, monkeypatch, ["MLD"])
    assert len(calls) == 2
    with pytest.raises(SystemExit):
        _qsub_calls(submit_mod, monkeypatch, ["MLD_max"])  # commented out in the array


def test_dry_run_submits_nothing(submit, monkeypatch):
    submit_mod, nbdir = submit
    monkeypatch.setattr(submit_mod.subprocess, "run", MagicMock(side_effect=AssertionError("qsub called")))
    submit_mod.main(["--dry-run"])
    assert not (nbdir / "mkfigs_output_exp1").exists()


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


def test_resubmitting_before_first_summary_preserves_first_result(submit, tmp_path, monkeypatch, caplog):
    submit_mod, nbdir = submit
    ofol = nbdir / "mkfigs_output_exp1"
    lock = ofol / SUBMISSION_LOCK
    results = {"SST": True}

    def run_job(cmd):
        pbs_env = dict(item.split("=", 1) for item in cmd[cmd.index("-v") + 1].split(","))
        notebooks = pbs_env.pop("MKFIGS_NOTEBOOKS").split(":")
        return _run_main(tmp_path, monkeypatch, notebooks, results, **pbs_env)

    notebook, summary = _qsub_calls(submit_mod, monkeypatch, ["SST"])
    # Run the notebook with the environment PBS would pass, leaving its summary queued.
    ran, mdfol = run_job(notebook)
    assert ran == ["SST"]
    status = mdfol / "status" / "SST.txt"
    rendered = ofol / "SST_rendered.ipynb"
    first_output = rendered.read_bytes()

    with monkeypatch.context() as patch:
        pbs = MagicMock(side_effect=AssertionError("overlapping submission reached PBS"))
        patch.setattr(submit_mod.subprocess, "run", pbs)
        with pytest.raises(SystemExit, match="output for exp1 is locked") as exc:
            submit_mod.main(["SST"])
        assert f"rmdir {lock}" in str(exc.value)
        pbs.assert_not_called()
    assert lock.is_dir()
    assert status.read_text() == "OK\n"
    assert rendered.read_bytes() == first_output

    with caplog.at_level("INFO", logger=run_mod.__name__):
        ran, _ = run_job(summary)
    assert ran == []
    assert "Succeeded (1): SST" in caplog.messages
    assert not (mdfol / "mkfigs_errors.log").exists()
    assert not lock.exists()
    assert len(_qsub_calls(submit_mod, monkeypatch, ["SST"])) == 2
    assert not status.exists()


# ---------------------------------------------------------------------------
# mkfigs.run modes used by the submitted jobs
# ---------------------------------------------------------------------------

def _run_main(tmp_path, monkeypatch, notebooks, fake_results, **env):
    wfolder = tmp_path / "paper-repo"
    (wfolder / "notebooks").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MKFIGS_NOTEBOOKS", ":".join(notebooks))
    for k in ("MKFIGS_SKIP_REPORT", "MKFIGS_REPORT_ONLY", "MKFIGS_RELEASE_SUBMISSION_LOCK"):
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
        else:
            (ofol / f"{nb}_rendered.ipynb").write_text(json.dumps({"cells": []}))
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
