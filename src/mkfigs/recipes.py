# Copyright 2024 ACCESS-NRI and contributors. See the top-level COPYRIGHT file for details.
# SPDX-License-Identifier: Apache-2.0

"""ESMValTool recipe support for mkfigs (DRAFT: not yet run against real ESMValTool output).

A *recipe* is an ESMValTool recipe YAML that mkfigs runs instead of a notebook.
For each recipe stem ``<stem>`` the runner:

1. copies ``<recipes dir>/<stem>.yml`` into ``mkfigs_output_<ename>/recipes/``;
2. optionally CMORises ACCESS output on the fly with ACCESS-MOPPy's ``moppy-esmval-prepare``, run through
   :mod:`mkfigs.moppy_prepare` so non-ESM1.6 models such as ACCESS-OM3 work (only when an input root is given);
3. runs ``esmvaltool run`` into ``mkfigs_output_<ename>/esmvaltool/<stem>/``;
4. copies every plot into ``mkmd/<stem>_NN.png`` and writes ``mkmd/<stem>.md``
   (same shape MkmdWriter produces for notebooks, so pushit's Figshare upload,
   link rewriting and landing-page table work unchanged);
5. copies ESMValTool's resolved ``run/<recipe>_filled.yml`` next to them and writes a small
   ``<stem>_recipe_run.json`` marker. The marker plays the role that
   ``<nb>_rendered.ipynb`` plays for notebooks: pushit uses it to tell OK / FAILED /
   NOT RUN apart, and the recipe page shows its tool versions.

pushit later turns the marker + filled recipe into a ``recipes/<stem>.md`` page that embeds the recipe.

ESMValTool's output layout, checked against a real ESMValTool 2.15 / ESMValCore 2.15 run on Gadi (2026-11-11):
``<recipe file stem>_<YYYYMMDD_HHMMSS>/`` (UTC timestamp) containing ``plots/<diag>/<script>/png/*.png`` and
``run/<recipe file stem>_filled.yml``. Upstream recipes are named ``recipe_<x>.yml``, hence ``recipe_<x>_<ts>/``.
"""

from __future__ import annotations

import json
import importlib.util
import os
import logging
import re
import shlex
import shutil
import signal
import subprocess
import sys
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path

from .configdoc import _mkmd_notebook

log = logging.getLogger(__name__)

RUN_MARKER_SUFFIX = "_recipe_run.json"
FILLED_SUFFIX = "_recipe_filled.yml"
DEFAULT_MOPPY_CACHE = "~/.cache/moppy-esmval"
TOOL_DISTRIBUTIONS = ("ESMValTool", "ESMValCore", "access-moppy")
# ESMValCore 2.14+ reads every YAML in ESMVALTOOL_CONFIG_DIR (default ~/.config/esmvaltool). mkfigs keeps a per-output
# config directory instead, so moppy-esmval-prepare's data-source file never lands in $HOME and runs stay reproducible.
CONFIG_SUBDIR = "esmvaltool_config"
MOPPY_CONFIG_NAME = "moppy-esmval-data.yml"
# MOPPy creator profile, kept in the output folder (not $HOME, and not the ESMValTool config dir, which must only
# hold ESMValCore config files).
MOPPY_USER_CONFIG_NAME = "moppy_user.yml"
# ESMValCore ships ready-made config files (data sources per HPC site, e.g. ``data-hpc-nci`` for Gadi's CMIP and
# observation replicas). Recipes that compare against observations or other models need one of them.
PRESETS_ENV = "MKFIGS_ESMVALTOOL_PRESETS"


def marker_path(ofol: Path | str, stem: str) -> Path:
    return Path(ofol) / f"{stem}{RUN_MARKER_SUFFIX}"


def filled_recipe_path(ofol: Path | str, stem: str) -> Path:
    return Path(ofol) / f"{stem}{FILLED_SUFFIX}"


def read_marker(ofol: Path | str, stem: str) -> dict | None:
    """Return the run marker for *stem*, or None if the recipe was never run (or the marker is unreadable)."""
    path = marker_path(ofol, stem)
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def recipe_stems_in(ofol: Path | str) -> list[str]:
    """Stems of every recipe that has a run marker in *ofol*."""
    ofol = Path(ofol)
    if not ofol.is_dir():
        return []
    return sorted(p.name[: -len(RUN_MARKER_SUFFIX)] for p in ofol.glob(f"*{RUN_MARKER_SUFFIX}"))


