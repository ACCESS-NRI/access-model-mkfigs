"""Tests for the three changes that let a second paper repo
(access-cm3-paper-1: notebooks one directory deeper, in
notebooks/polished-python/, and not ACCESS-OM3) use this package:

  - mkfigs.run --notebooks-subdir
  - mkfigs.pushit repo-root / notebooks-dir detection at any depth
  - FigshareUploader model identity (MKFIGS_MODEL_NAME / MKFIGS_REPO_URL)

Each also pins the unchanged default (access-om3-paper-1 layout / identity).
"""
from __future__ import annotations

from pathlib import Path

from mkfigs import pushit, run as run_mod
from mkfigs.configdoc import FigshareUploader

from .conftest import FIGSHARE_BASE  # noqa: F401  (fake_figshare fixture needs it importable)


# ---------------------------------------------------------------------------
# mkfigs.run --notebooks-subdir
# ---------------------------------------------------------------------------

def _drive_run_main(monkeypatch, wfolder: Path, extra_args: list[str]) -> list[Path]:
    """Run mkfigs.run.main() with run_notebook stubbed; return the
    notebooks_dir each notebook was looked for in."""
    monkeypatch.setenv("MKFIGS_NOTEBOOKS", "SST")
    monkeypatch.setattr(
        "sys.argv",
        ["mkfigs-run", "--ename", "exp1", "--esmdir", "/fake/esm.json",
         "--wfolder", str(wfolder), *extra_args],
    )
    seen: list[Path] = []

    def fake_run_notebook(nb, esmdir, ofol, nbdir):
        seen.append(nbdir)
        return True

    monkeypatch.setattr(run_mod, "run_notebook", fake_run_notebook)
    try:
        run_mod.main()
    except SystemExit:
        pass
    return seen


def test_run_uses_notebooks_subdir_when_given(tmp_path, monkeypatch):
    wfolder = tmp_path / "cm3-repo"
    (wfolder / "notebooks" / "polished-python").mkdir(parents=True)

    seen = _drive_run_main(monkeypatch, wfolder, ["--notebooks-subdir", "polished-python"])

    assert seen == [wfolder / "notebooks" / "polished-python"]


def test_run_default_is_still_notebooks_directly(tmp_path, monkeypatch):
    """No --notebooks-subdir: exactly the access-om3-paper-1 layout."""
    wfolder = tmp_path / "om3-repo"
    (wfolder / "notebooks").mkdir(parents=True)

    seen = _drive_run_main(monkeypatch, wfolder, [])

    assert seen == [wfolder / "notebooks"]


# ---------------------------------------------------------------------------
# pushit: find mkfigs.sh and the repo root at either depth
# ---------------------------------------------------------------------------

def _make_repo(root: Path, nb_levels: list[str]) -> Path:
    nbdir = root.joinpath(*nb_levels)
    nbdir.mkdir(parents=True)
    (nbdir / "mkfigs.sh").write_text("ENAME=x\n")
    (root / "documentation").mkdir()
    (root / "documentation" / "mkdocs.yml").write_text("site_name: x\n")
    return nbdir


def test_pushit_finds_repo_root_for_two_level_notebooks(tmp_path, monkeypatch):
    nbdir = _make_repo(tmp_path / "cm3", ["notebooks", "polished-python"])
    monkeypatch.chdir(nbdir)

    here = pushit._find_notebooks_dir()

    assert here == nbdir.resolve()
    assert pushit._find_repo_root(here) == (tmp_path / "cm3")


def test_pushit_finds_repo_root_for_one_level_notebooks(tmp_path, monkeypatch):
    """access-om3-paper-1 layout: unchanged behaviour."""
    nbdir = _make_repo(tmp_path / "om3", ["notebooks"])
    monkeypatch.chdir(nbdir)

    here = pushit._find_notebooks_dir()

    assert here == nbdir.resolve()
    assert pushit._find_repo_root(here) == (tmp_path / "om3")


def test_pushit_repo_root_falls_back_to_parent_without_markers(tmp_path):
    nbdir = tmp_path / "loose" / "notebooks"
    nbdir.mkdir(parents=True)
    assert pushit._find_repo_root(nbdir) == nbdir.parent


# ---------------------------------------------------------------------------
# FigshareUploader model identity
# ---------------------------------------------------------------------------

