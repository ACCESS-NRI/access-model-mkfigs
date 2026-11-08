"""The run summary reports the mkfigs version that *executed* each notebook.

https://github.com/ACCESS-NRI/access-model-mkfigs/issues/38: pushit used to
stamp its own (publishing-time) version onto every newly published notebook,
which is wrong whenever the submodule changed between mkfigs-run and pushit.
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from mkfigs import pushit
from mkfigs.pushit import (
    UNKNOWN_VERSION,
    build_run_summary,
    notebook_run_versions,
    report_run_versions,
)
from mkfigs.run import stamp_mkfigs_version

from .conftest import make_png, make_rendered_notebook

ENAME = "test_experiment_01"
RUN = datetime(2026, 11, 6, 2, 20, tzinfo=timezone.utc)
RUN_VERSION = "v0.2.0-3-gabc1234"       # version mkfigs-run executed with
PUSHIT_VERSION = "v0.2.0-9-gdef5678"    # newer submodule checked out at pushit time
LOG_FMT = "%(asctime)s %(levelname)-8s %(message)s"  # mkfigs.run.setup_logging


def _log_lines(*messages: tuple[int, str]) -> str:
    """Format messages exactly as mkfigs.run's log handler writes them."""
    fmt = logging.Formatter(LOG_FMT)
    return "".join(
        fmt.format(logging.LogRecord("mkfigs.run", level, __file__, 0, msg, None, None)) + "\n"
        for level, msg in messages
    )


def _rendered(ofol: Path, nb: str, version: str | None = None) -> Path:
    path = ofol / f"{nb}_rendered.ipynb"
    make_rendered_notebook(path)
    if version is not None:
        assert stamp_mkfigs_version(path, version)
    return path


# ---------------------------------------------------------------------------
# notebook_run_versions(): where the per-notebook version comes from
# ---------------------------------------------------------------------------

def test_version_read_from_rendered_notebook_metadata(tmp_path):
    _rendered(tmp_path, "SST", RUN_VERSION)
    _rendered(tmp_path, "MLD", "git+1234567-dirty")

    assert notebook_run_versions(tmp_path, tmp_path / "mkmd", ["SST", "MLD"]) == {
        "SST": RUN_VERSION,
        "MLD": "git+1234567-dirty",
    }


def test_unstamped_notebook_falls_back_to_last_run_log_entry(tmp_path):
    """Outputs from a mkfigs-run older than the stamp: use mkfigs_run.log.

    The log is appended to by every job, so the job that last STARTed a
    notebook decides its version, including when that run FAILED.
    """
    mdfol = tmp_path / "mkmd"
    mdfol.mkdir()
    (mdfol / "mkfigs_run.log").write_text(_log_lines(
        (logging.INFO, "Experiment            : exp"),
        (logging.INFO, "access-model-mkfigs   : git+1111111"),
        (logging.INFO, "START  SST"),
        (logging.INFO, "OK     SST"),
        (logging.INFO, "START  MLD"),
        (logging.ERROR, "FAILED MLD"),
        # second job, after a submodule update, re-runs MLD only
        (logging.INFO, "access-model-mkfigs   : v0.2.0-3-gabc1234-dirty"),
        (logging.INFO, "START  MLD"),
        (logging.ERROR, "FAILED MLD"),
        (logging.ERROR, "FAILED    (1): MLD"),
    ))
    for nb in ("SST", "MLD", "AMOC"):
        _rendered(tmp_path, nb)

    assert notebook_run_versions(tmp_path, mdfol, ["SST", "MLD", "AMOC"]) == {
        "SST": "git+1111111",
        "MLD": "v0.2.0-3-gabc1234-dirty",
        "AMOC": UNKNOWN_VERSION,  # never in the log
    }