_RUN_DIR_RE = re.compile(r"_\d{8}_\d{6}$")


def find_run_dir(output_dir: Path) -> Path | None:
    """Newest ESMValTool run directory (``<recipe stem>_<YYYYMMDD_HHMMSS>``) under *output_dir*.

    *output_dir* is per recipe, so all runs share the ``<recipe stem>_`` prefix and the timestamp suffix
    makes the lexical order chronological.
    """
    runs = sorted(
        p for p in Path(output_dir).iterdir()
        if p.is_dir() and _RUN_DIR_RE.search(p.name) and (p / "plots").is_dir()
    ) if Path(output_dir).is_dir() else []
    return runs[-1] if runs else None


def find_filled_recipe(run_dir: Path, recipe_stem: str) -> Path | None:
    """ESMValTool's resolved recipe, ``run/<recipe stem>_filled.yml`` (any ``*_filled.yml`` as a fallback)."""
    exact = Path(run_dir) / "run" / f"{recipe_stem}_filled.yml"
    if exact.is_file():
        return exact
    others = sorted((Path(run_dir) / "run").glob("*_filled.yml"))
    return others[0] if others else None


# mkfigs.moppy_prepare options settable from mkfigs.sh: env var -> option (MKFIGS_MOPPY_DATASETS is ':'-separated).
MOPPY_ENV_OPTIONS = {
    "MKFIGS_MOPPY_SUPERGRID": "--supergrid",
    "MKFIGS_MOPPY_NOMINAL_RES": "--nominal-resolution",
    "MKFIGS_MOPPY_WORKERS": "--workers",
    "MKFIGS_MOPPY_USER_CONFIG": "--user-config",
}


def moppy_args_from_env(environ=os.environ) -> list[str]:
    """Extra :mod:`mkfigs.moppy_prepare` options from the ``MKFIGS_MOPPY_*`` environment variables that are set."""
    args: list[str] = []
    for var, opt in MOPPY_ENV_OPTIONS.items():
        if environ.get(var, "").strip():
            args += [opt, environ[var].strip()]
    for name in filter(None, environ.get("MKFIGS_MOPPY_DATASETS", "").split(":")):
        args += ["--dataset", name.strip()]
    return args

def _reset_previous_outputs(mkmd: Path, stem: str) -> None:
    """Remove this recipe's markdown and PNGs from an earlier run (exact match, so "MLD" never removes "MLD_max_01.png")."""
    own_png = re.compile(rf"^{re.escape(stem)}_\d+\.png$")
    for path in [mkmd / f"{stem}.md", *(p for p in mkmd.glob("*.png") if own_png.match(p.name))]:
        if path.exists():
            path.unlink()
            log.warning("Removed output from a previous run: %s", path)


def collect_outputs(
    run_dir: Path,
    mkmd: Path,
    stem: str,
    experiment: str,
    repo_root: Path,
    nb_relpath: str | None = None,
) -> int:
    """Copy ESMValTool plots into *mkmd* as ``<stem>_NN.png`` and write ``<stem>.md``. Returns the plot count."""
    plots_root = Path(run_dir) / "plots"
    pngs = sorted(plots_root.rglob("*.png")) if plots_root.is_dir() else []
    mkmd.mkdir(parents=True, exist_ok=True)
    _reset_previous_outputs(mkmd, stem)
    for i, png in enumerate(pngs, start=1):
        fname = f"{stem}_{i:02d}.png"
        shutil.copy2(png, mkmd / fname)
        rel = png.relative_to(plots_root)
        title = f"{rel.parent.as_posix()}/{rel.stem}" if rel.parent != Path(".") else rel.stem
        _mkmd_notebook(
            title=title,
            caption=f"`{stem}.yml`: ESMValTool plot `{rel.as_posix()}`",
            experiment=experiment,
            nb_stem=stem,
            plot_fname=fname,
            mdfol=str(mkmd),
            repo_root=repo_root,
            nb_relpath=nb_relpath,
            kind="recipe",
        )
    return len(pngs)


