"""
Tests for offline preview workflow
"""

import subprocess
import sys
from pathlib import Path
import pytest

from mkfigs import preview, preview_branch


def make_paper(tmp_path, notebooks_subdir="notebooks"):
    """
    Create a minimal OM3 or CM3 paper repository
    """
    repo = tmp_path / "paper"
    nbdir = repo / notebooks_subdir
    nbdir.mkdir(parents=True)

    (nbdir / "mkfigs.sh").write_text(
        "ENAME=old-experiment\n"
        "# ENAME=ignored-experiment\n"
        "ENAME=test-experiment\n"
    )

    output = nbdir / "mkfigs_output_test-experiment"
    (output / "mkmd").mkdir(parents=True)

    docs = repo / "documentation"
    (docs / "docs/pages").mkdir(parents=True)
    (docs / "mkdocs.yml").write_text("site_name: Test\n")
    (docs / "docs/pages/index.md").write_text("# Original\n")

    (repo / "CITATION.cff").write_text(
        "cff-version: 1.2.0\n"
        "title: Test paper\n"
        "authors:\n"
        "  - family-names: John\n"
        "    given-names: John\n"
    )
    return repo, nbdir


@pytest.mark.parametrize(
    "notebooks_subdir",
    ["notebooks", "notebooks/polished-python"],
)
def test_locate_om3_and_cm3(tmp_path, notebooks_subdir):
    """
    Preview should support both paper repository layouts.
    """
    repo, nbdir = make_paper(tmp_path, notebooks_subdir)

    found_dir, ename = preview.locate(repo)

    assert found_dir == nbdir
    assert ename == "test-experiment"


@pytest.mark.parametrize(
    "workspace_location",
    ["repo", "inside_repo", "parent"],
)
def test_preview_rejects_overlapping_workspace(
    tmp_path, monkeypatch, workspace_location
):
    """
    Preview must not write its workspace inside the source repository.
    """
    repo, _ = make_paper(tmp_path)

    workspaces = {
        "repo": repo,
        "inside_repo": repo / "preview",
        "parent": tmp_path,
    }
    workspace = workspaces[workspace_location]

    monkeypatch.setattr(
        sys,
        "argv",
        ["mkfigs-preview", str(repo), "--preview-dir", str(workspace)],
    )

    # If the workspace validation fails to stop execution,
    # this makes the test fail before any files are modified.
    def unexpected_refresh(*args, **kwargs):
        pytest.fail("refresh() must not run for an unsafe workspace")

    monkeypatch.setattr(preview, "refresh", unexpected_refresh)

    with pytest.raises(SystemExit) as exc:
        preview.main()

    assert exc.value.code != 0


def test_refresh_preserves_citation_and_uses_local_outputs(
    tmp_path, monkeypatch
):
    """
    Refresh should use a copy, retain authors, and skip Figshare.
    """
    repo, nbdir = make_paper(tmp_path)
    workspace = tmp_path / "paper-preview"

    output = nbdir / "mkfigs_output_test-experiment"

    # Simulate existing notebook results.
    (output / "mkmd/SST.png").write_bytes(b"test png")
    (output / "SST_rendered.ipynb").write_text("{}")

    # Avoid running git init: this test focuses on refresh().
    (workspace / ".git").mkdir(parents=True)

    original_index = (
        repo / "documentation/docs/pages/index.md"
    ).read_text()

    original_citation = (
        repo / "CITATION.cff"
    ).read_text()

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))

        # Simulate pushit generating a Markdown page.
        md = (
            workspace
            / "documentation/docs/pages/experiments"
            / "test-experiment/SST.md"
        )
        md.parent.mkdir(parents=True, exist_ok=True)
        md.write_text("# SST\n")

        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout="  OK: 1\n",
            stderr="",
        )

    monkeypatch.setattr(preview.subprocess, "run", fake_run)

    preview.refresh(repo, nbdir, "test-experiment", workspace)

    # Commit 2: author metadata should be available to pushit.
    assert (
        workspace / "CITATION.cff"
    ).read_text() == original_citation

    # The original repository must remain unchanged.
    assert (
        repo / "documentation/docs/pages/index.md"
    ).read_text() == original_index

    assert (
        repo / "CITATION.cff"
    ).read_text() == original_citation

    # The local assets should be available to MkDocs.
    assets = (
        workspace
        / "documentation/docs/assets/experiments"
        / "test-experiment"
    )
    assert (assets / "SST.png").exists()

    notebook = (
        workspace
        / "documentation/docs/pages/experiments"
        / "test-experiment/notebooks/SST.ipynb"
    )
    assert notebook.exists()

    # pushit should run in a separate interpreter,
    # using the preview's notebook directory.
    assert len(calls) == 1

    cmd, kwargs = calls[0]

    assert cmd[0] == sys.executable
    assert cmd[1] == "-c"
    assert "--skip-figshare" in cmd[2]
    assert cmd[3] == "test-experiment"
    assert Path(kwargs["cwd"]) == workspace / "notebooks"


def test_preview_branch_rejects_organisation_fork(
    tmp_path, monkeypatch
):
    """
    Preview branches must not target ACCESS organisation repos.
    """
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "mkfigs-preview-branch",
            "--fork",
            "git@github.com:ACCESS-NRI/access-om3-paper-1.git",
            str(tmp_path),
        ],
    )

    with pytest.raises(SystemExit, match="refusing"):
        preview_branch.main()
