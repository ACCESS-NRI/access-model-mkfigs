"""Notebooks run from another repo (a git submodule), e.g. access-om3-paper-1
inside access-cm3-paper-1 (see mkfigs/_sources.py).

mkfigs.sh lists them as a `<name>_array` of notebook names plus a `<name>_dir`
(path from the repo root), and passes them on as <dir>/<notebook>. These tests
cover: parsing, running from the notebook's own directory, the recorded
source, the as-run copy that pushit writes (never into the submodule), the
landing-page Source column, and that a repo with no such notebooks (OM3) is
unchanged.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mkfigs import _sources, pushit, submit
from mkfigs import run as run_mod

from .conftest import make_png, make_rendered_notebook

ENAME = "test_experiment_01"
OM3_DIR = "external/access-om3-paper-1/notebooks"
OM3_URL = "https://github.com/ACCESS-Community-Hub/access-om3-paper-1"

MKFIGS_SH_WITH_OM3 = (
    "#!/bin/bash\n"
    f"ENAME={ENAME}\n"
    "ESMDIR=/g/data/tm70/fake/datastore.json\n"
    "array=(\n"
    "  TOA_CRE\n"
    ")\n"
    f"om3_dir={OM3_DIR}\n"
    "om3_array=(\n"
    "  SST    # inline comment\n"
    "  # MLD\n"
    ")\n"
    "printf -v _all '%s:' \"${array[@]}\" \"${om3_array[@]/#/${om3_dir}/}\"\n"
    "MKFIGS_NOTEBOOKS=\"${MKFIGS_NOTEBOOKS:-${_all%:}}\"\n"
)


# ---------------------------------------------------------------------------
# _sources helpers
# ---------------------------------------------------------------------------

def test_stem_and_path():
    assert _sources.notebook_stem(f"{OM3_DIR}/SST") == "SST"
    assert _sources.notebook_stem("SST") == "SST"
    assert _sources.is_external(f"{OM3_DIR}/SST") and not _sources.is_external("SST")
    nbdir, root = Path("/r/notebooks/polished-python"), Path("/r")
    assert _sources.notebook_path("TOA_CRE", nbdir, root) == nbdir / "TOA_CRE.ipynb"
    assert _sources.notebook_path(f"{OM3_DIR}/SST", nbdir, root) == root / OM3_DIR / "SST.ipynb"


def test_duplicate_stems_are_refused():
    _sources.check_unique_stems(["SST", f"{OM3_DIR}/MLD"])
    with pytest.raises(ValueError, match="Transects"):
        _sources.check_unique_stems(["Transects", f"{OM3_DIR}/Transects"])


def test_https_url():
    assert _sources._https_url("git@github.com:ACCESS-Community-Hub/access-om3-paper-1.git") == OM3_URL
    assert _sources._https_url(OM3_URL + ".git") == OM3_URL


def test_describe_source_of_a_real_git_repo(tmp_path):
    repo = tmp_path / "om3"
    (repo / "notebooks").mkdir(parents=True)
    nb = repo / "notebooks" / "SST.ipynb"
    nb.write_text("{}")
    git = ["git", "-C", str(repo), "-c", "user.name=x", "-c", "user.email=x@x", "-c", "commit.gpgsign=false"]
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run([*git, "remote", "add", "origin", "git@github.com:ACCESS-Community-Hub/access-om3-paper-1.git"], check=True)
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "x"], check=True)
    info = _sources.describe_source(nb)
    assert info["repo"] == "access-om3-paper-1"
    assert info["url"] == OM3_URL
    assert len(info["commit"]) == 40
    assert info["path"] == "notebooks/SST.ipynb"
    assert info["authors"] == ["x"]
    assert info["submodule"] == ""  # not inside a superproject here
    assert info["cff_authors"] == []  # no CITATION.cff
    link = _sources.source_link_md(info)
    assert link == f"`access-om3-paper-1`: [`@{info['commit'][:7]}`]({OM3_URL}/blob/{info['commit']}/notebooks/SST.ipynb)"


def test_cff_author_names(tmp_path):
    cff = tmp_path / "CITATION.cff"
    cff.write_text("authors:\n  - given-names: Andrew E.\n    family-names: Kiss\n"
                   "  - given-names: Chris\n    family-names: Bull\n")
    assert _sources.cff_author_names(cff) == ["Kiss, Andrew E.", "Bull, Chris"]
    assert _sources.authors_md(_sources.cff_author_names(cff)) == "**authors (alphabetically):** Bull, Chris; Kiss, Andrew E."
    assert _sources.cff_author_names(tmp_path / "missing.cff") == []


def test_notebook_authors_creator_first_then_by_commits(tmp_path):
    repo = tmp_path / "om3"
    (repo / "notebooks").mkdir(parents=True)
    nb = repo / "notebooks" / "SST.ipynb"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    for i, who in enumerate(["Creator", "Bea", "Al", "Al"]):
        nb.write_text(str(i))
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(repo), "-c", f"user.name={who}", "-c", "user.email=x@x",
                        "-c", "commit.gpgsign=false", "commit", "-q", "-m", str(i)], check=True)
    assert _sources.notebook_authors(repo, "notebooks/SST.ipynb") == ["Creator", "Al", "Bea"]


CONFIGDOC_HEADER = (
    "# SST\n \nEvaluation figures from ACCESS-CM3 experiment **e** produced by notebook `SST.ipynb`."
    " Co-authors for this notebook (via Git history): unknown. [View rendered notebook](notebooks/SST.ipynb)\n"
)


def test_rewrite_page_header_names_the_source_repo_and_its_authors(tmp_path):
    md = tmp_path / "SST.md"
    md.write_text(CONFIGDOC_HEADER + "\n## Fig\n")
    info = {"repo": "access-om3-paper-1", "url": OM3_URL, "commit": "c" * 40, "path": "notebooks/SST.ipynb",
            "submodule": "external/access-om3-paper-1", "authors": ["A. Kiss", "C. Bull"]}
    assert _sources.rewrite_page_header(md, info, "https://x/as-run.ipynb") is True
    text = md.read_text()
    assert f"from the [`access-om3-paper-1`]({OM3_URL}) repository of metrics" in text
    assert "`external/access-om3-paper-1`" in text
    assert "[as run](https://x/as-run.ipynb)" in text
    assert "(via Git history of `access-om3-paper-1`): A. Kiss, C. Bull. [View rendered notebook]" in text
    assert "unknown" not in text and text.endswith("## Fig\n")
    assert _sources.rewrite_page_header(md, info) is False  # idempotent


# ---------------------------------------------------------------------------
# mkfigs.sh parsing (pushit / submit)
# ---------------------------------------------------------------------------

def test_parse_mkfigs_sh_reads_submodule_array(tmp_path, monkeypatch):
    (tmp_path / "mkfigs.sh").write_text(MKFIGS_SH_WITH_OM3)
    monkeypatch.setattr(pushit, "HERE", tmp_path)
    ename, _, notebooks = pushit.parse_mkfigs_sh()
    assert ename == ENAME
    assert notebooks == ["TOA_CRE", f"{OM3_DIR}/SST"]


def test_parse_mkfigs_sh_ignores_brackets_in_comments(tmp_path, monkeypatch):
    """A ')' in a comment inside an array must not end it early (bash ignores it)."""
    (tmp_path / "mkfigs.sh").write_text(
        "ENAME=e\nESMDIR=/x\n"
        "# array=(Old)   a commented-out line (with brackets)\n"
        "array=(\n    A\n    # B   # not on CM3 (no output)\n    C   # keep (yes)\n)\n"
        "om3_dir=external/om3/notebooks   # (path)\n"
        "om3_array=(\n    # D   # not expected to work (no ocean.stats)\n    SST\n    SSH\n)\n"
    )
    monkeypatch.setattr(pushit, "HERE", tmp_path)
    _, _, notebooks = pushit.parse_mkfigs_sh()
    assert notebooks == ["A", "C", "external/om3/notebooks/SST", "external/om3/notebooks/SSH"]


def test_parse_mkfigs_sh_needs_the_dir(tmp_path, monkeypatch):
    (tmp_path / "mkfigs.sh").write_text("ENAME=e\nESMDIR=/x\narray=(\n A\n)\nom3_array=(\n SST\n)\n")
    monkeypatch.setattr(pushit, "HERE", tmp_path)
    with pytest.raises(SystemExit, match="om3_dir"):
        pushit.parse_mkfigs_sh()


def test_mkfigs_sh_bash_expansion_matches_the_parser(tmp_path):
    """What bash puts in MKFIGS_NOTEBOOKS is what pushit/submit read."""
    sh = tmp_path / "mkfigs.sh"
    sh.write_text(MKFIGS_SH_WITH_OM3 + 'echo "$MKFIGS_NOTEBOOKS"\n')
    out = subprocess.run(["bash", str(sh)], capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"})
    assert out.stdout.strip() == f"TOA_CRE:{OM3_DIR}/SST"


def test_submit_uses_stems_for_job_names_and_full_path_for_the_job(tmp_path, monkeypatch, capsys):
    (tmp_path / "mkfigs.sh").write_text(MKFIGS_SH_WITH_OM3)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(pushit, "HERE", tmp_path)
    monkeypatch.setenv("PROJECT", "tm70")
    submit.main(["--dry-run", "SST"])
    out = capsys.readouterr().out
    assert "-N mk_SST" in out
    assert f"MKFIGS_NOTEBOOKS={OM3_DIR}/SST,MKFIGS_SKIP_REPORT=1" in out
    assert "TOA_CRE" not in out


# ---------------------------------------------------------------------------
# mkfigs.run
# ---------------------------------------------------------------------------

def _notebook(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "cells": [], "metadata": {"kernelspec": {"display_name": "x", "name": "x"}},
        "nbformat": 4, "nbformat_minor": 5,
    }))


def test_run_notebook_runs_a_submodule_notebook_from_its_own_dir(tmp_path):
    repo = tmp_path / "cm3"
    nbdir = repo / "notebooks" / "polished-python"
    ofol = nbdir / "mkfigs_output_e"
    ofol.mkdir(parents=True)
    om3_nb = repo / OM3_DIR / "SST.ipynb"
    _notebook(om3_nb)
    original = om3_nb.read_bytes()
    with patch.object(run_mod.subprocess, "run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        assert run_mod.run_notebook(f"{OM3_DIR}/SST", "/esm.json", ofol, nbdir, repo) is True
    papermill = mock_run.call_args_list[0]
    assert papermill.kwargs["cwd"] == str(om3_nb.parent)
    argv = papermill.args[0]
    assert argv[2] == str(ofol / "SST_rendered.ipynb")
    assert argv[argv.index("nbname") + 1] == "SST.ipynb"
    assert om3_nb.read_bytes() == original
    assert list((repo / OM3_DIR).glob("*.kernel-fixed.*")) == []


def test_run_main_records_sources_and_uses_stems(tmp_path, monkeypatch):
    repo = tmp_path / "cm3"
    nbdir = repo / "notebooks" / "polished-python"
    nbdir.mkdir(parents=True)
    calls = []
    monkeypatch.setattr(run_mod, "run_notebook",
                        lambda nb, esm, ofol, d, repo_root=None: calls.append((nb, repo_root)) or True)
    monkeypatch.setattr(_sources, "describe_source",
                        lambda p: {"repo": "access-om3-paper-1", "url": OM3_URL, "commit": "a" * 40,
                                   "path": "notebooks/SST.ipynb"})
    monkeypatch.setenv("MKFIGS_NOTEBOOKS", f"TOA_CRE:{OM3_DIR}/SST")
    monkeypatch.setattr(sys, "argv", ["mkfigs-run", "--ename", "e", "--esmdir", "/esm.json",
                                      "--wfolder", str(repo), "--notebooks-subdir", "polished-python"])
    run_mod.main()
    assert calls == [("TOA_CRE", repo), (f"{OM3_DIR}/SST", repo)]
    mdfol = nbdir / "mkfigs_output_e" / "mkmd"
    assert sorted(p.name for p in (mdfol / "status").iterdir()) == ["SST.txt", "TOA_CRE.txt"]
    assert _sources.read_sources(mdfol) == {"SST": {"repo": "access-om3-paper-1", "url": OM3_URL,
                                                    "commit": "a" * 40, "path": "notebooks/SST.ipynb"}}


def test_run_main_refuses_duplicate_stems(tmp_path, monkeypatch):
    repo = tmp_path / "cm3"
    (repo / "notebooks").mkdir(parents=True)
    monkeypatch.setenv("MKFIGS_NOTEBOOKS", f"Transects:{OM3_DIR}/Transects")
    monkeypatch.setattr(sys, "argv", ["mkfigs-run", "--ename", "e", "--esmdir", "/x", "--wfolder", str(repo)])
    monkeypatch.setattr(run_mod, "run_notebook", lambda *a, **k: pytest.fail("must not run"))
    with pytest.raises(SystemExit):
        run_mod.main()


# ---------------------------------------------------------------------------
# mkfigs.pushit end to end
# ---------------------------------------------------------------------------

def _seed(repo: Path, notebooks: list[str], om3_sources: dict[str, str]):
    ofol = repo / "notebooks" / f"mkfigs_output_{ENAME}"
    mdfol = ofol / "mkmd"
    mdfol.mkdir(parents=True)
    for nb in notebooks:
        make_rendered_notebook(ofol / f"{nb}_rendered.ipynb")
        make_png(mdfol / f"{nb}_01.png")
        (mdfol / f"{nb}.md").write_text(CONFIGDOC_HEADER.replace("SST", nb)
                                        + f"\n![fig](/assets/experiments/{ENAME}/{nb}_01.png)\n")
    (mdfol / "sources").mkdir()
    for nb, commit in om3_sources.items():
        (mdfol / "sources" / f"{nb}.json").write_text(json.dumps(
            {"repo": "access-om3-paper-1", "url": OM3_URL, "commit": commit, "path": f"notebooks/{nb}.ipynb",
             "submodule": "external/access-om3-paper-1", "authors": ["Om Three"],
             "cff_authors": ["Three, Om", "Kiss, Andrew E."]}))


def _pushit(args):
    with patch.object(sys, "argv", ["mkfigs-pushit", *args]), patch.object(pushit.subprocess, "run"):
        pushit.main()


@pytest.fixture
def cm3_repo(patch_repo_paths, monkeypatch):
    repo = patch_repo_paths
    monkeypatch.setenv("MKFIGS_MODEL_NAME", "ACCESS-CM3")
    monkeypatch.setenv("MKFIGS_REPO_URL", "https://github.com/ACCESS-Community-Hub/access-cm3-paper-1")
    (repo / "notebooks" / "mkfigs.sh").write_text(MKFIGS_SH_WITH_OM3)
    om3_nb = repo / OM3_DIR / "SST.ipynb"
    om3_nb.parent.mkdir(parents=True)
    om3_nb.write_text("submodule copy: must not change")
    return repo


def test_pushit_keeps_submodule_untouched_and_writes_as_run_copy(cm3_repo, fake_figshare):
    repo = cm3_repo
    _seed(repo, ["TOA_CRE", "SST"], {"SST": "b" * 40})
    _pushit(["--skip-figshare"])

    assert (repo / OM3_DIR / "SST.ipynb").read_text() == "submodule copy: must not change"
    assert (repo / "notebooks" / "access-om3-paper-1" / "SST.ipynb").exists()  # as-run copy
    assert (repo / "notebooks" / "TOA_CRE.ipynb").exists()  # own notebook: as before
    assert not (repo / "notebooks" / "SST.ipynb").exists()

    exp = repo / "documentation" / "docs" / "pages" / "experiments" / ENAME
    urls = json.loads((exp / "notebooks_urls.json").read_text())
    assert urls["_sources"]["SST"]["commit"] == "b" * 40
    assert "TOA_CRE" not in urls["_sources"]

    index = (repo / "documentation" / "docs" / "pages" / "index.md").read_text()
    assert ("| Notebook | Notebook run success? | Source (repo: @commit&nbsp;·&nbsp;as&nbsp;run)"
            " | GitHub Issue(s) | Links |") in index
    intro = index.split("<!-- experiments -->")[0]
    assert "[access-cm3-paper-1](https://github.com/ACCESS-Community-Hub/access-cm3-paper-1/) and the" in intro
    assert f"[access-om3-paper-1]({OM3_URL}/)" in intro
    assert ("`access-om3-paper-1` is a git submodule of `access-cm3-paper-1` (`external/access-om3-paper-1`)"
            in intro)
    assert (f"[`eval-{ENAME}`](https://github.com/ACCESS-Community-Hub/access-cm3-paper-1/tree/eval-{ENAME}"
            "/notebooks/access-om3-paper-1)") in intro
    blob = f"https://github.com/ACCESS-Community-Hub/access-cm3-paper-1/blob/eval-{ENAME}/notebooks"
    sst_row = next(ln for ln in index.splitlines() if ln.startswith("| `SST`"))
    assert f"({OM3_URL}/blob/{'b' * 40}/notebooks/SST.ipynb)" in sst_row
    assert f"[as&nbsp;run]({blob}/access-om3-paper-1/SST.ipynb)" in sst_row
    toa_row = next(ln for ln in index.splitlines() if ln.startswith("| `TOA_CRE`"))
    assert f"`access-cm3-paper-1`: [as&nbsp;run]({blob}/TOA_CRE.ipynb)" in toa_row
    assert "**Source:**" in index
    assert ("`ACCESS-Community-Hub/access-om3-paper-1/` **authors (alphabetically):** Kiss, Andrew E.; Three, Om.\n"
            in intro)

    sst_page = (exp / "SST.md").read_text()
    assert "repository of metrics" in sst_page and "(via Git history of `access-om3-paper-1`): Om Three." in sst_page
    assert f"[as run]({blob}/access-om3-paper-1/SST.ipynb)" in sst_page
    assert "Co-authors for this notebook (via Git history): unknown." in (exp / "TOA_CRE.md").read_text()


def test_pushit_not_run_submodule_notebook_still_shows_its_repo(cm3_repo, fake_figshare, monkeypatch):
    repo = cm3_repo
    _seed(repo, ["TOA_CRE"], {})  # SST listed in om3_array but not run
    monkeypatch.setattr(pushit, "describe_source", lambda p: {
        "repo": "access-om3-paper-1", "url": OM3_URL, "commit": "d" * 40, "path": "notebooks/SST.ipynb",
        "submodule": "external/access-om3-paper-1", "authors": []})
    _pushit(["--skip-figshare"])
    index = (repo / "documentation" / "docs" / "pages" / "index.md").read_text()
    sst_row = next(ln for ln in index.splitlines() if ln.startswith("| `SST`"))
    assert f"`access-om3-paper-1`: [`@ddddddd`]({OM3_URL}/blob/{'d' * 40}/notebooks/SST.ipynb)" in sst_row
    assert "as&nbsp;run](" not in sst_row
    assert "git submodule of `access-cm3-paper-1`" in index
    exp = repo / "documentation" / "docs" / "pages" / "experiments" / ENAME
    assert "_sources" not in json.loads((exp / "notebooks_urls.json").read_text())


def test_pushit_check_upload_git_adds_the_as_run_copy(cm3_repo, fake_figshare, monkeypatch, capsys):
    repo = cm3_repo
    _seed(repo, ["TOA_CRE", "SST"], {"SST": "b" * 40})
    _pushit(["--skip-figshare"])
    capsys.readouterr()
    monkeypatch.setattr(pushit, "_check_figshare_urls", lambda urls: True)
    monkeypatch.setattr(pushit, "_git_lines", lambda *a: [])
    exp = repo / "documentation" / "docs" / "pages" / "experiments" / ENAME
    urls = json.loads((exp / "notebooks_urls.json").read_text())
    urls.update({"SST": "https://ndownloader.figshare.com/files/1", "TOA_CRE": "https://ndownloader.figshare.com/files/2"})
    (exp / "notebooks_urls.json").write_text(json.dumps(urls))
    pushit.check_figshare_upload_mode(ENAME)
    out = capsys.readouterr().out
    add = next(ln for ln in out.splitlines() if ln.strip().startswith("git add"))
    assert "notebooks/access-om3-paper-1/SST.ipynb" in add
    assert "notebooks/TOA_CRE.ipynb" in add
    assert OM3_DIR not in add


def test_pushit_without_submodule_notebooks_has_no_source_column(patch_repo_paths, fake_figshare):
    """access-om3-paper-1 itself (no *_array): landing page exactly as before."""
    repo = patch_repo_paths
    _seed(repo, ["SST", "MLD"], {})
    _pushit(["--skip-figshare"])
    index = (repo / "documentation" / "docs" / "pages" / "index.md").read_text()
    assert "| Notebook | Notebook run success? | GitHub Issue(s) | Links |" in index
    assert "Source" not in index
    exp = repo / "documentation" / "docs" / "pages" / "experiments" / ENAME
    assert "_sources" not in json.loads((exp / "notebooks_urls.json").read_text())
    assert (repo / "notebooks" / "SST.ipynb").exists()
