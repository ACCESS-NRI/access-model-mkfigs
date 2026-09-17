"""Shared fixtures for the mkfigs test suite.

The centrepiece is FakeFigshareServer: a small in-memory stand-in for the
Figshare v2 API surface that mkfigs.configdoc.FigshareUploader actually
calls (article search/list/create, file init/parts/complete/delete). It is
registered with the `responses` library so every `requests.request(...)`
and `requests.put(...)` call made by the code under test is intercepted --
no real network traffic, no real Figshare account needed, and nothing ever
gets published (the fake server has no publish endpoint at all, so a bug
that tried to publish would fail loudly with a ConnectionError from
`responses`, not silently succeed against a real account).

This fixture is deliberately stateful (not a flat list of `responses.add()`
stubs) because several of the behaviours worth testing -- resuming an
interrupted upload, cleaning up a duplicate stub, refreshing a stale
notebooks_urls.json entry -- only make sense as a *sequence* of calls that
all see a consistent, evolving view of "what's on Figshare right now".
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest
import responses

FIGSHARE_BASE = "https://api.figshare.com/v2"


class FakeFigshareServer:
    """In-memory double for the handful of Figshare v2 endpoints mkfigs uses.

    Deliberately does NOT implement a publish endpoint -- there is no
    legitimate reason for anything in this test suite to publish an
    article, mocked or otherwise (see tests/live/ for the one real,
    private-article-only exception, run opt-in against the real API).
    """

    def __init__(self):
        self._articles: dict[int, dict] = {}   # id -> {"title": str}
        self._files: dict[int, dict] = {}       # id -> file dict (see _file_json)
        self._article_files: dict[int, list[int]] = {}  # article_id -> [file_id]
        self._next_article_id = 1000
        self._next_file_id = 5000
        self.deleted_file_ids: list[int] = []   # audit trail for assertions

    # -- helpers ---------------------------------------------------------
    def _file_json(self, fid: int) -> dict:
        f = self._files[fid]
        d = {
            "id": fid,
            "name": f["name"],
            "size": f["size"],
            "status": f["status"],
            "supplied_md5": f.get("supplied_md5", ""),
            "computed_md5": f.get("computed_md5", ""),
        }
        if f["status"] == "available":
            d["download_url"] = f"https://ndownloader.figshare.com/files/{fid}"
        else:
            d["upload_url"] = f"{FIGSHARE_BASE}/account/articles/{f['article_id']}/files/{fid}/upload"
        return d

    # -- test-setup helpers (used directly by tests, not via HTTP) -------
    def seed_article(self, title: str, article_id: int | None = None) -> int:
        aid = article_id if article_id is not None else self._next_article_id
        self._next_article_id = max(self._next_article_id, aid + 1)
        self._articles[aid] = {"title": title}
        self._article_files.setdefault(aid, [])
        return aid

    def seed_file(self, article_id: int, name: str, content: bytes,
                  status: str = "available", file_id: int | None = None) -> int:
        """Insert a file as if a previous run had already uploaded it."""
        fid = file_id if file_id is not None else self._next_file_id
        self._next_file_id = max(self._next_file_id, fid + 1)
        md5 = hashlib.md5(content).hexdigest()
        self._files[fid] = {
            "article_id": article_id, "name": name, "size": len(content),
            "status": status,
            "supplied_md5": md5,
            "computed_md5": md5 if status == "available" else "",
        }
        self._article_files.setdefault(article_id, []).append(fid)
        return fid

    def files_for(self, article_id: int) -> list[dict]:
        return [self._file_json(fid) for fid in self._article_files.get(article_id, [])]

    def expire_file(self, file_id: int) -> None:
        """Test helper: simulate a file having been removed from Figshare
        by something other than the code under test (e.g. manual cleanup,
        an out-of-band duplicate-removal pass) so a "stale URL" scenario
        can be set up without going through the real delete endpoint.
        """
        self._files.pop(file_id, None)
        for ids in self._article_files.values():
            if file_id in ids:
                ids.remove(file_id)

    # -- HTTP callbacks ----------------------------------------------------
    def _articles_search(self, request):
        body = json.loads(request.body or "{}")
        # Real Figshare's search_for syntax is ":title: <text>"; we do a
        # plain substring match here, which is enough to exercise the
        # "found via search" and "search finds nothing, fall back to
        # pagination" code paths without reimplementing Figshare's query
        # parser (including its real '+'/'-' quirk -- see
        # test_figshare_uploader.py for a dedicated test of that case).
        search_for = body.get("search_for", "")
        text = search_for.replace(":title:", "").strip()
        matches = [
            {"id": aid, "title": a["title"]}
            for aid, a in self._articles.items()
            if text and text in a["title"]
        ]
        return (200, {}, json.dumps(matches))

    def _articles_collection(self, request):
        if request.method == "POST":
            data = json.loads(request.body or "{}")
            aid = self._next_article_id
            self._next_article_id += 1
            self._articles[aid] = {"title": data["title"]}
            self._article_files[aid] = []
            loc = f"{FIGSHARE_BASE}/account/articles/{aid}"
            return (201, {}, json.dumps({"location": loc}))

        # GET, paginated
        qs = dict(pair.split("=") for pair in (request.url.split("?", 1)[1] or "").split("&") if "=" in pair) \
            if "?" in request.url else {}
        page = int(qs.get("page", 1))
        page_size = int(qs.get("page_size", 10))
        all_articles = [{"id": aid, "title": a["title"]} for aid, a in self._articles.items()]
        start = (page - 1) * page_size
        batch = all_articles[start:start + page_size]
        return (200, {}, json.dumps(batch))

    def _article_files_collection(self, request, article_id):
        article_id = int(article_id)
        if request.method == "POST":
            data = json.loads(request.body or "{}")
            fid = self._next_file_id
            self._next_file_id += 1
            self._files[fid] = {
                "article_id": article_id, "name": data["name"], "size": data["size"],
                "status": "created", "supplied_md5": data.get("md5", ""), "computed_md5": "",
            }
            self._article_files.setdefault(article_id, []).append(fid)
            loc = f"{FIGSHARE_BASE}/account/articles/{article_id}/files/{fid}"
            return (201, {}, json.dumps({"location": loc}))

        # GET, paginated file listing
        qs = dict(pair.split("=") for pair in (request.url.split("?", 1)[1] or "").split("&") if "=" in pair) \
            if "?" in request.url else {}
        page = int(qs.get("page", 1))
        page_size = int(qs.get("page_size", 10))
        all_files = self.files_for(article_id)
        start = (page - 1) * page_size
        batch = all_files[start:start + page_size]
        return (200, {}, json.dumps(batch))

    def _file_item(self, request, article_id, file_id):
        file_id = int(file_id)
        if request.method == "GET":
            return (200, {}, json.dumps(self._file_json(file_id)))
        if request.method == "POST":
            # "complete" call: promote to available, computed_md5 = supplied_md5
            f = self._files[file_id]
            f["status"] = "available"
            f["computed_md5"] = f["supplied_md5"]
            return (200, {}, json.dumps({}))
        if request.method == "DELETE":
            self._files.pop(file_id, None)
            for aid, ids in self._article_files.items():
                if file_id in ids:
                    ids.remove(file_id)
            self.deleted_file_ids.append(file_id)
            return (204, {}, "")
        raise AssertionError(f"unexpected method {request.method}")

    def _file_upload_parts(self, request, article_id, file_id):
        file_id = int(file_id)
        f = self._files[file_id]
        size = f["size"]
        # Single-part uploads only -- sufficient for the small fixture
        # files this suite uploads. Multi-part chunking itself is
        # exercised in test_figshare_uploader.py via _upload_parts
        # directly against a synthetic parts_info dict, not over HTTP.
        parts = f.setdefault("_parts", [{
            "partNo": 1, "startOffset": 0, "endOffset": max(size - 1, 0),
            "status": "PENDING",
        }])
        return (200, {}, json.dumps({"parts": parts}))

    def _file_upload_part(self, request, article_id, file_id, part_no):
        file_id, part_no = int(file_id), int(part_no)
        f = self._files[file_id]
        for p in f.get("_parts", []):
            if p["partNo"] == part_no:
                p["status"] = "COMPLETE"
        return (200, {}, "")

    def register(self, mock: responses.RequestsMock) -> None:
        base = re.escape(FIGSHARE_BASE)
        mock.add_callback(
            responses.POST, re.compile(f"{base}/account/articles/search"),
            callback=self._articles_search, content_type="application/json",
        )
        mock.add_callback(
            responses.GET, re.compile(f"{base}/account/articles(\\?.*)?$"),
            callback=self._articles_collection, content_type="application/json",
        )
        mock.add_callback(
            responses.POST, re.compile(f"{base}/account/articles$"),
            callback=self._articles_collection, content_type="application/json",
        )
        mock.add_callback(
            responses.GET,
            re.compile(f"{base}/account/articles/(\\d+)/files(\\?.*)?$"),
            callback=lambda r: self._article_files_collection(r, re.search(r"articles/(\d+)/files", r.url).group(1)),
            content_type="application/json",
        )
        mock.add_callback(
            responses.POST,
            re.compile(f"{base}/account/articles/(\\d+)/files$"),
            callback=lambda r: self._article_files_collection(r, re.search(r"articles/(\d+)/files", r.url).group(1)),
            content_type="application/json",
        )
        mock.add_callback(
            responses.GET,
            re.compile(f"{base}/account/articles/(\\d+)/files/(\\d+)/upload$"),
            callback=lambda r: self._file_upload_parts(
                r, *re.search(r"articles/(\d+)/files/(\d+)/upload", r.url).groups()
            ),
            content_type="application/json",
        )
        mock.add_callback(
            responses.PUT,
            re.compile(f"{base}/account/articles/(\\d+)/files/(\\d+)/upload/(\\d+)$"),
            callback=lambda r: self._file_upload_part(
                r, *re.search(r"articles/(\d+)/files/(\d+)/upload/(\d+)", r.url).groups()
            ),
        )
        for method in (responses.GET, responses.POST, responses.DELETE):
            mock.add_callback(
                method,
                re.compile(f"{base}/account/articles/(\\d+)/files/(\\d+)$"),
                callback=lambda r: self._file_item(
                    r, *re.search(r"articles/(\d+)/files/(\d+)$", r.url).groups()
                ),
                content_type="application/json",
            )


@pytest.fixture
def fake_figshare():
    """Yield a fresh FakeFigshareServer wired into a `responses` mock.

    Any request to api.figshare.com not matched by one of the registered
    patterns raises ConnectionError (responses' default for unmatched
    requests) rather than silently falling through to the real network --
    that's the safety property that makes it safe to never worry about a
    stray call accidentally hitting production Figshare from this suite.
    """
    server = FakeFigshareServer()
    with responses.RequestsMock(assert_all_requests_are_fired=False) as mock:
        server.register(mock)
        yield server


@pytest.fixture
def fake_paper_repo(tmp_path: Path) -> Path:
    """Build a minimal, real-shaped ACCESS-OM3-style paper repo under tmp_path.

    Mirrors just enough of access-om3-paper-1's layout (mkfigs.sh,
    notebooks/, documentation/mkdocs.yml, documentation/docs/pages/index.md,
    CITATION.cff) for pushit.py/restore.py to operate on. Returns the repo
    root; the notebooks/ dir is repo_root / "notebooks".
    """
    repo = tmp_path / "paper-repo"
    notebooks = repo / "notebooks"
    notebooks.mkdir(parents=True)

    (repo / "CITATION.cff").write_text(
        "cff-version: 1.2.0\n"
        "authors:\n"
        "  - family-names: \"Bull\"\n"
        "    given-names: \"Chris\"\n"
    )

    (notebooks / "mkfigs.sh").write_text(
        "#!/bin/bash\n"
        "ENAME=test_experiment_01\n"
        "ESMDIR=/g/data/tm70/fake/${ENAME}/datastore.json\n"
        "array=(\n"
        "  SST\n"
        "  MLD\n"
        ")\n"
    )

    docs = repo / "documentation"
    (docs / "docs" / "pages").mkdir(parents=True)
    (docs / "docs" / "pages" / "index.md").write_text("# Placeholder\n\n<!-- experiments -->\n")
    (docs / "mkdocs.yml").write_text(
        "site_name: fake-paper-1\n"
        "nav:\n"
        "  - Home: pages/index.md\n"
        "plugins:\n"
        "  - search\n"
    )
    return repo


@pytest.fixture
def patch_repo_paths(monkeypatch, fake_paper_repo: Path):
    """Point mkfigs.pushit's module-level path globals at fake_paper_repo.

    pushit.py resolves HERE/REPO/DOCS_PAGES/MKDOCS_YML once, at import
    time, from cwd -- not per-call. That makes them impossible to steer
    correctly via a plain monkeypatch.chdir() in a test (the module is
    typically already imported by the time a test runs, and even a fresh
    import only ever sees pytest's own invocation cwd, never a per-test
    tmp_path). This fixture patches those four names directly instead,
    which is the only reliable way to redirect pushit.py at a fake repo
    today. Recommend hoisting this resolution into a function called from
    main() instead of a module-level side effect -- see PLAN.md.
    """
    from mkfigs import pushit

    notebooks_dir = fake_paper_repo / "notebooks"
    monkeypatch.setattr(pushit, "HERE", notebooks_dir)
    monkeypatch.setattr(pushit, "REPO", fake_paper_repo)
    monkeypatch.setattr(pushit, "DOCS_PAGES", fake_paper_repo / "documentation" / "docs" / "pages")
    monkeypatch.setattr(pushit, "MKDOCS_YML", fake_paper_repo / "documentation" / "mkdocs.yml")
    return fake_paper_repo


@pytest.fixture(autouse=True)
def _no_nci_gate(monkeypatch):
    """Neutralise the `_check_nci_environment()` guard that run.py,
    pushit.py, and restore.py each call as the first line of main().

    That guard hard `sys.exit`s unless the NCI-internal `nci_ipynb`
    package is importable -- which it never is outside an NCI login-node
    conda environment, including in ordinary GitHub Actions CI. Without
    neutralising it, every test that calls one of these main() functions
    would fail immediately with SystemExit(1), regardless of anything
    this suite is actually trying to test. Applied autouse so no test
    has to remember to opt in.
    """
    from mkfigs import pushit as _pushit_mod
    from mkfigs import restore as _restore_mod
    from mkfigs import run as _run_mod
    monkeypatch.setattr(_run_mod, "_check_nci_environment", lambda: None)
    monkeypatch.setattr(_pushit_mod, "_check_nci_environment", lambda: None)
    monkeypatch.setattr(_restore_mod, "_check_nci_environment", lambda: None)


@pytest.fixture
def figshare_token(monkeypatch):
    """Ensure resolve_figshare_token() finds a (fake) token in every test."""
    monkeypatch.setenv("FIGSHARE_TOKEN", "fake-token-for-tests")
    return "fake-token-for-tests"


def make_png(path: Path, payload: bytes = b"not-a-real-png-but-thats-fine") -> None:
    path.write_bytes(payload)


def make_rendered_notebook(path: Path, cell_text: str = "print('ok')") -> None:
    nb = {
        "cells": [{"cell_type": "code", "source": [cell_text], "outputs": [],
                   "execution_count": 1, "metadata": {}}],
        "metadata": {"kernelspec": {"display_name": "Python 3 (ipykernel)", "name": "python3"}},
        "nbformat": 4, "nbformat_minor": 5,
    }
    path.write_text(json.dumps(nb))
