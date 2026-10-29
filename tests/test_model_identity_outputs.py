"""No ACCESS-OM3 text in another model's outputs.

Covers the user-visible strings PR #10 left hardcoded to access-om3-paper-1:

  - pages/index.md preamble (heading, repo link, authors label)
  - git tag message ReadTheDocs URL and `git add` paths (--check-figshare-upload)
  - per-notebook docs page co-authors (git history lookup at any notebook depth)

Each test also pins the unchanged ACCESS-OM3 default.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from mkfigs import pushit
from mkfigs.configdoc import (
    MkmdWriter,
    get_docs_url,
    get_model_name,
    get_repo_slug,
    get_repo_url,
)

CM3_URL = "https://github.com/ACCESS-Community-Hub/access-cm3-paper-1"


@pytest.fixture
def om3_identity(monkeypatch):
    for var in ("MKFIGS_MODEL_NAME", "MKFIGS_REPO_URL", "MKFIGS_DOCS_URL"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def cm3_identity(monkeypatch):
    monkeypatch.setenv("MKFIGS_MODEL_NAME", "ACCESS-CM3")
    monkeypatch.setenv("MKFIGS_REPO_URL", CM3_URL)
    monkeypatch.delenv("MKFIGS_DOCS_URL", raising=False)


def _layout(tmp_path: Path, monkeypatch, subdir: str = "") -> Path:
    """Fake paper repo; point pushit's module globals at it. Returns HERE."""
    repo = tmp_path / "repo"
    here = repo / "notebooks" / subdir if subdir else repo / "notebooks"
    here.mkdir(parents=True)
    (repo / ".git").mkdir()
    pages = repo / "documentation" / "docs" / "pages"
    pages.mkdir(parents=True)
    monkeypatch.setattr(pushit, "HERE", here)
    monkeypatch.setattr(pushit, "REPO", repo)
    monkeypatch.setattr(pushit, "DOCS_PAGES", pages)
    monkeypatch.setattr(pushit, "MKDOCS_YML", repo / "documentation" / "mkdocs.yml")
    return here


# ---------------------------------------------------------------------------
# identity helpers
# ---------------------------------------------------------------------------

def test_identity_helpers_default_to_om3(om3_identity):
    assert get_model_name() == "ACCESS-OM3"
    assert get_repo_url() == "https://github.com/ACCESS-Community-Hub/access-om3-paper-1"
    assert get_repo_slug() == "ACCESS-Community-Hub/access-om3-paper-1"
    assert get_docs_url() == "https://access-om3-paper-1.readthedocs.io/"


def test_identity_helpers_follow_environment(cm3_identity):
    assert get_model_name() == "ACCESS-CM3"
    assert get_repo_slug() == "ACCESS-Community-Hub/access-cm3-paper-1"
    assert get_docs_url() == "https://access-cm3-paper-1.readthedocs.io/"


def test_docs_url_override(cm3_identity, monkeypatch):
    monkeypatch.setenv("MKFIGS_DOCS_URL", "https://example.org/site")
    assert get_docs_url() == "https://example.org/site/"


def test_pushit_reads_docs_url_from_mkfigs_sh(tmp_path, monkeypatch):
    monkeypatch.delenv("MKFIGS_DOCS_URL", raising=False)
    (tmp_path / "mkfigs.sh").write_text('export MKFIGS_DOCS_URL="https://example.org/x/"\n')
    monkeypatch.setattr(pushit, "HERE", tmp_path)
    pushit.load_model_identity_from_mkfigs_sh()
    assert get_docs_url() == "https://example.org/x/"


# ---------------------------------------------------------------------------
# pages/index.md preamble
# ---------------------------------------------------------------------------

def _index(here_pages: Path) -> str:
    return (here_pages / "index.md").read_text()


def test_index_preamble_om3_default(tmp_path, monkeypatch, om3_identity):
    _layout(tmp_path, monkeypatch)
    pushit.update_top_index("exp1", ["SST"], [], [], "| t |\n", "**authors:** A.")
    text = _index(pushit.DOCS_PAGES)
    assert text.startswith("# ACCESS-OM3 Evaluation Figures: exp1\n")
    assert "[access-om3-paper-1](https://github.com/ACCESS-Community-Hub/access-om3-paper-1/)" in text
    assert "`ACCESS-Community-Hub/access-om3-paper-1/` **authors:** A." in text


def test_index_preamble_has_no_om3_for_cm3(tmp_path, monkeypatch, cm3_identity):
    _layout(tmp_path, monkeypatch, "polished-python")
    pushit.update_top_index("cm3-exp", ["TOA_CRE"], [], [], "| t |\n", "**authors:** A.")
    text = _index(pushit.DOCS_PAGES)
    assert text.startswith("# ACCESS-CM3 Evaluation Figures: cm3-exp\n")
    assert f"[access-cm3-paper-1]({CM3_URL}/)" in text
    assert "`ACCESS-Community-Hub/access-cm3-paper-1/` **authors:** A." in text
    assert "om3" not in text.lower()


