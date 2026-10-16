# Copyright 2024 ACCESS-NRI and contributors. See the top-level COPYRIGHT file for details.
# SPDX-License-Identifier: Apache-2.0

"""
mkfigs — evaluation figure workflow tools for ACCESS model paper repositories.

Public API for use in analysis notebooks:

    from mkfigs import MkmdWriter
    mkmd = MkmdWriter(esm_file, nbname, str(cwd), pm=papermill)
"""

__author__ = "ACCESS-NRI and contributors"
__email__ = "chris.bull@anu.edu.au"
__version__ = "0.1.0"

from .configdoc import (  # noqa: F401
    MkmdWriter,
    FigshareUploader,
    figshare_upload_and_rewrite,
    get_notebook_authors,
)


def get_mkfigs_version() -> str:
    """Version string for the run-history table and logs.

    Must describe the code that is *actually imported*, not whatever
    ``access-model-mkfigs`` distribution happens to be installed. mkfigs.sh
    runs this package off PYTHONPATH from the external/access-model-mkfigs
    submodule, but if a copy is also pip-installed (e.g. in ~/.local or the
    analysis env) importlib.metadata reports *that* copy's static "0.1.0",
    which says nothing about the commit that ran.

    Order:
      1. ``git describe`` of this file's checkout -> tag (e.g. "v0.2.0"), or
         "v0.2.0-3-gabc1234", or "git+abc1234" when no tags exist; "-dirty"
         appended if uncommitted changes are present.
      2. Installed metadata, but only if that distribution owns this file;
         a VCS install's commit is appended ("0.1.0+abc1234").
      3. "unknown".
    """
    import json
    import subprocess
    from importlib.metadata import PackageNotFoundError, distribution
    from pathlib import Path

    pkg_dir = Path(__file__).resolve().parent

    try:
        res = subprocess.run(
            ["git", "-C", str(pkg_dir), "describe", "--tags", "--always", "--dirty", "--abbrev=7"],
            capture_output=True, text=True, timeout=5,
        )
        desc = res.stdout.strip()
        if res.returncode == 0 and desc:
            # --always with no tags yields a bare hash; make that explicit.
            if all(c in "0123456789abcdef" for c in desc.removesuffix("-dirty")):
                desc = f"git+{desc}"
            return desc
    except Exception:
        pass

    try:
        dist = distribution("access-model-mkfigs")
        owned = {Path(dist.locate_file(f)).resolve() for f in (dist.files or [])}
        if Path(__file__).resolve() in owned:
            ver = dist.version
            commit = ""
            try:
                direct = json.loads(dist.read_text("direct_url.json") or "{}")
                commit = direct.get("vcs_info", {}).get("commit_id", "")[:7]
            except Exception:
                pass
            return f"{ver}+{commit}" if commit else ver
    except PackageNotFoundError:
        pass
    except Exception:
        pass

    return "unknown"
