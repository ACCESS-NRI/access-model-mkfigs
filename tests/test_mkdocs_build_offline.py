"""Render the documentation site offline with `mkdocs build --strict`.

This is deliberately NOT a clone of the real access-om3-paper-1 repo --
that repo's mkdocs.yml pulls in a custom theme (overrides/), a
git-revision-date plugin that wants real git history, mkdocs-bibtex
against references.bib, and two GitHub-hosted plugins
(mkdocs_events_plugin, mkdocs_include_configuration_stubs_plugin) that
would need to be installed from source on every test run. None of that is
what this test exists to catch. What matters for access-model-mkfigs's own
test suite is narrower and more stable: does a docs tree shaped the way
pushit.py actually produces one (an experiment folder with a per-notebook
summary .md + a notebooks/<nb>.ipynb, wired into mkdocs.yml's nav, with
the mkdocs-jupyter plugin enabled) actually build into HTML with the same
core theme/plugin combination the real sites use?
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

FIXTURE_SITE = Path(__file__).parent / "fixtures" / "minimal_site"


def _mkdocs_stack_available() -> bool:
    """True only if every package the fixture site's mkdocs.yml actually
    needs is importable in *this* interpreter: core mkdocs, the material
    theme, and the mkdocs-jupyter plugin.

    Checking for bare `mkdocs` alone isn't enough -- an environment can
    have mkdocs itself installed (e.g. for building real docs elsewhere)
    without the specific theme/plugin this fixture site declares. That
    showed up in practice as a hard failure ("Config value 'plugins': The
    'mkdocs-jupyter' plugin is not installed") instead of a clean skip,
    in an env that had mkdocs + mkdocs-material but not mkdocs-jupyter.
    """
    import importlib.util
    return all(
        importlib.util.find_spec(mod) is not None
        for mod in ("mkdocs", "mkdocs_jupyter", "material")
    )


@pytest.mark.skipif(not _mkdocs_stack_available(), reason="mkdocs/mkdocs-material/mkdocs-jupyter not all installed in this environment")
def test_minimal_fixture_site_builds_cleanly(tmp_path):
    """The fixture site should build cleanly, including a rendered HTML page for the notebook."""
    site_dir = tmp_path / "site"
    result = subprocess.run(
        [sys.executable, "-m", "mkdocs", "build", "--strict", "-d", str(site_dir)],
        cwd=str(FIXTURE_SITE),
        capture_output=True, text=True,
    )
    assert result.returncode == 0, (
        f"mkdocs build failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    assert (site_dir / "pages" / "index.html").exists()
    assert (site_dir / "pages" / "experiments" / "exp1" / "SST" / "index.html").exists()
    # the notebook itself was rendered into its own page, not just linked
    nb_pages = list(site_dir.glob("pages/experiments/exp1/notebooks/**/*.html"))
    assert nb_pages, "expected mkdocs-jupyter to render SST.ipynb into an HTML page"


def test_fixture_site_nav_matches_pushit_generated_shape(tmp_path):
    """Sanity-check that fixtures/minimal_site's nav: block has the exact
    shape update_mkdocs_nav() actually generates (Summary + Notebook
    sub-entries under a top-level notebook name), so this test doesn't
    quietly drift away from what pushit.py really produces. If pushit.py's
    nav shape ever changes, update fixtures/minimal_site/mkdocs.yml to match
    and this assertion will catch the mismatch.

    Unlike the test above, this one only parses YAML (via the project's
    own pyyaml dependency) -- it never needs mkdocs/material/mkdocs-jupyter
    installed at all, so it always runs rather than sharing the skip
    condition above.
    """
    import yaml
    data = yaml.safe_load((FIXTURE_SITE / "mkdocs.yml").read_text())
    sst_entry = next(item for item in data["nav"] if "SST" in item)
    sub_keys = {k for entry in sst_entry["SST"] for k in entry}
    assert sub_keys == {"Summary", "Notebook"}
