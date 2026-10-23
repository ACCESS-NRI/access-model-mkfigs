# Copyright 2024 ACCESS-NRI and contributors. See the top-level COPYRIGHT file for details.
# SPDX-License-Identifier: Apache-2.0

"""mkfigs-preview: render a paper repo's website from an mkfigs run (finished or still running), without Figshare.

    mkfigs-preview [--ename ENAME] [--serve] [--preview-dir DIR] [REPO]        (REPO defaults to the current directory)

Copies REPO's documentation/ (uncommitted edits included) and the run's mkfigs_output_<ENAME>/ into a preview folder (default:
<REPO>-preview, next to the repo), runs `pushit --skip-figshare` there, puts the figures and notebooks where a Figshare upload
would have put them, and builds the site. With --serve it serves the site instead and re-renders whenever you edit the repo's docs or the run output.
Nothing is written to REPO. Needs mkdocs and the docs plugins in the current environment (see documentation/requirements.txt).
"""
import argparse
import contextlib
import functools
import http.server
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

# pushit must run in a fresh interpreter (it reads the paths it works on at import time); skip its NCI-only environment check.
PUSHIT = ("import sys, mkfigs.pushit as p; p._check_nci_environment = lambda: None; "
          "sys.argv = ['pushit', '--skip-figshare', '--ename', sys.argv[1]]; p.main()")


def newer(src, dst):
    """copytree copy function: only copy files that changed (keeps repeated refreshes cheap)."""
    if not os.path.exists(dst) or os.path.getmtime(src) > os.path.getmtime(dst):
        shutil.copy2(src, dst)


def locate(repo, ename=None):
    """(notebooks dir, experiment name) for a CM3 (notebooks/polished-python/) or OM3 (notebooks/) style repo."""
    sh = [*repo.glob("notebooks/mkfigs.sh"), *repo.glob("notebooks/*/mkfigs.sh")]
    if len(sh) != 1:
        sys.exit(f"expected exactly one notebooks/**/mkfigs.sh in {repo}, found {len(sh)}")
    if not ename:  # same rule as the shell: the last ENAME= line wins
        found = re.findall(r"^[ \t]*ENAME=([^\s#]+)", sh[0].read_text(), re.M)
        ename = found[-1].strip("\"'") if found else ""
    if not ename or "$" in ename:
        sys.exit("could not read a literal ENAME= from mkfigs.sh: pass --ename")
    if not (sh[0].parent / f"mkfigs_output_{ename}").is_dir():
        have = sorted(p.name[len("mkfigs_output_"):] for p in sh[0].parent.glob("mkfigs_output_*"))
        sys.exit(f"no mkfigs_output_{ename}/ in {sh[0].parent} (found: {have or 'none'}); use --ename")
    return sh[0].parent, ename


def sync(repo, preview_dir):
    """Copy the repo's docs that changed into the preview folder."""
    shutil.copytree(repo / "documentation", preview_dir / "documentation", dirs_exist_ok=True, copy_function=newer,
                    ignore=shutil.ignore_patterns(".cache", "site", "__pycache__"))


def refresh(repo, nbdir, ename, preview_dir):
    """Bring the preview copy up to date with the repo's docs and the run's output, then run pushit there."""
    out = nbdir / f"mkfigs_output_{ename}"
    docs, pdocs, pnb = repo / "documentation", preview_dir / "documentation", preview_dir / nbdir.relative_to(repo)
    sync(repo, preview_dir)
    # pushit reads CITATION.cff from the paper repo's root: copy it so the preview keeps the same author attribution
    citation = repo / "CITATION.cff"
    if citation.is_file():
        shutil.copy2(citation, preview_dir / "CITATION.cff")
    else:
        (preview_dir / "CITATION.cff").unlink(missing_ok=True)
    exp = f"docs/pages/experiments/{ename}"
    for rel in ("mkdocs.yml", "docs/pages/index.md", f"{exp}/notebooks_urls.json"):  # pushit rewrites these: start from the repo's
        if (docs / rel).exists():
            shutil.copy2(docs / rel, pdocs / rel)
        else:
            (pdocs / rel).unlink(missing_ok=True)
    pnb.mkdir(parents=True, exist_ok=True)
    for f in [nbdir / "mkfigs.sh", *nbdir.glob("*.py")]:
        shutil.copy2(f, pnb)
    link = pnb / out.name
    if not (link.is_symlink() or link.exists()):
        try:
            link.symlink_to(out)
        except OSError:  # e.g. VirtualBox shared folders forbid symlinks: copy instead
            pass
    if not link.is_symlink():
        shutil.copytree(out, link, dirs_exist_ok=True, copy_function=newer)
    if not (preview_dir / ".git").exists():  # some docs plugins (git-revision-date) insist on a git repo with a commit
        subprocess.run("git init -q && git -c user.name=x -c user.email=x@x commit -q --allow-empty -m x",
                       shell=True, cwd=preview_dir, check=True)
    pinned = repo / "external/access-model-mkfigs/src"  # the engine the paper repo pins (what production uses), if its submodule is there
    env = dict(os.environ)
    if (pinned / "mkfigs").is_dir():
        env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(pinned), env.get("PYTHONPATH")]))
    print(f"pushit from: {pinned if (pinned / 'mkfigs').is_dir() else 'the installed mkfigs'}")
    r = subprocess.run([sys.executable, "-c", PUSHIT, ename], cwd=pnb, env=env, capture_output=True, text=True)
    (preview_dir / "pushit.log").write_text(r.stdout + r.stderr)
    if r.returncode:
        sys.exit(f"pushit failed (log: {preview_dir / 'pushit.log'}):\n{r.stderr[-800:]}")
    print(*re.findall(r"^ +OK: .*$", r.stdout, re.M)[:1])
    (pdocs / "docs/assets/experiments" / ename).mkdir(parents=True, exist_ok=True)
    (pdocs / exp / "notebooks").mkdir(parents=True, exist_ok=True)
    for png in (out / "mkmd").glob("*.png"):
        newer(png, pdocs / "docs/assets/experiments" / ename / png.name)
    for nb in out.glob("*_rendered.ipynb"):
        name = nb.name.removesuffix("_rendered.ipynb")
        if (pdocs / exp / f"{name}.md").exists():  # only notebooks pushit accepted
            newer(nb, pdocs / exp / "notebooks" / f"{name}.ipynb")


