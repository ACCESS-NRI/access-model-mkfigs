# Copyright 2024 ACCESS-NRI and contributors. See the top-level COPYRIGHT file for details.
# SPDX-License-Identifier: Apache-2.0

"""mkfigs-gallery: a built site's landing page and one experiment's pages, as a single self-contained HTML file.

    mkfigs-gallery SITE_DIR ENAME OUT.html [--max-width PX] [--quality Q] [--note TEXT]

SITE_DIR is a site built by mkdocs (e.g. by mkfigs-preview, from a run that never touched Figshare).
The file keeps the site's look: the real header, the theme's and the site's CSS (inlined), the landing
page's text (run summary tables included), then every experiment page's text, figures and captions, one
after another, with a contents list on the left.
Figures are shrunk to --max-width and re-encoded as WebP so the file stays small enough to open in a
browser. Scripts, web fonts, rendered notebooks and links to other pages are left out: it is a
preview to look at, not a working copy of the site, and the note at the top says so, with where to get
the full site (--run-url, --artifact). Needs only Pillow.
"""
from __future__ import annotations

import argparse
import base64
import html
import io
import re
import sys
from pathlib import Path

from PIL import Image

_ARTICLE_RE = re.compile(r'<article class="md-content__inner md-typeset">(.*?)</article>', re.S)
_HEADER_RE = re.compile(r'<header class="md-header[^"]*".*?</header>', re.S)
_BODY_RE = re.compile(r"<body[^>]*>")
_CSS_LINK_RE = re.compile(r'<link rel="stylesheet" href="([^"]+)">')
_IMG_RE = re.compile(r'(<img\b[^>]*?\bsrc=")([^"]+)(")')
_LOCAL_HREF_RE = re.compile(r'<a href="(?!https?:|mailto:|#)[^"]*"')
_DROP_RES = [
    re.compile(r'<a href="[^"]*" title="Edit this page".*?</a>', re.S),     # edit button
    re.compile(r'<aside class="md-source-file">.*?</aside>', re.S),         # "last updated" dates
    re.compile(r"<script\b.*?</script>", re.S),
    re.compile(r'<form class="md-search__form".*?</form>', re.S),           # search needs JavaScript
    re.compile(r"<!--.*?-->", re.S),
]


def image_data_uri(path: Path, max_width: int, quality: int) -> str:
    with Image.open(path) as im:
        if im.width > max_width:
            im = im.resize((max_width, round(im.height * max_width / im.width)), Image.LANCZOS)
        if im.mode not in ("RGB", "RGBA"):
            im = im.convert("RGBA")
        buf = io.BytesIO()
        im.save(buf, "WEBP", quality=quality, method=6)
    return "data:image/webp;base64," + base64.b64encode(buf.getvalue()).decode()


def _local(src: str, base: Path, site: Path) -> Path:
    """The file a page link points to: site-absolute (/assets/...) or relative to the page."""
    return (site / src.lstrip("/")) if src.startswith("/") else (base / src).resolve()


def inline_images(fragment: str, base: Path, max_width: int, quality: int, site: Path | None = None) -> str:
    site = site or base

    def repl(m):
        src = m.group(2)
        if src.startswith(("data:", "http:", "https:", "//")):
            return m.group(0)
        path = _local(src, base, site)
        if not path.is_file():
            return m.group(0)
        return m.group(1) + image_data_uri(path, max_width, quality) + m.group(3)
    return _IMG_RE.sub(repl, fragment)


def inline_css(page: str, base: Path, site: Path) -> str:
    """The page's local stylesheets, concatenated (web fonts and icon fonts are left out)."""
    out = []
    for href in _CSS_LINK_RE.findall(page):
        path = _local(href, base, site)
        if href.startswith("http") or "fontello" in href or not path.is_file():
            continue
        css = path.read_text()
        css = re.sub(r"url\((?!['\"]?data:)[^)]*\)", "none", css)   # drop references to other files
        out.append(css)
    return "\n".join(out)


def tidy(fragment: str, prefix: str, ename: str = "") -> str:
    for rx in _DROP_RES:
        fragment = rx.sub("", fragment)
    if ename:   # links to this experiment's pages (the landing page's "Figures" links) -> their sections here
        fragment = re.sub(rf'href="(?:\.\./)*(?:[\w.-]+/)*experiments/{re.escape(ename)}/([\w.+-]+)/"',
                          r'href="#nb-\1"', fragment)
    fragment = _LOCAL_HREF_RE.sub('<a title="not included in this preview"', fragment)
    # Each page becomes a section of one page: keep heading ids (and links to them) unique.
    fragment = re.sub(r'\bid="([^"]+)"', rf'id="{prefix}-\1"', fragment)
    return re.sub(r'href="#(?!nb-)([^"]+)"', rf'href="#{prefix}-\1"', fragment)