def tool_versions() -> dict[str, str]:
    """Installed versions of the tools a recipe run depends on (those not installed are left out)."""
    found = {}
    for dist in TOOL_DISTRIBUTIONS:
        try:
            found[dist] = metadata.version(dist)
        except metadata.PackageNotFoundError:
            pass
    return found


def _write_marker(ofol: Path, stem: str, info: dict) -> None:
    info = {**info, "recipe": stem, "finished": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}
    marker_path(ofol, stem).write_text(json.dumps(info, indent=2) + "\n")


def conda_module(environ=os.environ) -> str | None:
    """The conda/analysis3-* module this run used: MKFIGS_CONDA_MODULE (set by mkfigs.sh jobs), else LOADEDMODULES."""
    if environ.get("MKFIGS_CONDA_MODULE"):
        return environ["MKFIGS_CONDA_MODULE"]
    return next((m for m in environ.get("LOADEDMODULES", "").split(":") if m.startswith("conda/")), None)


def _without_option(args: list[str], option: str) -> list[str]:
    out, skip = [], False
    for arg in args:
        if skip:
            skip = False
        elif arg == option:
            skip = True
        elif not arg.startswith(option + "="):
            out.append(arg)
    return out


def reproduce_commands(stem: str, info: dict) -> str | None:
    """Shell commands that remake a recipe's plots outside mkfigs, from the run marker (None for older markers)."""
    rep = info.get("reproduce")
    if not rep:
        return None
    q = shlex.quote
    recipe = rep.get("recipe") or f"notebooks/recipes/{stem}.yml"
    lines = ["# On Gadi, in a clone of this repository made with --recurse-submodules:"]
    if rep.get("conda_module"):
        lines.append(f"module use /g/data/xp65/public/modules && module load {q(rep['conda_module'])}")
    lines += [
        "export PYTHONPATH=$PWD/external/access-model-mkfigs/src",
        f"WORK=/g/data/$PROJECT/$USER/{stem}   # any folder you can write to",
        "mkdir -p $WORK/esmvaltool_config",
    ]
    for name in rep.get("presets") or []:
        fname = name if name.endswith(".yml") else f"{name}.yml"
        lines.append(f"esmvaltool config copy {q(fname)} --target_file $WORK/esmvaltool_config/{q(fname)}   # ESMValCore data preset")
    if rep.get("input_root"):
        cmd = [f"python -m mkfigs.moppy_prepare {q(recipe)}", f"--input-root {q(rep['input_root'])}",
               "--cache-dir $WORK/cmorised", "--output-config $WORK/esmvaltool_config/moppy-esmval-data.yml"]
        if rep.get("model_id"):
            cmd.append(f"--model-id {q(rep['model_id'])}")
        cmd += [f"--pattern {q(pat)}" for pat in rep.get("patterns") or []]
        extra = rep.get("moppy_args") or []
        cmd += [f"{a} {q(b)}" for a, b in zip(extra[::2], extra[1::2])]
        cmd.append("--user-config $WORK/moppy_user.yml")
        lines += ["", "# 1. CMORise the model output the recipe needs (ACCESS-MOPPy)", " \\\n    ".join(cmd)]
        lines += ["", "# 2. Run the recipe (use the .moppy-pinned.yml copy instead if step 1 wrote one next to it)"]
    else:
        lines += ["", "# Run the recipe"]
    esm = f"ESMVALTOOL_CONFIG_DIR=$WORK/esmvaltool_config esmvaltool run {q(recipe)} --output_dir $WORK/output"
    if rep.get("esmvaltool_args"):
        esm += " " + rep["esmvaltool_args"]
    lines += [esm, "", f"# Plots: $WORK/output/{Path(recipe).stem}_<YYYYMMDD_HHMMSS>/plots/"]
    return "\n".join(lines)


def esmvalcore_presets_dir() -> Path | None:
    """ESMValCore's bundled config presets (``esmvalcore/config/configurations``), or None if it isn't installed."""
    spec = importlib.util.find_spec("esmvalcore")
    if spec is None or not spec.origin:
        return None
    return Path(spec.origin).parent / "config" / "configurations"


