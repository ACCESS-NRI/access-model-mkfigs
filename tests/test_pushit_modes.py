"""Integration tests for mkfigs.pushit.main() and its two check modes.

These exercise the full pushit.py flow (notebook classification -> Figshare
upload -> docs-tree copy -> mkdocs.yml nav update -> pages/index.md update)
against fake_paper_repo + fake_figshare, with pushit's own subprocess calls
(jupyter nbconvert) stubbed out so the test needs neither jupyter nor a
network connection installed to run.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from mkfigs import pushit

from .conftest import make_png, make_rendered_notebook


def _seed_notebook_outputs(repo: Path, ename: str, notebooks: list[str]):
    """Populate mkfigs_output_<ename>/ as if mkfigs-run had already
    executed successfully for each notebook in *notebooks*.
    """
    ofol = repo / "notebooks" / f"mkfigs_output_{ename}"
    mdfol = ofol / "mkmd"
    mdfol.mkdir(parents=True)
    for nb in notebooks:
        make_rendered_notebook(ofol / f"{nb}_rendered.ipynb")
        make_png(mdfol / f"{nb}_01.png")
        (mdfol / f"{nb}.md").write_text(
            f"# {nb}\n\n![fig](/assets/experiments/{ename}/{nb}_01.png)\n"
        )
    return ofol, mdfol


def _run_pushit(args, no_subprocess=True):
    with patch.object(sys, "argv", ["mkfigs-pushit", *args]):
        if no_subprocess:
            with patch.object(pushit.subprocess, "run") as mock_run:
                pushit.main()
                return mock_run
        pushit.main()


# ---------------------------------------------------------------------------
# --dry-run: must not touch the filesystem or Figshare at all
# ---------------------------------------------------------------------------

def test_dry_run_writes_nothing_and_never_calls_figshare(patch_repo_paths, fake_figshare):
    repo = patch_repo_paths
    _seed_notebook_outputs(repo, "test_experiment_01", ["SST", "MLD"])

    exp_docs = repo / "documentation" / "docs" / "pages" / "experiments" / "test_experiment_01"
    mkdocs_before = (repo / "documentation" / "mkdocs.yml").read_text()
    index_before = (repo / "documentation" / "docs" / "pages" / "index.md").read_text()

    _run_pushit(["--dry-run"])

    assert not exp_docs.exists()  # nothing copied into the docs tree
    assert (repo / "documentation" / "mkdocs.yml").read_text() == mkdocs_before
    assert (repo / "documentation" / "docs" / "pages" / "index.md").read_text() == index_before
    assert fake_figshare.deleted_file_ids == []
    for aid in fake_figshare._articles:  # nothing created either
        pass
    assert fake_figshare._articles == {}


# ---------------------------------------------------------------------------
# --skip-figshare: docs tree updates, but Figshare is never contacted
# ---------------------------------------------------------------------------

def test_skip_figshare_copies_docs_but_uploads_nothing(patch_repo_paths, fake_figshare, figshare_token):
    repo = patch_repo_paths
    _seed_notebook_outputs(repo, "test_experiment_01", ["SST"])

    _run_pushit(["--skip-figshare"])

    exp_docs = repo / "documentation" / "docs" / "pages" / "experiments" / "test_experiment_01"
    assert (exp_docs / "SST.md").exists()
    urls = json.loads((exp_docs / "notebooks_urls.json").read_text())
    assert {k: v for k, v in urls.items() if not k.startswith("_")} == {}
    assert fake_figshare._articles == {}  # no article was ever created


# ---------------------------------------------------------------------------
# Full run: upload + docs tree + nav + index.md, end to end against the fake
# ---------------------------------------------------------------------------

def test_full_run_uploads_and_builds_docs_tree(patch_repo_paths, fake_figshare, figshare_token):
    repo = patch_repo_paths
    ename = "test_experiment_01"
    _seed_notebook_outputs(repo, ename, ["SST", "MLD"])

    mock_run = _run_pushit([])

    exp_docs = repo / "documentation" / "docs" / "pages" / "experiments" / ename

    # Per-notebook markdown copied into the docs tree
    assert (exp_docs / "SST.md").exists()
    assert (exp_docs / "MLD.md").exists()

    # notebooks_urls.json has a real figshare URL for each notebook
    urls = json.loads((exp_docs / "notebooks_urls.json").read_text())
    assert urls["SST"].startswith("https://ndownloader.figshare.com/files/")
    assert urls["MLD"].startswith("https://ndownloader.figshare.com/files/")

    # image URL was rewritten in the copied markdown (not left as a local path)
    assert "/assets/experiments/" not in (exp_docs / "SST.md").read_text()

    # mkdocs.yml nav grew a Summary + Notebook entry per notebook
    mkdocs_yml = (repo / "documentation" / "mkdocs.yml").read_text()
    assert "SST" in mkdocs_yml and "MLD" in mkdocs_yml

    # pages/index.md has an experiment block for this run
    index_md = (repo / "documentation" / "docs" / "pages" / "index.md").read_text()
    assert f"<!-- experiment:{ename} -->" in index_md
    assert "SST" in index_md and "MLD" in index_md

    # rendered notebooks copied back into notebooks/, and nbconvert was
    # invoked to strip outputs from that copy (stubbed, so just check the call)
    assert (repo / "notebooks" / "SST.ipynb").exists()
    nbconvert_calls = [c for c in mock_run.call_args_list if "nbconvert" in c.args[0]]
    assert len(nbconvert_calls) == 2  # once per successful notebook


def test_partial_run_records_failed_notebook_alongside_a_success(
    patch_repo_paths, fake_figshare, figshare_token
):
    """SST renders but produces neither PNGs nor markdown (FAILED, per
    pushit's own "rendered.ipynb existing doesn't mean success" rule);
    MLD succeeds fully. FAILED only shows up in index.md at all when the
    same experiment also has at least one OK/PREV-COMMITTED notebook --
    see test_all_failed_run_writes_no_index_update for the other half of
    that boundary.
    """
    repo = patch_repo_paths
    ename = "test_experiment_01"
    ofol = repo / "notebooks" / f"mkfigs_output_{ename}"
    mdfol = ofol / "mkmd"
    mdfol.mkdir(parents=True)
    make_rendered_notebook(ofol / "SST_rendered.ipynb")  # no PNGs, no .md -> FAILED
    make_rendered_notebook(ofol / "MLD_rendered.ipynb")
    make_png(mdfol / "MLD_01.png")
    (mdfol / "MLD.md").write_text(f"# MLD\n\n![fig](/assets/experiments/{ename}/MLD_01.png)\n")

    _run_pushit([])

    exp_docs = repo / "documentation" / "docs" / "pages" / "experiments" / ename
    index_md = (repo / "documentation" / "docs" / "pages" / "index.md").read_text()
    assert "❌ FAILED" in index_md
    assert not (exp_docs / "SST.md").exists()
    assert (exp_docs / "MLD.md").exists()


def test_all_failed_run_writes_no_index_update(patch_repo_paths, fake_figshare, figshare_token):
    """Characterization test for a real control-flow gap: main() returns
    right after printing the run summary, BEFORE update_top_index() is
    ever called, whenever there are no OK/PREV-COMMITTED notebooks --
    see the `if not ok_nbs and not prev_committed_nbs: ... return` guard
    in pushit.main(). Concretely: an experiment where every notebook
    fails (or the run never happened) leaves NO trace at all in
    pages/index.md -- not even a "this failed" marker -- because that
    guard fires before the FAILED/NOT RUN block would otherwise be
    written. Worth a product decision (silence vs. visible failure); this
    test just pins down what happens today so a future change here is a
    deliberate choice, not an accidental regression.
    """
    repo = patch_repo_paths
    index_before = (repo / "documentation" / "docs" / "pages" / "index.md").read_text()

    _run_pushit([])  # mkfigs.sh lists SST + MLD; neither has any output at all

    index_after = (repo / "documentation" / "docs" / "pages" / "index.md").read_text()
    assert index_after == index_before
    assert fake_figshare._articles == {}


def test_mkdocs_jupyter_plugin_addition_is_not_actually_persisted(
    patch_repo_paths, fake_figshare, figshare_token
):
    """Characterization test for a real bug: _ensure_mkdocs_jupyter_plugin
    mutates data["plugins"] in memory and update_mkdocs_nav prints
    "[mkdocs] Added mkdocs-jupyter plugin config", but _save_mkdocs_yml
    only ever serialises and splices back the nav: block -- the plugins
    change is silently discarded. On a fresh repo (or one where someone
    removed the plugin) mkfigs-pushit's own console output claims the fix
    happened when it didn't, and notebook pages will 404/fail to render
    until someone adds mkdocs-jupyter to mkdocs.yml by hand. Recommend
    either having _save_mkdocs_yml also splice in a plugins: block, or
    dropping the misleading print until it does.
    """
    repo = patch_repo_paths
    ename = "test_experiment_01"
    _seed_notebook_outputs(repo, ename, ["SST"])

    _run_pushit([])

    mkdocs_yml = (repo / "documentation" / "mkdocs.yml").read_text()
    assert "mkdocs-jupyter" not in mkdocs_yml  # documents the gap; flip once fixed


# ---------------------------------------------------------------------------
# --check-figshare-integrity: private-state verification BEFORE publishing
# ---------------------------------------------------------------------------

def test_check_figshare_integrity_passes_when_everything_matches(
    patch_repo_paths, fake_figshare, figshare_token, capsys
):
    repo = patch_repo_paths
    ename = "test_experiment_01"
    ofol, mdfol = _seed_notebook_outputs(repo, ename, ["SST", "MLD"])

    uploader = pushit.FigshareUploader(figshare_token, ename, str(mdfol))
    article_id = uploader._get_or_create_article()
    for nb in ["SST", "MLD"]:
        fake_figshare.seed_file(article_id, f"{nb}_rendered.ipynb",
                                 (ofol / f"{nb}_rendered.ipynb").read_bytes())
        fake_figshare.seed_file(article_id, f"{nb}_01.png",
                                 (mdfol / f"{nb}_01.png").read_bytes())

    _run_pushit(["--check-figshare-integrity"])  # must not raise/exit

    out = capsys.readouterr().out
    assert "safe to publish" in out.lower()


def test_check_figshare_integrity_exits_nonzero_on_missing_file(
    patch_repo_paths, fake_figshare, figshare_token
):
    repo = patch_repo_paths
    ename = "test_experiment_01"
    ofol, mdfol = _seed_notebook_outputs(repo, ename, ["SST"])

    uploader = pushit.FigshareUploader(figshare_token, ename, str(mdfol))
    article_id = uploader._get_or_create_article()
    # Only the notebook was uploaded -- the PNG is "missing" on Figshare.
    fake_figshare.seed_file(article_id, "SST_rendered.ipynb",
                             (ofol / "SST_rendered.ipynb").read_bytes())

    with pytest.raises(SystemExit) as exc:
        _run_pushit(["--check-figshare-integrity"])
    assert exc.value.code != 0


def test_check_figshare_integrity_flags_but_does_not_autodelete_duplicates_without_flag(
    patch_repo_paths, fake_figshare, figshare_token, capsys
):
    repo = patch_repo_paths
    ename = "test_experiment_01"
    ofol, mdfol = _seed_notebook_outputs(repo, ename, ["SST"])

    uploader = pushit.FigshareUploader(figshare_token, ename, str(mdfol))
    article_id = uploader._get_or_create_article()
    nb_bytes = (ofol / "SST_rendered.ipynb").read_bytes()
    fake_figshare.seed_file(article_id, "SST_rendered.ipynb", nb_bytes)
    fake_figshare.seed_file(article_id, "SST_01.png", (mdfol / "SST_01.png").read_bytes())
    stub_id = fake_figshare.seed_file(article_id, "SST_01.png", b"", status="created")

    with pytest.raises(SystemExit):
        _run_pushit(["--check-figshare-integrity"])  # no --fix-duplicates

    assert stub_id not in fake_figshare.deleted_file_ids  # reported, not deleted

    with pytest.raises(SystemExit):
        _run_pushit(["--check-figshare-integrity", "--fix-duplicates"])
    # This same invocation still exits non-zero (dup_report reflects what
    # was found in this run's snapshot, taken before the deletion), but
    # the stub is now actually gone -- a follow-up run of
    # --check-figshare-integrity (without --fix-duplicates even) would
    # find no duplicates left to report.
    assert stub_id in fake_figshare.deleted_file_ids


# ---------------------------------------------------------------------------
# --check-figshare-upload: public-reachability check + git command printout
# ---------------------------------------------------------------------------

def test_check_figshare_upload_fails_when_url_not_yet_public(patch_repo_paths, figshare_token):
    repo = patch_repo_paths
    ename = "test_experiment_01"
    exp_docs = repo / "documentation" / "docs" / "pages" / "experiments" / ename
    exp_docs.mkdir(parents=True)
    (exp_docs / "notebooks_urls.json").write_text(json.dumps({
        "SST": "https://ndownloader.figshare.com/files/999",
    }))

    import urllib.error

    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)

    with patch("urllib.request.urlopen", fake_urlopen):
        with pytest.raises(SystemExit) as exc:
            _run_pushit(["--check-figshare-upload", "--ename", ename])
    assert exc.value.code != 0


def test_check_figshare_upload_succeeds_and_prints_git_commands(
    patch_repo_paths, figshare_token, capsys
):
    repo = patch_repo_paths
    ename = "test_experiment_01"
    ofol = repo / "notebooks" / f"mkfigs_output_{ename}"
    exp_docs = repo / "documentation" / "docs" / "pages" / "experiments" / ename
    ofol.mkdir(parents=True)
    exp_docs.mkdir(parents=True)
    make_rendered_notebook(ofol / "SST_rendered.ipynb")
    (exp_docs / "SST.md").write_text("# SST\n")
    (exp_docs / "notebooks_urls.json").write_text(json.dumps({
        "SST": "https://ndownloader.figshare.com/files/999",
    }))

    def fake_urlopen(req, timeout=None):
        class _Resp:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *a): return False
        return _Resp()

    with patch("urllib.request.urlopen", fake_urlopen):
        _run_pushit(["--check-figshare-upload", "--ename", ename])

    out = capsys.readouterr().out
    assert "git commit" in out
    assert "git tag" in out
    assert "notebooks/SST.ipynb" in out
