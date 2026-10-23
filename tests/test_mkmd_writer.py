"""
Re-running a notebook into an existing output folder should replace its
markdown and PNGs, not append duplicate sections.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from mkfigs import configdoc


class _FakeFigure:
    def savefig(self, path, **kwargs):
        Path(path).write_bytes(b"not-a-real-png")


@pytest.fixture(autouse=True)
def _no_git_authors(monkeypatch):
    monkeypatch.setattr(configdoc, "get_notebook_authors", lambda *a, **k: [])


def _run(cwd, nfigs, nbname="MLD.ipynb"):
    """Simulate one papermill run of a notebook that saves nfigs figures."""
    writer = configdoc.MkmdWriter("/fake/exp1/datastore.json", nbname, cwd, pm=True)
    for i in range(1, nfigs + 1):
        writer.savefig(_FakeFigure(), f"Figure {i}", f"caption {i}")
    return writer


def _outputs(tmp_path):
    cwd = str(tmp_path / "mkfigs_output_exp1") + "/"
    return cwd, Path(cwd) / "mkmd"


def test_rerun_replaces_markdown_and_pngs(tmp_path):
    cwd, mdfol = _outputs(tmp_path)
    _run(cwd, 3)
    _run(cwd, 2)  # second, shorter run

    md = (mdfol / "MLD.md").read_text()
    assert md.count("# MLD\n") == 1
    assert md.count("## Figure") == 2
    assert sorted(p.name for p in mdfol.glob("MLD_*.png")) == ["MLD_01.png", "MLD_02.png"]


def test_rerun_leaves_other_notebooks_alone(tmp_path):
    cwd, mdfol = _outputs(tmp_path)
    _run(cwd, 2, nbname="MLD_max.ipynb")
    _run(cwd, 1)
    _run(cwd, 1)

    assert (mdfol / "MLD_max.md").read_text().count("## Figure") == 2
    assert sorted(p.name for p in mdfol.glob("MLD_max_*.png")) == ["MLD_max_01.png", "MLD_max_02.png"]


def test_run_without_output_keeps_previous_results(tmp_path):
    cwd, mdfol = _outputs(tmp_path)
    _run(cwd, 2)
    _run(cwd, 0)  # e.g. a re-run that fails before saving any figure

    assert (mdfol / "MLD.md").read_text().count("## Figure") == 2
    assert sorted(p.name for p in mdfol.glob("MLD_*.png")) == ["MLD_01.png", "MLD_02.png"]