def presets_from_env(environ=os.environ) -> list[str]:
    """``MKFIGS_ESMVALTOOL_PRESETS`` (names separated by spaces, commas or ':') as a list of preset names."""
    return [n for n in re.split(r"[\s,:]+", environ.get(PRESETS_ENV, "")) if n]


TIMEOUT_ENV = "MKFIGS_RECIPE_TIMEOUT"


def timeout_from_env(environ=os.environ) -> float | None:
    """``MKFIGS_RECIPE_TIMEOUT`` (minutes per ``esmvaltool run``) in seconds; None when unset, empty or 0."""
    raw = environ.get(TIMEOUT_ENV, "").strip()
    if not raw:
        return None
    try:
        minutes = float(raw)
    except ValueError:
        log.warning("Ignoring %s=%r: not a number of minutes", TIMEOUT_ENV, raw)
        return None
    return minutes * 60 if minutes > 0 else None


def run_killable(cmd, cwd=None, env=None, timeout: float | None = None) -> subprocess.CompletedProcess:
    """subprocess.run, but on timeout kill the whole process group (esmvaltool's worker processes too).

    ESMValTool can hang instead of exiting when a diagnostic script crashes, which would hold the job until
    walltime. Returns returncode -SIGKILL and ``timed_out=True`` on timeout.
    """
    proc = subprocess.Popen(cmd, cwd=cwd, env=env, start_new_session=True)
    try:
        rc = proc.wait(timeout=timeout)
        timed_out = False
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        rc = proc.wait()
        timed_out = True
    done = subprocess.CompletedProcess(cmd, rc)
    done.timed_out = timed_out
    return done


def _recipe_relpath(recipes_dir: Path, stem: str, repo_root: Path) -> str:
    """Path of the recipe relative to the repo root (for the git-history author lookup)."""
    try:
        return str((Path(recipes_dir) / f"{stem}.yml").resolve().relative_to(Path(repo_root).resolve()))
    except ValueError:
        return f"notebooks/recipes/{stem}.yml"