def test_index_preamble_extra_block_survives_refresh(tmp_path, monkeypatch, cm3_identity):
    _layout(tmp_path, monkeypatch, "polished-python")
    (pushit.DOCS_PAGES / "index.md").write_text(
        "# Hand-written heading (replaced)\n\n"
        "<!-- preamble-extra -->\n**Tracking issues:**\n\n- #1 mega-issue\n<!-- /preamble-extra -->\n\n"
        "<!-- experiments -->\n\n<!-- trailing note kept -->\n"
    )
    for _ in range(2):  # idempotent across re-runs
        pushit.update_top_index("cm3-exp", ["TOA_CRE"], [], [], "| t |\n", "")
    text = _index(pushit.DOCS_PAGES)
    assert "Hand-written heading" not in text
    assert text.count("**Tracking issues:**") == 1
    assert text.index("**Tracking issues:**") < text.index("<!-- experiments -->")
    assert "<!-- trailing note kept -->" in text
    assert text.count("<!-- experiment:cm3-exp -->") == 1


# ---------------------------------------------------------------------------
# --check-figshare-upload: git add paths and tag URL
# ---------------------------------------------------------------------------

def _check_upload_output(tmp_path, monkeypatch, capsys, subdir):
    here = _layout(tmp_path, monkeypatch, subdir)
    (here / "mkfigs.sh").write_text("ENAME=exp1\nESMDIR=/x.json\narray=(\n  SST\n)\n")
    ofol = here / "mkfigs_output_exp1"
    ofol.mkdir()
    (ofol / "SST_rendered.ipynb").write_text("{}")
    exp_docs = pushit.DOCS_PAGES / "experiments" / "exp1"
    exp_docs.mkdir(parents=True)
    (exp_docs / "SST.md").write_text("x")
    (exp_docs / "notebooks_urls.json").write_text(
        json.dumps({"SST": "https://ndownloader.figshare.com/files/1"})
    )
    monkeypatch.setattr(pushit, "_check_figshare_urls", lambda urls: True)
    pushit.check_figshare_upload_mode("exp1")
    return capsys.readouterr().out


def test_check_upload_paths_om3(tmp_path, monkeypatch, capsys, om3_identity):
    out = _check_upload_output(tmp_path, monkeypatch, capsys, "")
    assert "git add notebooks/SST.ipynb " in out
    assert "https://access-om3-paper-1.readthedocs.io/en/" in out


def test_check_upload_paths_cm3(tmp_path, monkeypatch, capsys, cm3_identity):
    out = _check_upload_output(tmp_path, monkeypatch, capsys, "polished-python")
    assert "git add notebooks/polished-python/SST.ipynb " in out
    assert "git add notebooks/SST.ipynb" not in out
    assert "https://access-cm3-paper-1.readthedocs.io/en/" in out
    assert "om3" not in out.lower()


@pytest.mark.parametrize("identity,subdir", [("om3_identity", ""), ("cm3_identity", "polished-python")])
def test_check_upload_commits_and_tags_on_ename_branch(
    tmp_path, monkeypatch, capsys, request, identity, subdir
):
    """The render commit and its tag go on a branch named eval-ENAME, never on main (#8)."""
    request.getfixturevalue(identity)
    monkeypatch.setattr(pushit, "_git_lines", _fake_git())  # first render: no tags, no branch
    out = _check_upload_output(tmp_path, monkeypatch, capsys, subdir)
    lines = [ln.strip() for ln in out.splitlines()]
    assert any(ln.startswith("git tag -a exp1-") and ".000 " in ln for ln in lines)
    switch = lines.index("git switch -c eval-exp1")
    commit = next(i for i, ln in enumerate(lines) if ln.startswith("git commit"))
    tag = next(i for i, ln in enumerate(lines) if ln.startswith("git tag -a exp1-"))
    assert switch < commit < tag
    assert "git push -u origin eval-exp1" in lines
    assert any(ln.startswith("git push origin exp1-") for ln in lines)
    assert "main --tags" not in out
    assert lines.index("git push -u origin eval-exp1") < next(
        i for i, ln in enumerate(lines) if ln.startswith("git switch main")
    )


def _fake_git(tags=(), branch=False, prev_urls=None):
    """Stand-in for pushit._git_lines: earlier tags, branch presence, and the
    notebooks_urls.json committed at the latest tag."""
    def fake(*args):
        if args[:2] == ("tag", "-l"):
            return list(tags)
        if args[0] == "rev-parse":
            return ["abc123"] if branch else []
        if args[0] == "show":
            return [json.dumps(prev_urls or {})]
        return []
    return fake


def test_next_render_tag_increments_within_month_only():
    from datetime import date
    tags = ["e-2026.07.000", "e-2026.07.001", "e-2026.06.004", "e-x-2026.07.009"]
    assert pushit._next_render_tag("e", date(2026, 7, 9), tags) == "e-2026.07.002"
    assert pushit._next_render_tag("e", date(2026, 8, 1), tags) == "e-2026.08.000"
    assert pushit._next_render_tag("e", date(2026, 8, 1), []) == "e-2026.08.000"


