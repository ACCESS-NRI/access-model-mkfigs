# Copyright 2024 ACCESS-NRI and contributors. See the top-level COPYRIGHT file for details.
# SPDX-License-Identifier: Apache-2.0

"""Notebooks that live outside the mkfigs.sh directory, e.g. in a git submodule.

An entry in the mkfigs.sh ``array`` is normally a notebook name in the same
directory as mkfigs.sh (``SST``). An entry that contains a ``/`` is instead a
path relative to the paper repo's root, without ``.ipynb``, e.g.

    external/access-om3-paper-1/notebooks/SST

which lets access-cm3-paper-1 run access-om3-paper-1's notebooks (included as a
git submodule) against a CM3 experiment. The notebook is run from its own
directory, so it can import the helpers that sit next to it, and its outputs go
to the same mkfigs_output_<ENAME>/ as every other notebook, under its stem
("SST"). Stems must therefore be unique across the whole array.

run.py records where each such notebook came from (repository, commit, path) in
``mkmd/sources/<stem>.json``, one file per notebook so that notebooks run as
separate PBS jobs (mkfigs.submit) never write the same file. pushit reads these
to show a "Source" column on the landing page.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path, PurePosixPath


def notebook_stem(entry: str) -> str:
    """'external/x/notebooks/SST' -> 'SST'; 'SST' -> 'SST'."""
    return PurePosixPath(entry).name


def is_external(entry: str) -> bool:
    """True for an entry given as a path from the repo root rather than a bare name."""
    return "/" in entry


def notebook_path(entry: str, notebooks_dir: Path, repo_root: Path) -> Path:
    """The .ipynb file an array entry refers to."""
    base = repo_root if is_external(entry) else notebooks_dir
    return base / f"{entry}.ipynb"


def check_unique_stems(entries: list[str]) -> None:
    """Raise ValueError if two entries would write the same outputs (same stem)."""
    seen: dict[str, str] = {}
    clashes = []
    for e in entries:
        stem = notebook_stem(e)
        if stem in seen and seen[stem] != e:
            clashes.append(f"{seen[stem]!r} and {e!r}")
        seen.setdefault(stem, e)
    if clashes:
        raise ValueError(
            "notebooks with the same name would overwrite each other's outputs: "
            + "; ".join(clashes)
            + ". Run only one of each pair."
        )


def _git(cwd: Path, *args: str) -> str:
    try:
        r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=10)
    except Exception:
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def _https_url(remote: str) -> str:
    """git@github.com:owner/repo.git or https://github.com/owner/repo.git -> https://github.com/owner/repo"""
    m = re.match(r"^git@([^:]+):(.+?)(?:\.git)?/?$", remote)
    if m:
        return f"https://{m.group(1)}/{m.group(2)}"
    return re.sub(r"\.git/?$", "", remote)


def describe_source(nb_path: Path) -> dict[str, str]:
    """Repository, URL, commit and in-repo path of a notebook file (best effort; '' where unknown)."""
    nb_dir = nb_path.parent
    top = _git(nb_dir, "rev-parse", "--show-toplevel")
    url = _https_url(_git(nb_dir, "config", "--get", "remote.origin.url"))
    path = str(nb_path.resolve().relative_to(Path(top).resolve())) if top else nb_path.name
    superproject = _git(nb_dir, "rev-parse", "--show-superproject-working-tree")
    info = {
        "repo": url.rsplit("/", 1)[-1] if url else (Path(top).name if top else ""),
        "url": url,
        "commit": _git(nb_dir, "rev-parse", "HEAD"),
        "path": path,
        # Where the submodule sits in the paper repo, e.g. external/access-om3-paper-1.
        "submodule": (str(Path(top).resolve().relative_to(Path(superproject).resolve()))
                      if top and superproject else ""),
        # Recorded here because pushit's own author lookup searches the paper
        # repo's history, where this notebook has none.
        "authors": notebook_authors(Path(top), path) if top else [],
    }
    return info


def notebook_authors(repo_top: Path, relpath: str) -> list[str]:
    """Authors of a notebook from its repo's git history: creator first, then by number of commits.

    Same order as configdoc.get_notebook_authors.
    """
    names = [a for a in _git(repo_top, "log", "--follow", "--format=%an", "--", relpath).splitlines() if a.strip()]
    if not names:
        return []
    first = names[-1]
    counts: dict[str, int] = {}
    for a in names:
        counts[a] = counts.get(a, 0) + 1
    return [first] + sorted((a for a in counts if a != first), key=lambda a: counts[a], reverse=True)


def write_source(mdfol: Path, entry: str, nb_path: Path) -> None:
    """Record mkmd/sources/<stem>.json for an external entry."""
    d = mdfol / "sources"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{notebook_stem(entry)}.json").write_text(json.dumps(describe_source(nb_path), indent=2) + "\n")


def read_sources(mdfol: Path) -> dict[str, dict[str, str]]:
    """{stem: source info} from mkmd/sources/*.json (empty if none)."""
    out = {}
    for f in sorted((mdfol / "sources").glob("*.json")):
        try:
            out[f.stem] = json.loads(f.read_text())
        except Exception:
            pass
    return out


def source_header_md(info: dict, as_run_url: str = "") -> str:
    """Opening paragraph of a submodule notebook's figure page, naming where the notebook came from.

    Replaces the paragraph configdoc writes, which names only this repo and finds
    no authors (it looks in this repo's history, not the submodule's).
    """
    repo = info.get("repo") or "another repository"
    url = info.get("url") or ""
    repo_md = f"[`{repo}`]({url})" if url else f"`{repo}`"
    where = f"`{info['submodule']}`" if info.get("submodule") else "a git submodule"
    links = source_link_md(info) + (f" · [as run]({as_run_url})" if as_run_url else "")
    authors = ", ".join(info.get("authors") or []) or "unknown"
    return (
        f"produced by notebook `{PurePosixPath(info.get('path') or '').name}` from the {repo_md}"
        f" repository of metrics, run from its git submodule at {where} (notebook: {links})."
        f" Co-authors for this notebook (via Git history of `{repo}`): {authors}."
    )


def rewrite_page_header(md_path: Path, info: dict, as_run_url: str = "") -> bool:
    """Swap configdoc's 'produced by notebook ... Co-authors ...: X.' sentence for source_header_md.

    Returns True if the page was changed. Leaves the page alone if the sentence is not found.
    """
    text = md_path.read_text()
    pattern = re.compile(r"produced by notebook `[^`]*`\..*?Co-authors for this notebook \(via Git history[^)]*\): [^\n]*?\.(?= \[View rendered notebook\]|\n|$)")
    new, n = pattern.subn(lambda _m: source_header_md(info, as_run_url), text, count=1)
    if n and new != text:
        md_path.write_text(new)
        return True
    return False


def source_link_md(info: dict[str, str]) -> str:
    """Markdown for a Source cell: the repo name, linked to the notebook at the commit that ran."""
    repo = info.get("repo") or "submodule"
    url, commit, path = info.get("url"), info.get("commit"), info.get("path")
    if url and commit and path:
        return f"[`{repo}`@`{commit[:7]}`]({url}/blob/{commit}/{path})"
    if url:
        return f"[`{repo}`]({url})"
    return f"`{repo}`"