def test_notebook_metadata_wins_over_run_log(tmp_path):
    mdfol = tmp_path / "mkmd"
    mdfol.mkdir()
    (mdfol / "mkfigs_run.log").write_text(_log_lines(
        (logging.INFO, "access-model-mkfigs   : git+1111111"),
        (logging.INFO, "START  SST"),
    ))
    _rendered(tmp_path, "SST", RUN_VERSION)

    assert notebook_run_versions(tmp_path, mdfol, ["SST"]) == {"SST": RUN_VERSION}


def test_missing_or_unreadable_sources_give_unknown_not_pushit_version(tmp_path):
    """Never substitute pushit's own version: that is the bug in #38."""
    (tmp_path / "BAD_rendered.ipynb").write_text("{not json")
    _rendered(tmp_path, "EMPTY", "")  # empty stamp is not a version

    with patch.object(pushit, "get_mkfigs_version", return_value=PUSHIT_VERSION):
        versions = notebook_run_versions(tmp_path, tmp_path / "no-mkmd", ["BAD", "EMPTY", "GONE"])

    assert versions == dict.fromkeys(["BAD", "EMPTY", "GONE"], UNKNOWN_VERSION)


# ---------------------------------------------------------------------------
# build_run_summary(): run-history table
# ---------------------------------------------------------------------------

def _history_rows(md: str) -> list[str]:
    table = md.split("| Date | Notebooks run | access-model-mkfigs |", 1)[1]
    return [line for line in table.splitlines() if line.startswith("| 20")]


def test_run_history_uses_per_notebook_run_versions():
    _, md = build_run_summary(
        ENAME, "/esm.json", ["SST", "MLD"], [], [], ["AMOC"], RUN,
        mkfigs_version=PUSHIT_VERSION,
        run_versions={"SST": RUN_VERSION, "MLD": "git+1234567"},
        existing_run_times={"AMOC": "2026-10-01 00:00 UTC"},
        existing_run_versions={"AMOC": "git+7654321"},
    )

    rows = _history_rows(md)
    assert len(rows) == 3  # same time, two run versions, plus the earlier run
    assert any("`SST`" in r and "commit/abc1234" in r and "MLD" not in r for r in rows)
    assert any("`MLD`" in r and "commit/1234567" in r and "SST" not in r for r in rows)
    assert any("`AMOC`" in r and "commit/7654321" in r for r in rows)
    assert "def5678" not in md  # pushit's own version appears nowhere


def test_notebooks_sharing_a_run_version_share_a_row():
    _, md = build_run_summary(
        ENAME, "/esm.json", ["SST", "MLD"], [], [], [], RUN,
        run_versions={"SST": RUN_VERSION, "MLD": RUN_VERSION},
    )

    (row,) = _history_rows(md)
    assert "`MLD`, `SST`" in row and "commit/abc1234" in row


def test_mkfigs_version_still_used_when_run_versions_omitted():
    """Existing callers that pass only mkfigs_version keep working."""
    _, md = build_run_summary(ENAME, "/esm.json", ["SST"], [], [], [], RUN,
                              mkfigs_version=RUN_VERSION)

    (row,) = _history_rows(md)
    assert "commit/abc1234" in row


# ---------------------------------------------------------------------------
# report_run_versions(): login-node feedback
# ---------------------------------------------------------------------------

def test_report_flags_mismatch_with_pushit_version(capsys):
    report_run_versions({"SST": RUN_VERSION, "MLD": RUN_VERSION}, PUSHIT_VERSION)

    out = capsys.readouterr().out
    assert f"{RUN_VERSION}" in out and "MLD, SST" in out
    assert f"pushit itself is running from {PUSHIT_VERSION}" in out
    assert "WARNING" not in out


def test_report_is_quiet_when_versions_match(capsys):
    report_run_versions({"SST": RUN_VERSION}, RUN_VERSION)

    out = capsys.readouterr().out
    assert "NOTE" not in out and "WARNING" not in out


def test_report_warns_about_unknown_versions(capsys):
    report_run_versions({"SST": UNKNOWN_VERSION}, PUSHIT_VERSION)

    out = capsys.readouterr().out
    assert "WARNING: no version recorded" in out
    assert "NOTE" not in out  # 'unknown' is not a mismatch to report


