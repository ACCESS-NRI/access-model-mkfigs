# Copyright 2024 ACCESS-NRI and contributors. See the top-level COPYRIGHT file for details.
# SPDX-License-Identifier: Apache-2.0

"""mkfigs-preview-branch: prepare a throwaway `preview/...` branch that ReadTheDocs can render online without Figshare.

    mkfigs-preview-branch --fork git@github.com:YOU/YOUR-FORK.git [--branch preview/NAME] [--ename ENAME] [REPO]

Commits only documentation/ (your uncommitted docs edits plus this run's pages, figures and notebooks) on top of the repo's
committed HEAD, in <repo>-preview-branch/clone, and PRINTS the push command: it never pushes. The commit must already exist in the
fork. NEVER merge such a branch (it holds local figures and notebooks); delete it when you are done.
"""
import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

from mkfigs.preview import locate, refresh


def git(*args, cwd):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"git {args[0]} failed:\n{r.stderr}")
    return r.stdout


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("repo", nargs="?", default=".")
    ap.add_argument("--fork", required=True, help="git URL of YOUR FORK (organisation repos are refused); used for the printed command")
    ap.add_argument("--branch", help="default: preview/<repo>-<ename>")
    ap.add_argument("--ename", help="experiment (default: the last ENAME= in mkfigs.sh)")
    a = ap.parse_args()
    if re.search(r"ACCESS-(Community-Hub|NRI)/", a.fork, re.I):
        sys.exit("refusing to prepare a big, never-to-be-merged branch for an organisation repo: use your own fork")
    repo = Path(a.repo).resolve()
    nbdir, ename = locate(repo, a.ename)
    branch = a.branch or f"preview/{repo.name}-{re.sub(r'[^A-Za-z0-9._-]', '-', ename)}"
    if not branch.startswith("preview/"):
        sys.exit("branch name must start with preview/")
    work = Path(f"{repo}-preview-branch")
    shutil.rmtree(work, ignore_errors=True)
    scratch, clone = work / "scratch", work / "clone"
    refresh(repo, nbdir, ename, scratch)
    git("clone", "-q", "--depth", "1", f"file://{repo}", str(clone), cwd=work)  # the committed HEAD: .readthedocs.yaml, real manifests
    git("checkout", "-q", "-b", branch, cwd=clone)
    shutil.copytree(scratch / "documentation", clone / "documentation", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("notebooks_urls.json", ".cache", "site", "__pycache__"))  # keep the real manifests
    for md in (clone / "documentation/docs/pages/experiments" / ename).glob("*.md"):
        # ReadTheDocs serves under /<version>/, where site-absolute /assets/... links 404: make them source-relative
        md.write_text(md.read_text().replace("(/assets/experiments/", "(../../../assets/experiments/"))
    (clone / "PREVIEW-ONLY-DO-NOT-MERGE.md").write_text("# PREVIEW ONLY: DO NOT MERGE\n\nLocal figures and notebooks. Delete this branch.\n")
    git("add", "-f", "PREVIEW-ONLY-DO-NOT-MERGE.md", "documentation", cwd=clone)
    sizes = {f: (clone / f).stat().st_size for f in git("diff", "--cached", "--name-only", cwd=clone).split()}
    if max(sizes.values()) > 50e6:
        sys.exit(f"refusing: {max(sizes, key=sizes.get)} is over 50 MB (GitHub blocks files over 100 MB)")
    git("commit", "-q", "-m", f"PREVIEW ONLY (do not merge): {ename}", cwd=clone)
    print(f"committed {len(sizes)} files ({sum(sizes.values()) / 1e6:.1f} MB) on {branch} in {clone}; nothing was pushed.")
    print(f"push it to your fork:   git -C {clone} push --force {a.fork} {branch}:{branch}")
    print(f"delete it afterwards:   git push {a.fork} --delete {branch}")


if __name__ == "__main__":
    main()
