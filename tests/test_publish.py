"""Tests for mkfigs-pushit --publish and --git-plan.

conftest.FakeFigshareServer deliberately has no publish endpoint, so nothing else in
the suite can publish, even by mistake. These tests add one, and the article-details
endpoint --publish reads, to their own copy of the fake server only.
"""
from __future__ import annotations

import json
import re
import sys
from unittest.mock import patch

import pytest
import responses

from mkfigs import pushit

from .conftest import FIGSHARE_BASE, FakeFigshareServer, make_png, make_rendered_notebook

ENAME = "test_experiment_01"


class PublishableFakeFigshare(FakeFigshareServer):
    """FakeFigshareServer plus GET /account/articles/<id>, POST .../publish and the public
    GET /articles/<id>/files (the files as they were at the last publish)."""

    def __init__(self):
        super().__init__()
        self.published_ids: list[int] = []
        self.public_files: dict[int, list[dict]] = {}

    def snapshot(self, aid):
        """Record the article's current files as its public version."""
        self.public_files[aid] = [
            {"name": f["name"], "computed_md5": f["computed_md5"]} for f in self.files_for(aid)
        ]

    def _public_files(self, request):
        aid = int(re.search(r"/articles/(\d+)/files", request.url).group(1))
        if aid not in self.public_files:
            return (404, {}, json.dumps({"message": "not found"}))
        page = int(re.search(r"page=(\d+)", request.url).group(1)) if "page=" in request.url else 1
        return (200, {}, json.dumps(self.public_files[aid] if page == 1 else []))

    def _article_item(self, request):
        aid = int(re.search(r"articles/(\d+)$", request.url).group(1))
        status = "public" if aid in self.published_ids or self._articles[aid].get("public") else "draft"
        return (200, {}, json.dumps({"id": aid, "title": self._articles[aid]["title"], "status": status}))

    def _publish(self, request):
        aid = int(re.search(r"articles/(\d+)/publish$", request.url).group(1))
        self.published_ids.append(aid)
        self.snapshot(aid)
        return (201, {}, json.dumps({"location": f"{FIGSHARE_BASE}/articles/{aid}"}))

    def register(self, mock):
        super().register(mock)
        base = re.escape(FIGSHARE_BASE)
        mock.add_callback(responses.GET, re.compile(f"{base}/account/articles/(\\d+)$"),
                          callback=self._article_item, content_type="application/json")
        mock.add_callback(responses.POST, re.compile(f"{base}/account/articles/(\\d+)/publish$"),
                          callback=self._publish, content_type="application/json")
        mock.add_callback(responses.GET, re.compile(f"{base}/articles/(\\d+)/files"),
                          callback=self._public_files, content_type="application/json")


@pytest.fixture
def figshare():
    server = PublishableFakeFigshare()
    with responses.RequestsMock(assert_all_requests_are_fired=False) as mock:
        server.register(mock)
        yield server


class _Resp:
    status = 200
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _public(req, timeout=None):
    return _Resp()


def _not_public(req, timeout=None):
    import urllib.error
    raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)


def _run(args):
    with patch.object(sys, "argv", ["mkfigs-pushit", *args]), \
         patch.object(pushit.subprocess, "run"), \
         patch("time.sleep"):
        pushit.main()


def _upload(repo):
    """Run outputs for SST, then a normal pushit upload to the (fake) private article."""
    ofol = repo / "notebooks" / f"mkfigs_output_{ENAME}"
    mdfol = ofol / "mkmd"
    mdfol.mkdir(parents=True)
    make_rendered_notebook(ofol / "SST_rendered.ipynb")
    make_png(mdfol / "SST_01.png")
    (mdfol / "SST.md").write_text(f"# SST\n\n![fig](/assets/experiments/{ENAME}/SST_01.png)\n")
    _run([])


def test_publish_checks_publishes_and_writes_git_plan(patch_repo_paths, figshare, figshare_token, tmp_path):
    repo = patch_repo_paths
    _upload(repo)
    (article_id,) = figshare.created_article_ids
    plan_path = tmp_path / "plan.json"

    with patch("urllib.request.urlopen", _public):
        _run(["--publish", "--git-plan", str(plan_path)])

    assert figshare.published_ids == [article_id]
    plan = json.loads(plan_path.read_text())
    assert plan["branch"] == f"eval-{ENAME}"
    assert plan["tag"].startswith(f"{ENAME}-")
    assert "notebooks/SST.ipynb" in plan["files"]
    assert f"documentation/docs/pages/experiments/{ENAME}/notebooks_urls.json" in plan["files"]
    assert plan["recommended_files"] == ["notebooks/mkfigs.sh"]