def _uploader(tmp_path: Path, **kw) -> FigshareUploader:
    mdfol = tmp_path / "mkmd"
    mdfol.mkdir(exist_ok=True)
    return FigshareUploader(token="tok", experiment="exp1", mdfol=str(mdfol), **kw)


def test_model_identity_defaults_to_om3(tmp_path, monkeypatch):
    monkeypatch.delenv("MKFIGS_MODEL_NAME", raising=False)
    monkeypatch.delenv("MKFIGS_REPO_URL", raising=False)

    up = _uploader(tmp_path)

    assert up.model_name == "ACCESS-OM3"
    assert up.article_title == "ACCESS-OM3 evaluation figures – exp1"
    assert up.repo_url.endswith("access-om3-paper-1")


def test_model_identity_from_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("MKFIGS_MODEL_NAME", "ACCESS-CM3")
    monkeypatch.setenv("MKFIGS_REPO_URL", "https://github.com/ACCESS-Community-Hub/access-cm3-paper-1")

    up = _uploader(tmp_path)

    assert up.article_title == "ACCESS-CM3 evaluation figures – exp1"
    assert up.repo_url.endswith("access-cm3-paper-1")


def test_explicit_model_name_beats_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("MKFIGS_MODEL_NAME", "ACCESS-CM3")
    assert _uploader(tmp_path, model_name="ACCESS-AM3").model_name == "ACCESS-AM3"


def test_created_article_is_titled_with_the_model(fake_figshare, tmp_path, monkeypatch):
    """End to end against the fake server: the article that actually gets
    created carries the CM3 name, not a hard-coded ACCESS-OM3."""
    monkeypatch.setenv("MKFIGS_MODEL_NAME", "ACCESS-CM3")
    up = _uploader(tmp_path)

    article_id = up._get_or_create_article()

    assert fake_figshare._articles[article_id]["title"] == "ACCESS-CM3 evaluation figures – exp1"


# ---------------------------------------------------------------------------
# Experiment name in figure paths = ENAME, not the datastore's parent directory
# ---------------------------------------------------------------------------
# MkmdWriter writes /assets/experiments/<experiment>/<png> into the .md files and
# mkfigs-pushit rewrites exactly that prefix (with <experiment> = ENAME) to Figshare
# URLs / local paths. If the two differ, links silently stay dead.

CM3_ESM = "/g/data/x/ACCESS-CM3/cm3-run-1/cm3-datastore/cm3-datastore.json"
OM3_ESM = "/g/data/x/access-om3-25km/MC_25km_run/datastore.json"


def test_mkmd_experiment_falls_back_to_datastore_parent_dir(monkeypatch):
    """access-om3-paper-1 layout (<ENAME>/datastore.json): unchanged."""
    from mkfigs.configdoc import MkmdWriter
    monkeypatch.delenv("MKFIGS_ENAME", raising=False)
    assert MkmdWriter(OM3_ESM, "SST.ipynb", "/tmp/x/").experiment == "MC_25km_run"


def test_mkmd_experiment_uses_ename_when_exported(monkeypatch):
    """CM3 layout: the datastore's parent dir is 'cm3-datastore' for every run."""
    from mkfigs.configdoc import MkmdWriter
    monkeypatch.delenv("MKFIGS_ENAME", raising=False)
    assert MkmdWriter(CM3_ESM, "SST.ipynb", "/tmp/x/").experiment == "cm3-datastore"  # the trap
    monkeypatch.setenv("MKFIGS_ENAME", "cm3-run-1")
    assert MkmdWriter(CM3_ESM, "SST.ipynb", "/tmp/x/").experiment == "cm3-run-1"


def test_run_exports_ename_to_the_notebooks(tmp_path, monkeypatch):
    monkeypatch.delenv("MKFIGS_ENAME", raising=False)
    wfolder = tmp_path / "repo"
    (wfolder / "notebooks").mkdir(parents=True)
    seen = []
    monkeypatch.setenv("MKFIGS_NOTEBOOKS", "SST")
    monkeypatch.setattr("sys.argv", ["mkfigs-run", "--ename", "my-exp", "--esmdir", CM3_ESM,
                                     "--wfolder", str(wfolder)])
    import os
    monkeypatch.setattr(run_mod, "run_notebook",
                        lambda nb, esm, ofol, nbdir: seen.append(os.environ.get("MKFIGS_ENAME")) or True)
    try:
        run_mod.main()
    except SystemExit:
        pass
    assert seen == ["my-exp"]
