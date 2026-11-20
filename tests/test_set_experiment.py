"""Tests for mkfigs.set_experiment (used by the CI workflow to point mkfigs.sh at one run)."""
from __future__ import annotations

import pytest

from mkfigs import pushit
from mkfigs.set_experiment import current_experiment, main, set_experiment, storage_flag

OM3_STYLE = """\
#!/bin/bash
#PBS -l storage=gdata/tm70+gdata/xp65
#PBS -q normal
ESMDIR=/g/data/ol01/outputs/a/datastore.json
ENAME=a
#
  ESMDIR=/g/data/ol01/outputs/b/datastore.json  # built from somewhere
ENAME=b
#ENAME=c
#ESMDIR=/g/data/ol01/outputs/${ENAME}/datastore.json
export PYTHONPATH="x"
array=(
#    #00_template_notebook
    SST
    MLD   #needs a lot of memory
)
printf -v _all '%s:' "${array[@]}"
"""

NEW = "/g/data/zv30/non-cmip/run1/cm3-datastore/cm3-datastore.json"


def _active(text, var):
    return [line.strip() for line in text.splitlines() if line.strip().startswith(f"{var}=")]


def test_replaces_ename_and_esmdir():
    out = set_experiment(OM3_STYLE, "run1", NEW, note="https://example/run/1")
    assert _active(out, "ENAME") == ["ENAME=run1"]
    assert _active(out, "ESMDIR") == [f"ESMDIR={NEW}"]
    assert "#ENAME=a" in out and "#ENAME=c" in out
    assert "# Set by mkfigs.set_experiment: https://example/run/1" in out
    # The new lines come after the last old one, before anything uses them.
    assert out.index("ENAME=run1") < out.index("export PYTHONPATH")
    # The notebook array is unchanged unless asked.
    assert "    MLD   #needs a lot of memory\n" in out


def test_pushit_parses_the_result(tmp_path, monkeypatch):
    (tmp_path / "mkfigs.sh").write_text(set_experiment(OM3_STYLE, "run1", NEW, ["SST"]))
    monkeypatch.setattr(pushit, "HERE", tmp_path)
    assert pushit.parse_mkfigs_sh() == ("run1", NEW, ["SST"])


def test_replaces_notebook_array():
    out = set_experiment(OM3_STYLE, "run1", NEW, ["Transects", "SSH"])
    assert "array=(\n    Transects\n    SSH\n)\n" in out
    assert "MLD" not in out


def test_adds_storage_project_once():
    out = set_experiment(OM3_STYLE, "run1", NEW)
    assert "#PBS -l storage=gdata/tm70+gdata/xp65+gdata/zv30\n" in out
    again = set_experiment(out, "run1", NEW)
    assert again.count("gdata/zv30") == 1


@pytest.mark.parametrize(("path", "flag"), [
    ("/g/data/ol01/outputs/x.json", "gdata/ol01"),
    ("/scratch/tm70/abc123/x.json", "scratch/tm70"),
])
def test_storage_flag(path, flag):
    assert storage_flag(path) == flag


@pytest.mark.parametrize(("ename", "esmdir", "notebooks"), [
    ("run1; rm -rf ~", NEW, None),                   # shell metacharacters
    ("run1", "/home/user/datastore.json", None),     # not /g/data or /scratch
    ("run1", "/g/data/ol01/x/datastore.csv", None),  # not a .json
    ("run1", "/g/data/ol01/../ol02/x.json", None),   # climbs out of the project
    ("run1", "/g/data/ol01/x $(id).json", None),
    ("run1", NEW, ["SST", "MLD;id"]),
    ("", NEW, None),
])
def test_rejects_bad_input(ename, esmdir, notebooks):
    with pytest.raises(ValueError):
        set_experiment(OM3_STYLE, ename, esmdir, notebooks)


def test_main_edits_mkfigs_sh_in_cwd(tmp_path, monkeypatch, capsys):
    (tmp_path / "mkfigs.sh").write_text(OM3_STYLE)
    monkeypatch.chdir(tmp_path)
    main(["--ename", "run1", "--esmdir", NEW, "--notebooks", "SST"])
    assert _active((tmp_path / "mkfigs.sh").read_text(), "ENAME") == ["ENAME=run1"]


def test_main_reports_bad_input_without_editing(tmp_path, monkeypatch):
    (tmp_path / "mkfigs.sh").write_text(OM3_STYLE)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit):
        main(["--ename", "bad name", "--esmdir", NEW])
    assert (tmp_path / "mkfigs.sh").read_text() == OM3_STYLE


def test_current_experiment():
    assert current_experiment(OM3_STYLE) == ("b", "/g/data/ol01/outputs/b/datastore.json")
    assert current_experiment(set_experiment(OM3_STYLE, "run1", NEW)) == ("run1", NEW)
    text = "ENAME=run9\nESMDIR=/g/data/ol01/outputs/${ENAME}/datastore.json\n"
    assert current_experiment(text) == ("run9", "/g/data/ol01/outputs/run9/datastore.json")


def test_print_current(tmp_path, monkeypatch, capsys):
    (tmp_path / "mkfigs.sh").write_text(OM3_STYLE)
    monkeypatch.chdir(tmp_path)
    main(["--print-current"])
    assert capsys.readouterr().out == "b\t/g/data/ol01/outputs/b/datastore.json\n"
    assert (tmp_path / "mkfigs.sh").read_text() == OM3_STYLE


def test_parameters_line_added_and_replaced():
    out = set_experiment(OM3_STYLE, "run1", NEW, parameters=["NYEARS=5", "dpi=150"])
    assert 'export MKFIGS_PARAMETERS="NYEARS=5 dpi=150"' in out
    again = set_experiment(out, "run1", NEW, parameters=["NYEARS=10"])
    active = [line for line in again.splitlines() if line.startswith("export MKFIGS_PARAMETERS=")]
    assert active == ['export MKFIGS_PARAMETERS="NYEARS=10"   # extra papermill parameters']


def test_parameters_kept_unless_given():
    out = set_experiment(OM3_STYLE, "run1", NEW, parameters=["NYEARS=5"])
    kept = set_experiment(out, "run2", NEW)              # None: keep what mkfigs.sh has
    assert 'export MKFIGS_PARAMETERS="NYEARS=5"' in kept
    cleared = set_experiment(out, "run2", NEW, parameters=[])
    assert not [ln for ln in cleared.splitlines() if ln.startswith("export MKFIGS_PARAMETERS=")]


def test_parameters_reach_the_notebooks(tmp_path):
    """What bash exports is what mkfigs-run hands to papermill."""
    import subprocess

    from mkfigs.run import parse_parameters
    sh = tmp_path / "mkfigs.sh"
    sh.write_text(set_experiment(OM3_STYLE, "run1", NEW, parameters=["NYEARS=5", "start_year=1990"])
                  .replace("export PYTHONPATH", 'echo "$MKFIGS_PARAMETERS"; exit 0\nexport PYTHONPATH'))
    out = subprocess.run(["bash", str(sh)], capture_output=True, text=True, check=True).stdout.strip()
    assert parse_parameters(out) == [("NYEARS", "5"), ("start_year", "1990")]


@pytest.mark.parametrize("params", [["NYEARS=5;id"], ["papermill=False"], ["NYEARS"]])
def test_bad_parameters_rejected(params):
    with pytest.raises(ValueError):
        set_experiment(OM3_STYLE, "run1", NEW, parameters=params)