def test_publish_refuses_when_integrity_check_fails(patch_repo_paths, figshare, figshare_token):
    repo = patch_repo_paths
    _upload(repo)
    # A file on disk that never reached Figshare: the integrity check reports it MISSING.
    make_png(repo / "notebooks" / f"mkfigs_output_{ENAME}" / "mkmd" / "SST_02.png", b"new figure")

    with patch("urllib.request.urlopen", _public), pytest.raises(SystemExit) as exc:
        _run(["--publish"])
    assert exc.value.code != 0
    assert figshare.published_ids == []


def test_publish_does_not_republish_an_unchanged_public_article(patch_repo_paths, figshare, figshare_token, capsys):
    repo = patch_repo_paths
    _upload(repo)
    (article_id,) = figshare.created_article_ids
    figshare._articles[article_id]["public"] = True
    figshare.snapshot(article_id)

    with patch("urllib.request.urlopen", _public):
        _run(["--publish"])
    assert figshare.published_ids == []
    assert "already public and unchanged" in capsys.readouterr().out


def test_publish_makes_a_new_version_after_an_update(patch_repo_paths, figshare, figshare_token, capsys):
    """Adding a notebook to a published experiment: the article is public, with new files."""
    repo = patch_repo_paths
    _upload(repo)
    (article_id,) = figshare.created_article_ids
    figshare._articles[article_id]["public"] = True
    figshare.snapshot(article_id)
    mdfol = repo / "notebooks" / f"mkfigs_output_{ENAME}" / "mkmd"
    make_rendered_notebook(mdfol.parent / "MLD_rendered.ipynb")
    make_png(mdfol / "MLD_01.png", b"mld figure")
    (mdfol / "MLD.md").write_text(f"# MLD\n\n![fig](/assets/experiments/{ENAME}/MLD_01.png)\n")
    _run([])   # upload the new notebook to the same article

    with patch("urllib.request.urlopen", _public):
        _run(["--publish"])
    assert figshare.published_ids == [article_id]
    assert f"Publishing a new version of {article_id}" in capsys.readouterr().out


def test_publish_refuses_when_two_articles_share_the_title(patch_repo_paths, figshare, figshare_token):
    repo = patch_repo_paths
    _upload(repo)
    (article_id,) = figshare.created_article_ids
    figshare.seed_article(figshare._articles[article_id]["title"])

    with patch("urllib.request.urlopen", _public), pytest.raises(SystemExit) as exc:
        _run(["--publish"])
    assert exc.value.code != 0
    assert figshare.published_ids == []


def test_publish_exits_nonzero_if_links_never_go_public(patch_repo_paths, figshare, figshare_token, tmp_path):
    repo = patch_repo_paths
    _upload(repo)
    plan_path = tmp_path / "plan.json"

    with patch("urllib.request.urlopen", _not_public), pytest.raises(SystemExit) as exc:
        _run(["--publish", "--publish-wait-minutes", "0", "--git-plan", str(plan_path)])
    assert exc.value.code != 0
    assert len(figshare.published_ids) == 1   # published, but no git plan until links work
    assert not plan_path.exists()


def test_check_figshare_upload_writes_git_plan(patch_repo_paths, figshare_token, tmp_path):
    repo = patch_repo_paths
    ofol = repo / "notebooks" / f"mkfigs_output_{ENAME}"
    exp_docs = repo / "documentation" / "docs" / "pages" / "experiments" / ENAME
    ofol.mkdir(parents=True)
    exp_docs.mkdir(parents=True)
    make_rendered_notebook(ofol / "SST_rendered.ipynb")
    (exp_docs / "SST.md").write_text("# SST\n")
    (exp_docs / "notebooks_urls.json").write_text(json.dumps({"SST": "https://ndownloader.figshare.com/files/999"}))
    plan_path = tmp_path / "plan.json"

    with patch("urllib.request.urlopen", _public):
        _run(["--check-figshare-upload", "--ename", ENAME, "--git-plan", str(plan_path)])

    plan = json.loads(plan_path.read_text())
    assert plan["commit_message"] == f"docs: render evaluation figures for {ENAME}"
    assert plan["files"][0] == "notebooks/SST.ipynb"


def test_git_plan_needs_a_mode(patch_repo_paths, tmp_path):
    with pytest.raises(SystemExit) as exc:
        _run(["--git-plan", str(tmp_path / "plan.json")])
    assert exc.value.code == 2