def test_render_tags_ignore_other_experiments_with_same_prefix(monkeypatch, tmp_path):
    monkeypatch.setattr(pushit, "REPO", tmp_path)
    monkeypatch.setattr(pushit, "_git_lines", _fake_git(
        tags=["exp1-2026.07.000", "exp1-1.0-beta-cdfb3543-2026.07.000", "exp1-notes"]
    ))
    assert pushit._render_tags("exp1") == ["exp1-2026.07.000"]


@pytest.mark.parametrize("identity,subdir", [("om3_identity", ""), ("cm3_identity", "polished-python")])
def test_check_upload_rerun_bumps_tag_and_reuses_branch(
    tmp_path, monkeypatch, capsys, request, identity, subdir
):
    """After restore, a re-render tags the next serial on the existing eval-ENAME branch."""
    from datetime import date
    request.getfixturevalue(identity)
    month = date.today().strftime("%Y.%m")
    monkeypatch.setattr(pushit, "_git_lines", _fake_git(
        tags=[f"exp1-{month}.000"], branch=True, prev_urls={"SST": "u"}
    ))
    out = _check_upload_output(tmp_path, monkeypatch, capsys, subdir)
    lines = [ln.strip() for ln in out.splitlines()]
    assert "git switch eval-exp1" in lines
    assert "git switch -c eval-exp1" not in lines
    assert any(ln.startswith(f"git tag -a exp1-{month}.001 ") for ln in lines)
    assert f"git push origin exp1-{month}.001" in lines
    assert "WARNING" not in out


def test_check_upload_rerun_warns_when_restore_skipped(tmp_path, monkeypatch, capsys, om3_identity):
    """Notebooks in the last tag but not in this render mean restore was skipped."""
    from datetime import date
    month = date.today().strftime("%Y.%m")
    monkeypatch.setattr(pushit, "_git_lines", _fake_git(
        tags=[f"exp1-{month}.000"], branch=True, prev_urls={"SST": "u", "MLD": "u"}
    ))
    out = _check_upload_output(tmp_path, monkeypatch, capsys, "")
    assert "WARNING: 1 notebook(s)" in out
    assert "MLD" in out
    assert "mkfigs.restore" in out


# ---------------------------------------------------------------------------
# per-notebook docs page: co-authors from git history at any depth
# ---------------------------------------------------------------------------

def _git_repo_with_notebook(repo: Path, rel: str) -> None:
    nb = repo / rel
    nb.parent.mkdir(parents=True, exist_ok=True)
    nb.write_text("{}")
    env = {"GIT_AUTHOR_NAME": "Ada Author", "GIT_AUTHOR_EMAIL": "a@x",
           "GIT_COMMITTER_NAME": "Ada Author", "GIT_COMMITTER_EMAIL": "a@x",
           "HOME": str(repo), "PATH": "/usr/bin:/bin"}
    for cmd in (["git", "init", "-q"], ["git", "add", "."], ["git", "commit", "-qm", "nb"]):
        subprocess.run(cmd, cwd=repo, check=True, env=env)


@pytest.mark.parametrize("subdir", ["", "polished-python"])
def test_mkmd_coauthors_found_at_any_depth(tmp_path, monkeypatch, subdir, cm3_identity):
    repo = tmp_path / "repo"
    nbdir = repo / "notebooks" / subdir if subdir else repo / "notebooks"
    _git_repo_with_notebook(repo, str((nbdir / "SST.ipynb").relative_to(repo)))
    ofol = nbdir / "mkfigs_output_exp1"
    ofol.mkdir()
    monkeypatch.setenv("MKFIGS_ENAME", "exp1")

    w = MkmdWriter("/fake/exp1/datastore.json", "SST.ipynb", str(ofol) + "/", pm=True)
    assert w.repo_root == repo.resolve()

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig = plt.figure()
    w.savefig(fig, "t", "c")
    md = (ofol / "mkmd" / "SST.md").read_text()
    assert "Ada Author" in md
    assert "unknown" not in md
    assert "Evaluation figures from ACCESS-CM3 experiment" in md


@pytest.mark.parametrize("ename", [
    "MC_25km_jra_iaf+wombatlite_bs1-mpudig-backscat1-4c21c151",  # longest published OM3 ENAME
    "25km-iaf-test-for-AK-expt-7df5ef4c",
    "cm3-run-27-07-2026-PD-control",
])
def test_render_branch_is_a_valid_git_branch_name(ename):
    """Real OM3/CM3 ENAMEs (with '+', '_', long hashes) make legal eval- branch names."""
    branch = pushit.render_branch(ename)
    assert branch == f"eval-{ename}"
    r = subprocess.run(["git", "check-ref-format", "--branch", branch], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