# ---------------------------------------------------------------------------
# pushit main(): end to end, submodule updated between run and pushit
# ---------------------------------------------------------------------------

def _seed_run(repo: Path, stamps: dict[str, str | None]) -> tuple[Path, Path]:
    ofol = repo / "notebooks" / f"mkfigs_output_{ENAME}"
    mdfol = ofol / "mkmd"
    mdfol.mkdir(parents=True)
    for nb, version in stamps.items():
        _rendered(ofol, nb, version)
        make_png(mdfol / f"{nb}_01.png")
        (mdfol / f"{nb}.md").write_text(f"# {nb}\n\n![fig](/assets/experiments/{ENAME}/{nb}_01.png)\n")
    return ofol, mdfol


def _run_pushit(args: list[str]) -> None:
    with patch.object(sys, "argv", ["mkfigs-pushit", *args]), \
         patch.object(pushit.subprocess, "run"), \
         patch.object(pushit, "get_mkfigs_version", return_value=PUSHIT_VERSION):
        pushit.main()


def test_pushit_records_run_versions_not_its_own(patch_repo_paths, fake_figshare, figshare_token, capsys):
    repo = patch_repo_paths
    _, mdfol = _seed_run(repo, {"SST": RUN_VERSION, "MLD": None})
    # MLD was rendered before the stamp existed; its job's log still says what ran it.
    (mdfol / "mkfigs_run.log").write_text(_log_lines(
        (logging.INFO, "access-model-mkfigs   : git+1234567"),
        (logging.INFO, "START  MLD"),
    ))

    _run_pushit(["--skip-figshare"])

    exp_docs = repo / "documentation" / "docs" / "pages" / "experiments" / ENAME
    urls = json.loads((exp_docs / "notebooks_urls.json").read_text())
    assert urls["_run_versions"] == {"SST": RUN_VERSION, "MLD": "git+1234567"}

    index_md = (repo / "documentation" / "docs" / "pages" / "index.md").read_text()
    assert "commit/abc1234" in index_md and "commit/1234567" in index_md
    assert "def5678" not in index_md

    out = capsys.readouterr().out
    assert f"pushit itself is running from {PUSHIT_VERSION}" in out


def test_pushit_keeps_earlier_versions_and_replaces_rerun_ones(
    patch_repo_paths, fake_figshare, figshare_token,
):
    """A later pushit run keeps carried-forward versions and updates re-run ones."""
    repo = patch_repo_paths
    exp_docs = repo / "documentation" / "docs" / "pages" / "experiments" / ENAME
    exp_docs.mkdir(parents=True)
    (exp_docs / "notebooks_urls.json").write_text(json.dumps({
        "SST": "https://ndownloader.figshare.com/files/1",
        "AMOC": "https://ndownloader.figshare.com/files/2",
        "_run_times": {"SST": "2026-10-01 00:00 UTC", "AMOC": "2026-10-01 00:00 UTC"},
        "_run_versions": {"SST": "git+1111111", "AMOC": "git+1111111"},
    }))
    _seed_run(repo, {"SST": RUN_VERSION})

    _run_pushit(["--skip-figshare"])

    urls = json.loads((exp_docs / "notebooks_urls.json").read_text())
    assert urls["_run_versions"] == {"SST": RUN_VERSION, "AMOC": "git+1111111"}


def test_dry_run_reports_run_versions_and_writes_nothing(patch_repo_paths, fake_figshare, capsys):
    repo = patch_repo_paths
    _seed_run(repo, {"SST": RUN_VERSION})

    _run_pushit(["--dry-run"])

    out = capsys.readouterr().out
    assert "version that ran the notebooks to publish" in out and RUN_VERSION in out
    assert not (repo / "documentation" / "docs" / "pages" / "experiments" / ENAME).exists()
