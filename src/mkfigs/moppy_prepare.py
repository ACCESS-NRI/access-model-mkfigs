# Copyright 2024 ACCESS-NRI and contributors. See the top-level COPYRIGHT file for details.
# SPDX-License-Identifier: Apache-2.0

"""``moppy-esmval-prepare`` for models ACCESS-MOPPy's ESMValTool integration doesn't support yet (e.g. ACCESS-OM3).

Usage (same arguments as ``moppy-esmval-prepare``, plus the mkfigs options below)::

    python -m mkfigs.moppy_prepare RECIPE --input-root ROOT --cache-dir CACHE --output-config CFG \\
        --model-id ACCESS-OM3 --pattern 'Omon.mlotst:output[0-9]*/access-om3.mom6.2d.mlotst.1mon.mean.*.nc' \\
        --supergrid /g/data/vk83/.../ocean_hgrid.nc --user-config moppy_user.yml

ACCESS-MOPPy 1.9 (conda/analysis3-26.11) assumes ACCESS-ESM1.6 in six places. This module works around each one in
memory, then hands over to MOPPy's own ``moppy-esmval-prepare`` code, so CMORisation, the ESMValCore data config and
the ``<recipe>.moppy-pinned.yml`` copy are exactly what MOPPy produces. Every workaround is a candidate upstream fix;
delete it here once MOPPy has it:

1. Recipe datasets: ``RecipeReader`` keeps only ACCESS-ESM1-5/ACCESS-ESM1-6 and the CLI can't change that
   (``prepare_recipe(allowed_datasets=...)`` can) -> ``--dataset`` (default: ``--model-id``).
2. Source CV: the CMIP6 ``source_id`` CV has no ACCESS-OM3 -> registered from a template entry
   (``--cv-template``, default ACCESS-OM2) with ``--nominal-resolution``. Skipped if the CV already has the model.
3. Ocean supergrid: MOPPy picks its bundled grid by nominal resolution; its "25 km" grid is ACCESS-OM2's
   (1080 rows) but ACCESS-OM3 25 km has 1152 -> ``--supergrid`` (the run's own MOM6 ``ocean_hgrid.nc``).
4. Creator metadata: MOPPy reads ``~/.moppy/user.yml`` and ``input()``-prompts when it is missing, which dies with
   EOFError in a batch job -> ``--user-config`` (created with generic mkfigs values if the file doesn't exist).
5. Version pinning: the ``.moppy-pinned.yml`` copy pins ``version:`` on every CMIP6 entry named ACCESS-ESM1-5/1-6
   (a fixed list), not on the datasets MOPPy CMORised. So a recipe comparing with published ACCESS-ESM1-5 data asks
   for that data under today's CMORisation version and finds nothing. Pinning the CMORised datasets instead isn't
   safe either: MOPPy pins one version (the newest) per recipe, and a cache reused across days holds variables
   under different versions -> no pinning; ESMValCore already uses the newest version of each variable.
6. 3D ocean output on z levels: the ACCESS-OM3 mapping knows MOM6's native vertical axis ``zl`` but not ``z_l``/``z_i``
   of the z-remapped ``+z`` diagnostics, so the depth axis was left un-CMORised (``z_l``, units "meters", no
   bounds) and ended up last (``so(time, j, i, z_l)``); ESMValCore rejects it ("olevel: does not exist") ->
   each input file's ``z_l``/``z_i`` are read as ``zl``/``zi``. Still upstream: the files get no ``lev_bnds``
   (ESMValCore accepts that), because the ACCESS-OM3 mapping names the axis ``depth_coord`` (the table key)
   where MOPPy's bounds lookup expects the out_name ``lev`` (as in the ESM1.6 mapping), and ``depth_edges`` is
   empty for ACCESS-OM3. Same for native ``zl`` output.

Must run in a Python that has ``access_moppy`` >= 1.8 (the recipes conda environment; tested with 1.9.0 in
conda/analysis3-26.11). Older MOPPy reads the creator profile when the package is imported, before workaround 4
can redirect it, so it is refused rather than left to hang or die on an ``input()`` prompt.
"""

from __future__ import annotations

import argparse
import copy
import functools
import logging
import sys
from pathlib import Path