def _banner(ename: str, note: str, run_url: str, artifact: str, max_width: int) -> str:
    """The note at the top: this is a reduced preview, what is left out, and where the full site is."""
    e = html.escape
    where = ""
    m = re.match(r"https://github\.com/([\w.-]+/[\w.-]+)/actions/runs/(\d+)", run_url)
    if run_url and artifact:
        cmds = (f"gh run download {m.group(2)} -R {e(m.group(1))} -n {e(artifact)} -D {e(artifact)}\n" if m else "")
        cmds += f"python3 -m http.server -d {e(artifact)}    # then open http://localhost:8000"
        where = (f'download the <code>{e(artifact)}</code> artifact from <a href="{e(run_url)}">the GitHub '
                 f"Actions run</a>, unzip it into a folder of that name and serve it:</p>"
                 f"<pre><code>{cmds}</code></pre><p>")
    elif run_url:
        where = f'it is attached to <a href="{e(run_url)}">the GitHub Actions run</a> that made this page.'
    return f"""<div class="admonition warning">
<p><strong>Preview only: this is not the full result.</strong> It is a reduced, one-file copy of the site for
<strong>{e(ename)}</strong>, built from the run output; nothing has been published to Figshare.</p>
<p>Left out of this page:</p>
<ul>
<li>the rendered notebooks;</li>
<li>working links to other pages (the run summary's <em>Figures</em> links jump to the sections below; other links do nothing);</li>
<li>search, menus and everything else that needs JavaScript;</li>
<li>full-resolution figures (they are shrunk to {max_width} px wide and compressed).</li>
</ul>
<p><strong>For the full version</strong>, with rendered notebooks and full-resolution figures, {where or "build the site with mkfigs-preview."}</p>
{f"<p>{e(note)}</p>" if note else ""}
</div>"""


def build(site: Path, ename: str, max_width: int = 1400, quality: int = 80, note: str = "",
          run_url: str = "", artifact: str = "") -> str:
    pages = sorted(p for p in site.glob(f"**/experiments/{ename}/*/index.html") if p.parent.name != "notebooks")
    if not pages:
        sys.exit(f"no pages for experiment {ename} under {site}")
    first = pages[0].read_text()
    body_tag = _BODY_RE.search(first).group(0)
    header = _HEADER_RE.search(first)
    header = tidy(header.group(0), "hdr") if header else ""
    header = inline_images(header, pages[0].parent, 200, quality, site)
    css = inline_css(first, pages[0].parent, site)

    toc, sections = [], []
    home = site / "index.html"
    m = _ARTICLE_RE.search(home.read_text()) if home.is_file() else None
    if m:
        toc.append('<li class="md-nav__item"><a class="md-nav__link" href="#nb-home">Home</a></li>')
        sections.append(f'<section id="nb-home">{inline_images(tidy(m.group(1), "home", ename), site, max_width, quality, site)}</section><hr>')
    for page in pages:
        name = page.parent.name
        m = _ARTICLE_RE.search(page.read_text())
        if not m:
            continue
        content = inline_images(tidy(m.group(1), name), page.parent, max_width, quality, site)
        title = re.search(r"<h1[^>]*>(.*?)</h1>", content, re.S)
        label = re.sub("<[^>]+>", "", title.group(1)) if title else name
        n_figs = content.count("<img")
        toc.append(f'<li class="md-nav__item"><a class="md-nav__link" href="#nb-{name}">'
                   f'{label} <small>({n_figs})</small></a></li>')
        sections.append(f'<section id="nb-{name}">{content}</section><hr>')

    banner = _banner(ename, note, run_url, artifact, max_width)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Preview: {html.escape(ename)}</title>
<style>{css}
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }}
.md-sidebar--primary {{ display: block !important; }}
.md-sidebar--primary .md-nav__link {{ font-size: .7rem; }}
.md-sidebar--primary .md-nav__link small {{ color: var(--md-default-fg-color--light); margin-left: .2rem; }}
.md-typeset img {{ max-width: 100%; height: auto; }}
section {{ scroll-margin-top: 3.5rem; }}
</style></head>
{body_tag}
<div class="md-container">
{header}
<main class="md-main"><div class="md-main__inner md-grid">
  <div class="md-sidebar md-sidebar--primary"><div class="md-sidebar__scrollwrap"><div class="md-sidebar__inner">
    <nav class="md-nav md-nav--primary"><label class="md-nav__title">{html.escape(ename)}</label>
      <ul class="md-nav__list">{''.join(toc)}</ul></nav>
  </div></div></div>
  <div class="md-content"><article class="md-content__inner md-typeset">
    {banner}
    {''.join(sections)}
  </article></div>
</div></main>
</div>
</body></html>
"""


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("site", type=Path, help="a built mkdocs site directory")
    ap.add_argument("ename", help="experiment name")
    ap.add_argument("out", type=Path, help="HTML file to write")
    ap.add_argument("--max-width", type=int, default=1400, help="shrink wider figures to this many pixels (default 1400)")
    ap.add_argument("--quality", type=int, default=80, help="WebP quality, 1-100 (default 80)")
    ap.add_argument("--note", default="", help="extra text for the banner")
    ap.add_argument("--run-url", default="", help="the GitHub Actions run the full site is attached to")
    ap.add_argument("--artifact", default="", help="name of that run's artifact holding the full site")
    a = ap.parse_args(argv)
    a.out.write_text(build(a.site, a.ename, a.max_width, a.quality, a.note, a.run_url, a.artifact))
    print(f"wrote {a.out} ({a.out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