def run_recipe(
    stem: str,
    ofol: Path | str,
    recipes_dir: Path | str,
    *,
    experiment: str,
    repo_root: Path | str,
    input_root: str = "",
    cache_dir: str = "",
    esmvaltool_args: str = "",
    model_id: str = "",
    patterns: list[str] | tuple[str, ...] = (),
    moppy_args: list[str] | tuple[str, ...] = (),
    base_config_dir: str = "",
    presets: list[str] | tuple[str, ...] = (),
    presets_dir: Path | str | None = None,
    timeout: float | None = None,
    runner=subprocess.run,
) -> bool:
    """Run one recipe end to end. Returns True if esmvaltool succeeded and produced at least one plot.

    A run marker is always written (also on failure) so pushit can report FAILED rather than NOT RUN.
    ``input_root`` is the ACCESS-MOPPy ``--input-root``; leave it empty to skip CMORisation (data already
    discoverable by ESMValTool). ``model_id`` is MOPPy's ``--model-id`` (e.g. ``ACCESS-OM3``) and ``patterns`` are
    ``COMPOUND_NAME:GLOB`` strings passed as ``--pattern`` (MOPPy's default globs follow the ACCESS-ESM1.6 archive
    layout). ``moppy_args`` are extra :mod:`mkfigs.moppy_prepare` options (``--supergrid``, ``--nominal-resolution``,
    ``--workers``, ``--user-config`` ...); without ``--user-config`` MOPPy's creator profile is ``<ofol>/moppy_user.yml``.
    ESMValTool runs with ``ESMVALTOOL_CONFIG_DIR=<ofol>/esmvaltool_config``, seeded with the ``*.yml`` files
    of ``base_config_dir`` when given and with the ESMValCore config ``presets`` named (e.g. ``data-hpc-nci``, from
    ``presets_dir``, default ESMValCore's own). ``timeout`` (seconds) caps ``esmvaltool run``: the run is killed and
    reported as failed (see :func:`run_killable`). ``runner`` is injectable for tests.
    """
    ofol, recipes_dir, repo_root = Path(ofol), Path(recipes_dir), Path(repo_root)
    mkmd = ofol / "mkmd"
    ofol.mkdir(parents=True, exist_ok=True)
    versions = tool_versions()

    src = recipes_dir / f"{stem}.yml"
    if not src.is_file():
        _write_marker(ofol, stem, {"returncode": 2, "error": f"recipe not found: {src}", "n_plots": 0, "versions": versions})
        return False

    work = ofol / "recipes"
    work.mkdir(exist_ok=True)
    recipe = work / f"{stem}.yml"
    shutil.copy2(src, recipe)

    config_dir = ofol / CONFIG_SUBDIR
    config_dir.mkdir(exist_ok=True)
    # ESMValCore reads every YAML here, so a data-source file left by an earlier run (another cache, another
    # seed directory) would silently add data roots. Start each recipe from an empty config set.
    for stale in config_dir.glob("*.yml"):
        stale.unlink()
    if base_config_dir:
        for cfg in sorted(Path(base_config_dir).expanduser().glob("*.yml")):
            if cfg.name != MOPPY_CONFIG_NAME:
                shutil.copy2(cfg, config_dir / cfg.name)
    for name in presets:
        fname = name if name.endswith(".yml") else f"{name}.yml"
        where = Path(presets_dir) if presets_dir else esmvalcore_presets_dir()
        if where is None or not (where / fname).is_file():
            _write_marker(ofol, stem, {"returncode": 2, "error": f"ESMValCore config preset not found: {fname} (in {where})",
                                       "n_plots": 0, "versions": versions})
            return False
        shutil.copy2(where / fname, config_dir / fname)
    env = {**os.environ, "ESMVALTOOL_CONFIG_DIR": str(config_dir)}

    # A pinned copy left by an earlier run would otherwise be run instead of the current recipe.
    (work / f"{stem}.moppy-pinned.yml").unlink(missing_ok=True)
    if input_root:
        cache = str(Path(cache_dir or DEFAULT_MOPPY_CACHE).expanduser())
        cmd = [sys.executable, "-m", "mkfigs.moppy_prepare", str(recipe), "--input-root", input_root,
               "--cache-dir", cache, "--output-config", str(config_dir / MOPPY_CONFIG_NAME)]
        if model_id:
            cmd += ["--model-id", model_id]
        for pat in patterns:
            cmd += ["--pattern", pat]
        cmd += list(moppy_args)
        if "--user-config" not in moppy_args:
            cmd += ["--user-config", str(ofol / MOPPY_USER_CONFIG_NAME)]
        prep = runner(cmd, cwd=str(ofol), env=env)
        if prep.returncode != 0:
            _write_marker(ofol, stem, {"returncode": prep.returncode, "error": "moppy-esmval-prepare failed",
                                       "n_plots": 0, "versions": versions})
            return False
        # Some ACCESS-MOPPy versions write a pinned copy of the recipe next to the one they were given.
        pinned = work / f"{stem}.moppy-pinned.yml"
        if pinned.is_file():
            recipe = pinned

    out = ofol / "esmvaltool" / stem
    out.mkdir(parents=True, exist_ok=True)
    esmvaltool_cmd = ["esmvaltool", "run", str(recipe), "--output_dir", str(out), *shlex.split(esmvaltool_args)]
    if timeout:
        result = (run_killable if runner is subprocess.run else runner)(
            esmvaltool_cmd, cwd=str(ofol), env=env, timeout=timeout)
    else:
        result = runner(esmvaltool_cmd, cwd=str(ofol), env=env)
    timed_out = bool(getattr(result, "timed_out", False))

    run_dir = find_run_dir(out)
    n_plots = 0
    if run_dir is not None:
        n_plots = collect_outputs(run_dir, mkmd, stem, experiment, repo_root, _recipe_relpath(recipes_dir, stem, repo_root))
        filled = find_filled_recipe(run_dir, Path(recipe).stem)
        shutil.copy2(filled or recipe, filled_recipe_path(ofol, stem))

    info = {
        "returncode": result.returncode,
        "run_dir": str(run_dir) if run_dir else None,
        "n_plots": n_plots,
        "recipe_used": recipe.name,
        "prepared": bool(input_root),
        "model_id": model_id or None,
        "config_dir": str(config_dir),
        "versions": versions,
        "reproduce": {  # what write_recipe_page needs to print the commands that remake these plots
            "recipe": _recipe_relpath(recipes_dir, stem, repo_root),
            "conda_module": conda_module(),
            "input_root": input_root or None,
            "model_id": model_id or None,
            "patterns": list(patterns),
            # the creator profile path is this run's own; the reproduce commands use their own
            "moppy_args": _without_option(list(moppy_args), "--user-config"),
            "esmvaltool_args": esmvaltool_args,
            "presets": list(presets),
        },
    }
    if timed_out:
        info["error"] = (f"esmvaltool run killed after {timeout / 60:g} min ({TIMEOUT_ENV}); it may have hung "
                         "after a diagnostic script failed: see the diagnostic logs in run/")
    elif result.returncode != 0:
        info["error"] = "esmvaltool run failed"
    elif run_dir is None:
        info["error"] = "no ESMValTool run directory with plots/ found"
    elif n_plots == 0:
        info["error"] = "esmvaltool succeeded but produced no PNG plots"
    _write_marker(ofol, stem, info)
    return result.returncode == 0 and n_plots > 0