log = logging.getLogger("mkfigs.moppy_prepare")

MIN_MOPPY_VERSION = (1, 8)

DEFAULT_CREATOR = {
    "creator_name": "access-model-mkfigs",
    "organisation": "ACCESS-NRI",
    "creator_email": "noreply@example.invalid",  # MOPPy requires a non-empty value
    "creator_url": "https://github.com/ACCESS-NRI/access-model-mkfigs",
}


def build_parser() -> argparse.ArgumentParser:
    """Only the mkfigs options; everything else is passed through to ``moppy-esmval-prepare``."""
    p = argparse.ArgumentParser(
        prog="python -m mkfigs.moppy_prepare",
        description="moppy-esmval-prepare with mkfigs workarounds for non-ESM1.6 models. "
                    "Unrecognised arguments are passed to moppy-esmval-prepare.",
    )
    p.add_argument("--dataset", action="append", default=[], metavar="NAME",
                   help="Recipe dataset name to CMORise (repeatable; default: the --model-id value).")
    p.add_argument("--cv-template", default="ACCESS-OM2", metavar="SOURCE_ID",
                   help="CMIP6 source_id CV entry to copy when --model-id isn't in the CV (default ACCESS-OM2).")
    p.add_argument("--nominal-resolution", default="", metavar="RES",
                   help="Ocean/sea-ice nominal resolution for the registered CV entry, e.g. '25 km'.")
    p.add_argument("--supergrid", default="", metavar="FILE",
                   help="MOM supergrid (ocean_hgrid.nc) to use instead of MOPPy's bundled one.")
    p.add_argument("--user-config", default="", metavar="FILE",
                   help="MOPPy creator profile to use instead of ~/.moppy/user.yml (written if missing).")
    return p


def moppy_version_ok() -> bool:
    """True if the installed ACCESS-MOPPy only reads its creator profile when CMORising (>= 1.8), not on import."""
    from importlib import metadata

    try:
        version = metadata.version("access-moppy")
    except metadata.PackageNotFoundError:
        return False
    parts = []
    for piece in version.split(".")[:2]:
        digits = "".join(ch for ch in piece if ch.isdigit()) or "0"
        parts.append(int(digits))
    return tuple(parts) >= MIN_MOPPY_VERSION


def _model_id(passthrough: list[str]) -> str:
    for i, arg in enumerate(passthrough):
        if arg == "--model-id" and i + 1 < len(passthrough):
            return passthrough[i + 1]
        if arg.startswith("--model-id="):
            return arg.split("=", 1)[1]
    return ""


def allow_datasets(names: frozenset[str]) -> None:
    """Workaround 1: make ``CMORiseOrchestrator.prepare_recipe`` accept *names* when the caller passes nothing."""
    from access_moppy.esmval.orchestrator import CMORiseOrchestrator

    original = CMORiseOrchestrator.prepare_recipe

    @functools.wraps(original)
    def prepare_recipe(self, recipe_path, allowed_datasets=None):
        return original(self, recipe_path, allowed_datasets=allowed_datasets or names)

    CMORiseOrchestrator.prepare_recipe = prepare_recipe

    # Workaround 5: no version pinning at all (MOPPy reads this module global at call time).
    from access_moppy.esmval import cli_commands
    cli_commands._ACCESS_DATASETS = frozenset()


def register_source_id(model_id: str, template: str, nominal_resolution: str = "") -> bool:
    """Workaround 2: add *model_id* to MOPPy's CMIP6 source_id CV, copied from *template*. False if already there."""
    import json

    import access_moppy
    from access_moppy import vocabulary_processors as vp

    cv_file = Path(access_moppy.__file__).parent / "vocabularies" / "CMIP6_CVs" / "CMIP6_source_id.json"
    cv = json.loads(cv_file.read_text())["source_id"]
    if model_id in cv or model_id in vp._CMIP6_TEMP_SOURCE_OVERRIDES:
        return False
    if template not in cv:
        raise SystemExit(f"--cv-template {template!r} is not in MOPPy's CMIP6 source_id CV")
    entry = copy.deepcopy(cv[template])
    entry.update(source_id=model_id, label=model_id,
                 label_extended=f"{model_id} (provisional CV entry added by access-model-mkfigs from {template})")
    # The template's component descriptions name the template model (e.g. "ACCESS-OM2 (MOM5, ... 1deg ...)") and
    # MOPPy copies them into each file's `source` attribute: don't let them describe the wrong model.
    for component, info in entry.get("model_component", {}).items():
        if info.get("description", "none") != "none":
            info["description"] = (f"{model_id} {component} (provisional CV entry from access-model-mkfigs: "
                                   "see the experiment's configuration)")
        if nominal_resolution and component in ("ocean", "ocnBgchem", "seaIce"):
            info["native_nominal_resolution"] = nominal_resolution
    vp._CMIP6_TEMP_SOURCE_OVERRIDES[model_id] = entry
    return True