def build(preview_dir, dirty=False):
    """Build the site in this process: repeated builds skip mkdocs' slow start-up, and we avoid the `mkdocs` command (OM3's
    docs requirements replace it with a wrapper that needs `gh`)."""
    try:
        from mkdocs.commands.build import build as mkdocs_build
        from mkdocs.config import load_config
    except ImportError:
        sys.exit("mkdocs is not installed in this environment: pip install -r documentation/requirements.txt")
    os.chdir(preview_dir)  # the notebook plugin writes a ./.cache: keep it out of the user's directory (and out of their repo)
    log = preview_dir / "mkdocs.log"  # the (many, expected) warnings go to a file: links to notebook pages that Figshare would supply
    with open(log, "w") as f, contextlib.redirect_stderr(f):  # redirecting stderr loses none; a logging handler loses most
        logging.getLogger("mkdocs").setLevel(logging.WARNING)
        mkdocs_build(load_config(config_file=str(preview_dir / "documentation/mkdocs.yml"), site_dir=str(preview_dir / "site")), dirty=dirty)
    print(f"built; {log.read_text().count('WARNING')} mkdocs warnings in {log}")


def stamp(*dirs):
    """Latest modification time under dirs: how --serve notices an edit."""
    return max((f.stat().st_mtime for d in dirs for f in d.rglob("*") if f.is_file() and ".cache" not in f.parts), default=0)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("repo", nargs="?", default=".")
    ap.add_argument("--ename", help="experiment (default: the last ENAME= in mkfigs.sh)")
    ap.add_argument("--serve", action="store_true", help="serve the site and re-render on every edit")
    ap.add_argument("--port", type=int, default=8000, help="with --serve (default 8000)")
    ap.add_argument("--preview-dir", help="work folder (default: <repo>-preview; kept between runs so re-runs are fast)")
    a = ap.parse_args()
    repo = Path(a.repo).resolve()
    nbdir, ename = locate(repo, a.ename)
    preview_dir = Path(a.preview_dir or f"{repo}-preview").resolve()
    # it is written to (and its git repo created) freely: never let it be, contain or sit inside the paper repo
    if preview_dir == repo or repo in preview_dir.parents or preview_dir in repo.parents:
        ap.error(f"--preview-dir must be outside the paper repo {repo} (and must not contain it)")
    refresh(repo, nbdir, ename, preview_dir)
    build(preview_dir)
    if not a.serve:
        return print(f"site in {preview_dir / 'site'}   (view it: cd there && python3 -m http.server, or use --serve)")
    # No `mkdocs serve`: its file watcher does not fire on every filesystem. Rebuild ourselves and serve with the stdlib.
    quiet = type("Quiet", (http.server.SimpleHTTPRequestHandler,), {"log_message": lambda *args: None})
    server = http.server.ThreadingHTTPServer(("127.0.0.1", a.port), functools.partial(quiet, directory=str(preview_dir / "site")))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"serving http://127.0.0.1:{a.port}/ : edit the repo's docs, then reload the page (Ctrl-C to stop)")
    docs, out = repo / "documentation", nbdir / f"mkfigs_output_{ename}"
    generated = [docs / "mkdocs.yml", docs / "docs/pages/index.md", repo / "CITATION.cff"]  # pushit rewrites/reads these

    def key():
        return (stamp(out), *(g.stat().st_mtime if g.exists() else 0 for g in generated))

    last_key, last_docs = key(), stamp(docs)
    try:
        while True:
            time.sleep(1)
            try:
                if key() != last_key:  # run output or a pushit-generated file changed: redo everything
                    last_key, last_docs = key(), stamp(docs)
                    refresh(repo, nbdir, ename, preview_dir)
                    build(preview_dir)
                elif stamp(docs) != last_docs:  # any other docs edit: copy it over and rebuild just what changed
                    last_docs = stamp(docs)
                    sync(repo, preview_dir)
                    build(preview_dir, dirty=True)
            except (Exception, SystemExit) as e:  # a bad edit must not stop the server: fix it and save again
                print(f"rebuild failed: {e}")
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