def extract_recipe_error(ofol: Path | str, stem: str, tail_lines: int = 40) -> str | None:
    """Short failure description for mkfigs_errors.log: the marker's error plus the end of ESMValTool's main log."""
    info = read_marker(ofol, stem)
    if info is None:
        return None
    parts = [str(info.get("error") or f"returncode {info.get('returncode')}")]
    run_dir = info.get("run_dir")
    if run_dir:
        log_file = Path(run_dir) / "run" / "main_log.txt"
        try:
            parts.append("\n".join(log_file.read_text().splitlines()[-tail_lines:]))
        except OSError:
            pass
    return "\n".join(parts)


def write_recipe_page(ofol: Path | str, stem: str, dest_md: Path, ename: str) -> Path:
    """Write ``recipes/<stem>.md``: the recipe that produced the figures, with the tool versions that ran it.

    The body is wrapped in a Jinja raw block because the docs build renders Markdown through mkdocs-macros.
    """
    info = read_marker(ofol, stem) or {}
    filled = filled_recipe_path(ofol, stem)
    body = filled.read_text() if filled.is_file() else "# resolved recipe not found in the run output\n"
    fence = "`" * max(3, max((len(m) for m in re.findall(r"`+", body)), default=0) + 1)
    versions = info.get("versions") or {}
    rows = "".join(f"| {name} | {ver} |\n" for name, ver in sorted(versions.items()))
    lines = [
        "{% raw %}",
        f"# {stem}",
        "",
        f"ESMValTool recipe used to produce the figures for experiment **{ename}**."
        f" [Back to the figures](../{stem}.md).",
        "",
    ]
    if info.get("finished"):
        lines += [f"Run finished: {info['finished']}", ""]
    if rows:
        lines += ["| Tool | Version |", "|---|---|", rows.rstrip("\n"), ""]
    lines += [
        "This is ESMValTool's resolved copy of the recipe (`<recipe>_filled.yml`), i.e. what actually ran.",
        "",
        f"{fence}yaml",
        body.rstrip("\n"),
        fence,
        "",
    ]
    commands = reproduce_commands(stem, info)
    if commands:
        lines += [
            "## Reproduce these plots",
            "",
            "These commands repeat this run by hand: the same recipe, input data and settings, without mkfigs' page building."
            " Run them where the tool versions above are installed; the cached CMORised data can be reused for later runs.",
            "",
            "```bash",
            commands,
            "```",
            "",
            "To remake the whole website entry instead, set `ENAME`/`ESMDIR` in `notebooks/mkfigs.sh` and run "
            f"`qsub -v MKFIGS_KIND=recipes,MKFIGS_CONDA_MODULE={info['reproduce'].get('conda_module') or '<module>'} mkfigs.sh`"
            " from `notebooks/`, then `python3 -m mkfigs.pushit`.",
            "",
        ]
    lines += ["{% endraw %}", ""]
    dest_md.parent.mkdir(parents=True, exist_ok=True)
    dest_md.write_text("\n".join(lines))
    return dest_md
