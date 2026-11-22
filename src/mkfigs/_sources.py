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
    info = {
        "repo": url.rsplit("/", 1)[-1] if url else (Path(top).name if top else ""),
        "url": url,
        "commit": _git(nb_dir, "rev-parse", "HEAD"),
        "path": str(nb_path.resolve().relative_to(Path(top).resolve())) if top else nb_path.name,
    }
    return info


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


def source_link_md(info: dict[str, str]) -> str:
    """Markdown for a Source cell: the repo name, linked to the notebook at the commit that ran."""
    repo = info.get("repo") or "submodule"
    url, commit, path = info.get("url"), info.get("commit"), info.get("path")
    if url and commit and path:
        return f"[`{repo}`@`{commit[:7]}`]({url}/blob/{commit}/{path})"
    if url:
        return f"[`{repo}`]({url})"
    return f"`{repo}`"