#: MOM6 z-remapped (``+z``) axis names -> the native names ACCESS-MOPPy's ACCESS-OM3 mapping uses.
Z_LEVEL_ALIASES = {"z_l": "zl", "z_i": "zi"}


def _z_level_preprocess(ds, **kwargs):
    """MOPPy's per-file ``open_mfdataset`` preprocess, with ``z_l``/``z_i`` renamed first (workaround 6).

    Module-level (not a closure) so it stays picklable for Dask workers; it finds MOPPy's original through
    ``access_moppy.base`` either way (patched or not).
    """
    from access_moppy import base

    original = getattr(base, "_mkfigs_original_preprocess", base._preprocess_open_mfdataset)
    rename = {old: new for old, new in Z_LEVEL_ALIASES.items()
              if (old in ds.variables or old in ds.dims) and new not in ds.variables and new not in ds.dims}
    if rename:
        ds = ds.rename(rename)
        edges_attr = ds["zl"].attrs.get("edges") if "zl" in ds.variables else None
        if edges_attr in Z_LEVEL_ALIASES:
            ds["zl"].attrs["edges"] = Z_LEVEL_ALIASES[edges_attr]
    return original(ds, **kwargs)


def support_z_level_output() -> None:
    """Workaround 6: CMORise ACCESS-OM3 3D output written on z levels (``z_l``) like native ``zl`` output."""
    from access_moppy import base

    if not hasattr(base, "_mkfigs_original_preprocess"):
        base._mkfigs_original_preprocess = base._preprocess_open_mfdataset
        base._preprocess_open_mfdataset = _z_level_preprocess


def use_supergrid(path: str) -> None:
    """Workaround 3: always use *path* as the ocean supergrid."""
    from access_moppy import ocean_supergrid

    if not Path(path).is_file():
        raise SystemExit(f"--supergrid file not found: {path}")
    ocean_supergrid.Supergrid.get_supergrid_path = lambda self, nominal_resolution: str(path)


def use_user_config(path: str) -> None:
    """Workaround 4: read MOPPy's creator profile from *path*, writing generic mkfigs values first if it is missing."""
    import yaml
    from access_moppy import _config

    p = Path(path).expanduser()
    if not p.is_file():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(yaml.safe_dump(DEFAULT_CREATOR, sort_keys=False))
        log.info("Wrote MOPPy creator profile %s", p)
    _config.CONFIG_PATH = str(p)


def main(argv: list[str] | None = None) -> int:
    args, passthrough = build_parser().parse_known_args(argv)
    model_id = _model_id(passthrough)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not moppy_version_ok():
        log.error("ACCESS-MOPPy >= %s is required (e.g. module load conda/analysis3-26.11)",
                  ".".join(map(str, MIN_MOPPY_VERSION)))
        return 1

    datasets = frozenset(args.dataset or ([model_id] if model_id else []))
    if datasets:
        allow_datasets(datasets)
        log.info("CMORising recipe datasets: %s", ", ".join(sorted(datasets)))
    if model_id and register_source_id(model_id, args.cv_template, args.nominal_resolution):
        log.info("Registered provisional CMIP6 source_id %s (from %s)", model_id, args.cv_template)
    support_z_level_output()
    if args.supergrid:
        use_supergrid(args.supergrid)
        log.info("Using supergrid %s", args.supergrid)
    if args.user_config:
        use_user_config(args.user_config)

    from access_moppy.esmval.cli_commands import main_prepare

    return main_prepare(passthrough)


if __name__ == "__main__":
    sys.exit(main())