# restore: same mkfigs.sh / repo-root detection as pushit
# ---------------------------------------------------------------------------

def test_restore_finds_docs_tree_for_two_level_notebooks(tmp_path, monkeypatch):
    """CM3 layout: restore must read the docs tree at the repo root, not notebooks/."""
    import json
    from unittest.mock import patch

    from mkfigs import restore

    repo = tmp_path / "cm3"
    nbdir = _make_repo(repo, ["notebooks", "polished-python"])
    (nbdir / "mkfigs.sh").write_text("ENAME=exp1\nESMDIR=/fake\n")
    exp_docs = repo / "documentation" / "docs" / "pages" / "experiments" / "exp1"
    exp_docs.mkdir(parents=True)
    (exp_docs / "notebooks_urls.json").write_text(json.dumps({"SST": "https://example/1"}))
    (exp_docs / "SST.md").write_text("# SST\n")

    monkeypatch.chdir(nbdir)
    monkeypatch.setattr(restore, "_check_nci_environment", lambda: None)
    monkeypatch.setattr("sys.argv", ["mkfigs-restore"])
    with patch("urllib.request.urlretrieve", lambda url, dest: Path(dest).write_text("{}")):
        restore.main()

    out = nbdir / "mkfigs_output_exp1"
    assert (out / "SST_rendered.ipynb").exists()
    assert (out / "mkmd" / "SST.md").read_text() == "# SST\n"


# ---------------------------------------------------------------------------
# run: end-of-job instructions point at the right directory
# ---------------------------------------------------------------------------

def test_run_next_steps_cd_into_notebooks_subdir(tmp_path, monkeypatch, capsys):
    wfolder = tmp_path / "cm3-repo"
    (wfolder / "notebooks" / "polished-python").mkdir(parents=True)
    (wfolder / "external" / "access-model-mkfigs" / "src").mkdir(parents=True)

    _drive_run_main(monkeypatch, wfolder, ["--notebooks-subdir", "polished-python"])

    out = capsys.readouterr().out
    assert f"cd {wfolder / 'notebooks' / 'polished-python'}" in out
    assert f"{wfolder / 'external' / 'access-model-mkfigs' / 'src'}" in out
    assert "python3 -m mkfigs.pushit --check-figshare-upload" in out
    assert "venv" not in out


# ---------------------------------------------------------------------------
# pushit: model identity from mkfigs.sh (pushit runs outside the batch job)
# ---------------------------------------------------------------------------

def test_pushit_reads_model_identity_from_mkfigs_sh(tmp_path, monkeypatch):
    monkeypatch.delenv("MKFIGS_MODEL_NAME", raising=False)
    monkeypatch.delenv("MKFIGS_REPO_URL", raising=False)
    (tmp_path / "mkfigs.sh").write_text(
        '# export MKFIGS_MODEL_NAME="ignored"\n'
        'export MKFIGS_MODEL_NAME="ACCESS-CM3"\n'
        "export MKFIGS_REPO_URL=https://github.com/ACCESS-Community-Hub/access-cm3-paper-1\n"
    )
    monkeypatch.setattr(pushit, "HERE", tmp_path)

    pushit.load_model_identity_from_mkfigs_sh()

    up = _uploader(tmp_path)
    assert up.model_name == "ACCESS-CM3"
    assert up.repo_url.endswith("access-cm3-paper-1")


def test_pushit_model_identity_env_beats_mkfigs_sh(tmp_path, monkeypatch):
    monkeypatch.setenv("MKFIGS_MODEL_NAME", "FROM-ENV")
    (tmp_path / "mkfigs.sh").write_text('export MKFIGS_MODEL_NAME="ACCESS-CM3"\n')
    monkeypatch.setattr(pushit, "HERE", tmp_path)

    pushit.load_model_identity_from_mkfigs_sh()

    assert _uploader(tmp_path).model_name == "FROM-ENV"


def test_pushit_model_identity_unset_for_om3_mkfigs_sh(tmp_path, monkeypatch):
    """OM3's mkfigs.sh has no export lines: default identity unchanged."""
    monkeypatch.delenv("MKFIGS_MODEL_NAME", raising=False)
    (tmp_path / "mkfigs.sh").write_text("ENAME=x\nESMDIR=/y\n")
    monkeypatch.setattr(pushit, "HERE", tmp_path)

    pushit.load_model_identity_from_mkfigs_sh()

    assert _uploader(tmp_path).model_name == "ACCESS-OM3"
