"""Tests for mkfigs-gallery: a built site squashed into one self-contained preview page."""
from __future__ import annotations

import re

from PIL import Image

from mkfigs.gallery import build, main

ENAME = "run1+wombat"
SHELL = """<!doctype html><html><head>
<link rel="stylesheet" href="{up}assets/main.css">
<link rel="stylesheet" href="https://fonts.googleapis.com/css?family=Roboto">
<script src="{up}assets/bundle.js"></script></head>
<body dir="ltr" data-md-color-scheme="custom-light">
<header class="md-header md-header--shadow"><img src="{up}assets/logo.png" alt="logo">Site
<form class="md-search__form"><input></form></header>
<article class="md-content__inner md-typeset">
<a href="https://github.com/x/edit" title="Edit this page" class="md-content__button">edit</a>
{body}
<aside class="md-source-file">updated today</aside>
</article><script>alert(1)</script></body></html>"""


def _site(tmp_path):
    site = tmp_path / "site"
    (site / "assets").mkdir(parents=True)
    (site / "assets/main.css").write_text(".md-typeset{color:red} .x{background:url(../img/bg.png)}")
    Image.new("RGB", (40, 40), "orange").save(site / "assets/logo.png")
    Image.new("RGB", (3000, 1500), "blue").save(site / "assets/fig_01.png")
    (site / "index.html").write_text(SHELL.format(up="", body=f"""
<h1 id="home">Landing page</h1><p>Summary for {ENAME}.</p>
<table><tr><td><a href="experiments/{ENAME}/SST/">Figures</a></td>
<td><a href="experiments/{ENAME}/notebooks/SST/">Notebook</a></td>
<td><a href="#home">top</a></td><td><a href="https://github.com/x/issues/1">#1</a></td></tr></table>"""))
    for nb in ("SST", "MLD"):
        page = site / "experiments" / ENAME / nb
        page.mkdir(parents=True)
        (page / "index.html").write_text(SHELL.format(up="../../../", body=f"""
<h1 id="{nb.lower()}">{nb}</h1><h2 id="maps">Maps</h2><a href="#maps">maps</a>
<p><img alt="fig" src="../../../assets/fig_01.png" /></p><p><a href="../notebooks/{nb}/">View rendered notebook</a></p>"""))
    (site / "experiments" / ENAME / "notebooks" / "SST").mkdir(parents=True)
    (site / "experiments" / ENAME / "notebooks" / "SST" / "index.html").write_text(SHELL.format(up="", body="<h1>nb</h1>"))
    return site


def test_one_self_contained_file(tmp_path):
    out = build(_site(tmp_path), ENAME, max_width=800, note="https://example/run/1")
    assert "<script" not in out
    assert "<link" not in out                      # CSS inlined, web fonts dropped
    assert ".md-typeset{color:red}" in out
    assert "url(../img/bg.png)" not in out         # no references to other files
    assert re.search(r'src="(?!data:)', out) is None
    assert out.count("data:image/webp;base64,") == 3   # logo + one figure per page
    assert "Edit this page" not in out and "updated today" not in out and "md-search__form" not in out
    assert "https://example/run/1" in out and "Preview only: this is not the full result" in out


def test_landing_page_first_then_each_page(tmp_path):
    out = build(_site(tmp_path), ENAME)
    assert out.index('id="nb-home"') < out.index('id="nb-MLD"') < out.index('id="nb-SST"')
    assert 'id="nb-notebooks"' not in out          # rendered notebooks are left out
    assert 'href="#nb-home">Home<' in out and "SST <small>(1)</small>" in out


def test_links(tmp_path):
    out = build(_site(tmp_path), ENAME)
    assert 'href="#nb-SST">Figures' in out                              # into this preview
    assert '<a title="not included in this preview">Notebook' in out    # disabled
    assert '<a title="not included in this preview">View rendered notebook' in out
    assert 'href="https://github.com/x/issues/1"' in out                # external links kept
    # Heading ids made unique per page, with in-page links following them.
    assert 'id="SST-maps"' in out and 'id="MLD-maps"' in out
    assert 'href="#SST-maps"' in out and 'href="#home-home"' in out


def test_site_absolute_image_paths(tmp_path):
    # mkfigs-preview leaves pushit's /assets/experiments/... links as they are.
    site = _site(tmp_path)
    page = site / "experiments" / ENAME / "SST" / "index.html"
    page.write_text(page.read_text().replace('src="../../../assets/fig_01.png"', 'src="/assets/fig_01.png"'))
    out = build(site, ENAME)
    assert out.count("data:image/webp;base64,") == 3
    assert 'src="/assets' not in out


def test_figures_are_shrunk(tmp_path):
    import base64
    import io
    out = build(_site(tmp_path), ENAME, max_width=500)
    data = re.findall(r"data:image/webp;base64,([A-Za-z0-9+/=]+)", out)
    widths = sorted(Image.open(io.BytesIO(base64.b64decode(d))).width for d in data)
    assert widths[-1] == 500


def test_main_writes_file(tmp_path, capsys):
    out = tmp_path / "preview.html"
    main([str(_site(tmp_path)), ENAME, str(out)])
    assert out.read_text().startswith("<!doctype html>")
    assert "MB)" in capsys.readouterr().out


def test_banner_says_what_is_missing_and_where_the_full_site_is(tmp_path):
    run = "https://github.com/ACCESS-Community-Hub/access-om3-paper-1/actions/runs/123456"
    out = build(_site(tmp_path), ENAME, max_width=900, run_url=run, artifact=f"site-{ENAME}")
    top = out[out.index('class="admonition warning"'):out.index('id="nb-home"')]
    for missing in ("rendered notebooks", "links to other pages", "JavaScript", "900 px"):
        assert missing in top
    assert f'href="{run}"' in top
    assert f"gh run download 123456 -R ACCESS-Community-Hub/access-om3-paper-1 -n site-{ENAME}" in top
    assert "python3 -m http.server" in top


def test_banner_without_run_details(tmp_path):
    out = build(_site(tmp_path), ENAME)
    assert "build the site with mkfigs-preview" in out
