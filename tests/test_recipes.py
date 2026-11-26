"""ESMValTool recipe support (mkfigs.recipes) and its pushit / run integration.

All ESMValTool and ACCESS-MOPPy calls are replaced by a fake runner that writes the output layout
mkfigs expects from ESMValTool (<recipe stem>_<timestamp>/plots/... and run/<recipe stem>_filled.yml), as seen in a
real ESMValTool 2.15 run on Gadi (2026-11-11).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import yaml

from mkfigs import pushit, recipes, run as run_mod

from .conftest import make_png, make_rendered_notebook

ENAME = "test_experiment_01"


def make_run_dir(out: Path, name: str = "demo", stamp: str = "20261110_100000", plots=None) -> Path:
    """Write a fake ESMValTool run directory under *out* and return it."""
    run_dir = out / f"{name}_{stamp}"
    (run_dir / "plots").mkdir(parents=True, exist_ok=True)
    for rel in plots if plots is not None else ["diagA/taskA/fig1.png", "diagA/taskB/fig2.png"]:
        png = run_dir / "plots" / rel
        png.parent.mkdir(parents=True, exist_ok=True)
        make_png(png)
    (run_dir / "run").mkdir(parents=True, exist_ok=True)
    (run_dir / "run" / f"{name}_filled.yml").write_text("documentation:\n  title: Demo\n")
    return run_dir


PREPARE = [sys.executable, "-m", "mkfigs.moppy_prepare"]


def fake_runner(esmvaltool_rc=0, prepare_rc=0, write_pinned=False, plots=None, calls=None):
    """A stand-in for subprocess.run that fakes mkfigs.moppy_prepare and esmvaltool run."""
    def runner(cmd, cwd=None, **kwargs):
        if calls is not None:
            calls.append(list(cmd))
        if cmd[:3] == PREPARE:
            if write_pinned:
                Path(cmd[3]).with_name(Path(cmd[3]).stem + ".moppy-pinned.yml").write_text("pinned: true\n")
            return SimpleNamespace(returncode=prepare_rc)
        assert cmd[0] == "esmvaltool" and cmd[1] == "run"
        out = Path(cmd[cmd.index("--output_dir") + 1])
        if esmvaltool_rc == 0 or plots is not None:
            make_run_dir(out, plots=plots)
        return SimpleNamespace(returncode=esmvaltool_rc)
    return runner


@pytest.fixture
def recipe_repo(tmp_path):
    """A repo root with notebooks/recipes/demo.yml and an output folder for ENAME."""
    repo = tmp_path / "paper-repo"
    (repo / "notebooks" / "recipes").mkdir(parents=True)
    (repo / "notebooks" / "recipes" / "demo.yml").write_text("documentation:\n  title: Demo\n")
    ofol = repo / "notebooks" / f"mkfigs_output_{ENAME}"
    ofol.mkdir()
    return repo, ofol


# ---------------------------------------------------------------------------
# recipes module
# ---------------------------------------------------------------------------

def test_find_run_dir_returns_newest_and_ignores_dirs_without_plots(tmp_path):
    make_run_dir(tmp_path, stamp="20261109_090000")
    newest = make_run_dir(tmp_path, stamp="20261110_100000")
    (tmp_path / "recipe_demo_20261111_000000").mkdir()  # an aborted run: no plots/
    assert recipes.find_run_dir(tmp_path) == newest
    assert recipes.find_run_dir(tmp_path / "missing") is None


def test_collect_outputs_names_pngs_after_the_stem_and_writes_markdown(tmp_path):
    run_dir = make_run_dir(tmp_path / "esmvaltool")
    mkmd = tmp_path / "mkmd"
    n = recipes.collect_outputs(run_dir, mkmd, "demo", ENAME, tmp_path)
    assert n == 2
    assert sorted(p.name for p in mkmd.glob("*.png")) == ["demo_01.png", "demo_02.png"]
    md = (mkmd / "demo.md").read_text()
    assert "produced by ESMValTool recipe `demo.yml`" in md
    assert "[View recipe](recipes/demo.md)" in md
    assert f"(/assets/experiments/{ENAME}/demo_01.png)" in md
    assert "diagA/taskA/fig1" in md  # title from the plot's path under plots/


def test_collect_outputs_replaces_an_earlier_runs_output(tmp_path):
    mkmd = tmp_path / "mkmd"
    recipes.collect_outputs(make_run_dir(tmp_path / "a"), mkmd, "demo", ENAME, tmp_path)
    make_png(mkmd / "demo_max_01.png")  # another recipe whose stem starts with "demo": must survive
    n = recipes.collect_outputs(make_run_dir(tmp_path / "b", plots=["only.png"]), mkmd, "demo", ENAME, tmp_path)
    assert n == 1
    assert sorted(p.name for p in mkmd.glob("*.png")) == ["demo_01.png", "demo_max_01.png"]
    assert (mkmd / "demo.md").read_text().count("## ") == 1


def test_run_recipe_success_prepares_runs_collects_and_writes_marker(recipe_repo):
    repo, ofol = recipe_repo
    calls: list[list[str]] = []
    ok = recipes.run_recipe(
        "demo", ofol, repo / "notebooks" / "recipes", experiment=ENAME, repo_root=repo,
        input_root="/g/data/x/archive", cache_dir=str(repo / "cache"),
        esmvaltool_args="--max-parallel-tasks 4", runner=fake_runner(calls=calls),
    )
    assert ok is True
    assert calls[0][:3] == PREPARE
    assert calls[0][calls[0].index("--input-root") + 1] == "/g/data/x/archive"
    assert calls[0][calls[0].index("--cache-dir") + 1] == str(repo / "cache")
    assert calls[1][:2] == ["esmvaltool", "run"]
    assert calls[1][-2:] == ["--max-parallel-tasks", "4"]
    marker = json.loads(recipes.marker_path(ofol, "demo").read_text())
    assert marker["returncode"] == 0 and marker["n_plots"] == 2 and marker["prepared"] is True
    assert "error" not in marker
    assert recipes.filled_recipe_path(ofol, "demo").read_text().startswith("documentation:")
    assert (ofol / "mkmd" / "demo_01.png").exists() and (ofol / "mkmd" / "demo.md").exists()
    assert (repo / "notebooks" / "recipes" / "demo.yml").read_text() == "documentation:\n  title: Demo\n"  # source untouched


def test_run_recipe_runs_the_moppy_pinned_copy_when_prepare_writes_one(recipe_repo):
    repo, ofol = recipe_repo
    calls: list[list[str]] = []
    recipes.run_recipe("demo", ofol, repo / "notebooks" / "recipes", experiment=ENAME, repo_root=repo,
                       input_root="/in", cache_dir="/c", runner=fake_runner(write_pinned=True, calls=calls))
    assert calls[1][2].endswith("demo.moppy-pinned.yml")
    assert json.loads(recipes.marker_path(ofol, "demo").read_text())["recipe_used"] == "demo.moppy-pinned.yml"


def test_run_recipe_skips_cmorisation_without_an_input_root(recipe_repo):
    repo, ofol = recipe_repo
    calls: list[list[str]] = []
    assert recipes.run_recipe("demo", ofol, repo / "notebooks" / "recipes", experiment=ENAME, repo_root=repo,
                              runner=fake_runner(calls=calls))
    assert [c[0] for c in calls] == ["esmvaltool"]
    assert json.loads(recipes.marker_path(ofol, "demo").read_text())["prepared"] is False


@pytest.mark.parametrize(
    "runner_kwargs, expected_error",
    [
        ({"prepare_rc": 3}, "moppy-esmval-prepare failed"),
        ({"esmvaltool_rc": 1}, "esmvaltool run failed"),
        ({"plots": []}, "produced no PNG plots"),
    ],
)
def test_run_recipe_failures_still_write_a_marker(recipe_repo, runner_kwargs, expected_error):
    repo, ofol = recipe_repo
    ok = recipes.run_recipe("demo", ofol, repo / "notebooks" / "recipes", experiment=ENAME, repo_root=repo,
                            input_root="/in", cache_dir="/c", runner=fake_runner(**runner_kwargs))
    assert ok is False
    marker = recipes.read_marker(ofol, "demo")
    assert marker is not None and expected_error in marker["error"]
    assert expected_error in recipes.extract_recipe_error(ofol, "demo")


def test_run_recipe_missing_source_is_a_failure_not_a_crash(recipe_repo):
    repo, ofol = recipe_repo
    assert recipes.run_recipe("nope", ofol, repo / "notebooks" / "recipes", experiment=ENAME, repo_root=repo,
                              runner=fake_runner()) is False
    assert "recipe not found" in recipes.read_marker(ofol, "nope")["error"]


def test_write_recipe_page_embeds_the_recipe_safely(tmp_path):
    ofol = tmp_path / f"mkfigs_output_{ENAME}"
    ofol.mkdir()
    recipes.marker_path(ofol, "demo").write_text(json.dumps(
        {"returncode": 0, "finished": "2026-11-10 10:00 UTC", "versions": {"ESMValTool": "2.15.0"}}))
    # a recipe containing a code fence and Jinja-looking text must neither break the page nor be rendered
    recipes.filled_recipe_path(ofol, "demo").write_text("note: |\n  ```\n  {{ not_jinja }}\n  ```\n")
    page = recipes.write_recipe_page(ofol, "demo", tmp_path / "pages" / "recipes" / "demo.md", ENAME)
    text = page.read_text()
    assert text.startswith("{% raw %}") and text.rstrip().endswith("{% endraw %}")
    assert "````yaml" in text and "| ESMValTool | 2.15.0 |" in text
    assert "[Back to the figures](../demo.md)" in text
    body = text.split("````yaml\n")[1].split("\n````")[0]
    assert yaml.safe_load(body)["note"].startswith("```")


# ---------------------------------------------------------------------------
# pushit integration
# ---------------------------------------------------------------------------

def _seed_recipe(ofol: Path, stem="demo", returncode=0, n_png=1):
    mdfol = ofol / "mkmd"
    mdfol.mkdir(parents=True, exist_ok=True)
    recipes.marker_path(ofol, stem).write_text(json.dumps(
        {"returncode": returncode, "n_plots": n_png, "versions": {"ESMValTool": "2.15.0"}}))
    recipes.filled_recipe_path(ofol, stem).write_text("documentation:\n  title: Demo\n")
    for i in range(1, n_png + 1):
        make_png(mdfol / f"{stem}_{i:02d}.png")
    if n_png:
        (mdfol / f"{stem}.md").write_text(f"# {stem}\n\n![fig](/assets/experiments/{ENAME}/{stem}_01.png)\n")


def _set_mkfigs_sh(repo: Path, recipes_array="demo"):
    sh = repo / "notebooks" / "mkfigs.sh"
    sh.write_text(
        "#!/bin/bash\n"
        "# an example, not a real list:\n"
        "#   recipes=( commented_out )\n"
        f"ENAME={ENAME}\n"
        "ESMDIR=/g/data/tm70/fake/${ENAME}/datastore.json\n"
        "array=(\n  SST\n)\n"
        f"recipes=(\n  {recipes_array}  # trailing comment\n)\n"
    )


def _run_pushit(args):
    with patch.object(sys, "argv", ["mkfigs-pushit", *args]), patch.object(pushit.subprocess, "run") as mock_run:
        pushit.main()
    return mock_run


def test_parse_recipes_ignores_commented_examples_and_missing_arrays(patch_repo_paths):
    _set_mkfigs_sh(patch_repo_paths, recipes_array="demo\n  other")
    assert pushit.parse_recipes_from_mkfigs_sh() == ["demo", "other"]
    (patch_repo_paths / "notebooks" / "mkfigs.sh").write_text("ENAME=x\nESMDIR=y\narray=(\n SST\n)\n")
    assert pushit.parse_recipes_from_mkfigs_sh() == []


def test_pushit_publishes_a_recipe_alongside_a_notebook(patch_repo_paths):
    repo = patch_repo_paths
    _set_mkfigs_sh(repo)
    ofol = repo / "notebooks" / f"mkfigs_output_{ENAME}"
    (ofol / "mkmd").mkdir(parents=True)
    make_rendered_notebook(ofol / "SST_rendered.ipynb")
    make_png(ofol / "mkmd" / "SST_01.png")
    (ofol / "mkmd" / "SST.md").write_text("# SST\n")
    _seed_recipe(ofol)

    mock_run = _run_pushit(["--skip-figshare"])

    docs = repo / "documentation" / "docs" / "pages"
    exp = docs / "experiments" / ENAME
    assert (exp / "demo.md").exists() and (exp / "SST.md").exists()
    assert "documentation:" in (exp / "recipes" / "demo.md").read_text()
    assert not (repo / "notebooks" / "demo.ipynb").exists()
    # nbconvert --clear-output ran for the notebook only
    cleared = [c.args[0][-1] for c in mock_run.call_args_list if "--clear-output" in c.args[0]]
    assert cleared == [str(repo / "notebooks" / "SST.ipynb")]

    nav = {next(iter(i)): i[next(iter(i))] for i in yaml.safe_load((repo / "documentation" / "mkdocs.yml").read_text())["nav"][1:]}
    assert "demo" not in nav  # recipes are grouped, after the notebooks
    assert list(nav)[-1] == pushit.RECIPES_CATEGORY
    assert nav[pushit.RECIPES_CATEGORY] == [{"demo": [{"Summary": f"pages/experiments/{ENAME}/demo.md"},
                                                      {"Recipe": f"pages/experiments/{ENAME}/recipes/demo.md"}]}]
    assert nav["SST"][1] == {"Notebook": f"pages/experiments/{ENAME}/notebooks/SST.ipynb"}

    index = (docs / "index.md").read_text()
    assert "| Notebook / recipe |" in index and "Notebooks / recipes run" in index
    assert f"[Recipe](experiments/{ENAME}/recipes/demo.md)" in index
    assert f"[Notebook](experiments/{ENAME}/notebooks/SST/)" in index
    # the recipe has its own section, not the notebooks' table
    recipes_at = index.index(f"### {pushit.RECIPES_CATEGORY}")
    assert index.index("`SST`") < recipes_at < index.index("`demo`")


def test_pushit_reports_failed_and_not_run_recipes(patch_repo_paths, capsys):
    repo = patch_repo_paths
    _set_mkfigs_sh(repo, recipes_array="bad\n  never_ran")
    ofol = repo / "notebooks" / f"mkfigs_output_{ENAME}"
    _seed_recipe(ofol, "bad", returncode=1, n_png=0)
    _run_pushit(["--dry-run"])
    out = capsys.readouterr().out
    assert "FAILED" in out and "bad" in out
    assert "NOT RUN (recipe)" in out and "never_ran" in out


def test_recipe_without_a_new_run_stays_listed_as_previously_committed(patch_repo_paths):
    repo = patch_repo_paths
    _set_mkfigs_sh(repo)
    ofol = repo / "notebooks" / f"mkfigs_output_{ENAME}"
    (ofol / "mkmd").mkdir(parents=True)
    make_rendered_notebook(ofol / "SST_rendered.ipynb")
    make_png(ofol / "mkmd" / "SST_01.png")
    (ofol / "mkmd" / "SST.md").write_text("# SST\n")
    page = repo / "documentation" / "docs" / "pages" / "experiments" / ENAME / "recipes" / "demo.md"
    page.parent.mkdir(parents=True)
    page.write_text("committed earlier\n")

    _run_pushit(["--skip-figshare"])

    index = (repo / "documentation" / "docs" / "pages" / "index.md").read_text()
    assert "| `demo` | ✅ Yes (earlier run) |" in index
    nav = yaml.safe_load((repo / "documentation" / "mkdocs.yml").read_text())["nav"]
    assert {pushit.RECIPES_CATEGORY: [{"demo": [{"Summary": f"pages/experiments/{ENAME}/demo.md"},
                                                {"Recipe": f"pages/experiments/{ENAME}/recipes/demo.md"}]}]} in nav
    assert page.read_text() == "committed earlier\n"


def test_notebook_only_repos_are_unchanged(patch_repo_paths):
    """No recipes array: same table headings and nav as before this feature."""
    repo = patch_repo_paths
    ofol = repo / "notebooks" / f"mkfigs_output_{ENAME}"
    (ofol / "mkmd").mkdir(parents=True)
    make_rendered_notebook(ofol / "SST_rendered.ipynb")
    make_png(ofol / "mkmd" / "SST_01.png")
    (ofol / "mkmd" / "SST.md").write_text("# SST\n")
    _run_pushit(["--skip-figshare"])
    index = (repo / "documentation" / "docs" / "pages" / "index.md").read_text()
    assert "| Notebook |" in index and "Notebook / recipe" not in index and "[Recipe]" not in index


# ---------------------------------------------------------------------------
# run.main
# ---------------------------------------------------------------------------

def _run_main(monkeypatch, wfolder: Path, *extra):
    monkeypatch.setattr("sys.argv", ["mkfigs-run", "--ename", "exp1", "--esmdir", "/fake/esm.json",
                                     "--wfolder", str(wfolder), *extra])
    run_mod.main()


def test_main_kind_recipes_runs_only_recipes_and_logs_failures_separately(tmp_path, monkeypatch):
    wfolder = tmp_path / "paper-repo"
    (wfolder / "notebooks").mkdir(parents=True)
    monkeypatch.setenv("MKFIGS_RECIPES", "good:bad")
    monkeypatch.setenv("MKFIGS_MOPPY_INPUT_ROOT", "/g/data/x/archive")
    monkeypatch.delenv("MKFIGS_NOTEBOOKS", raising=False)
    seen = []

    def fake_run_recipe(stem, ofol, recipes_dir, **kwargs):
        seen.append((stem, Path(recipes_dir).name, kwargs["input_root"], kwargs["experiment"]))
        if stem == "bad":
            recipes.marker_path(ofol, stem).write_text(json.dumps({"returncode": 1, "error": "esmvaltool run failed"}))
        return stem == "good"

    monkeypatch.setattr(run_mod.recipes_mod, "run_recipe", fake_run_recipe)
    monkeypatch.setattr(run_mod, "run_notebook", lambda *a, **k: pytest.fail("notebooks must not run"))
    _run_main(monkeypatch, wfolder, "--kind", "recipes")

    assert seen == [("good", "recipes", "/g/data/x/archive", "exp1"), ("bad", "recipes", "/g/data/x/archive", "exp1")]
    mdfol = wfolder / "notebooks" / "mkfigs_output_exp1" / "mkmd"
    errors = (mdfol / "mkfigs_recipe_errors.log").read_text()
    assert "FAILED recipe: bad" in errors and "esmvaltool run failed" in errors and "good" not in errors
    assert not (mdfol / "mkfigs_errors.log").exists()


def test_main_kind_recipes_requires_the_recipes_env_var(tmp_path, monkeypatch):
    wfolder = tmp_path / "paper-repo"
    (wfolder / "notebooks").mkdir(parents=True)
    monkeypatch.delenv("MKFIGS_RECIPES", raising=False)
    with pytest.raises(SystemExit):
        _run_main(monkeypatch, wfolder, "--kind", "recipes")


def test_main_default_kind_never_touches_recipes(tmp_path, monkeypatch):
    wfolder = tmp_path / "paper-repo"
    (wfolder / "notebooks").mkdir(parents=True)
    monkeypatch.setenv("MKFIGS_NOTEBOOKS", "SST")
    monkeypatch.setenv("MKFIGS_RECIPES", "demo")
    monkeypatch.setattr(run_mod, "run_notebook", lambda *a, **k: True)
    monkeypatch.setattr(run_mod.recipes_mod, "run_recipe", lambda *a, **k: pytest.fail("recipes must not run"))
    _run_main(monkeypatch, wfolder)


def test_run_recipe_passes_moppy_model_patterns_and_keeps_config_out_of_home(recipe_repo, tmp_path):
    repo, ofol = recipe_repo
    base = tmp_path / "base_cfg"
    base.mkdir()
    (base / "data.yml").write_text("rootpath: {}\n")
    envs: list[dict] = []
    calls: list[list[str]] = []
    inner = fake_runner(calls=calls)

    def runner(cmd, cwd=None, env=None, **kwargs):
        envs.append(env)
        return inner(cmd, cwd=cwd, **kwargs)

    assert recipes.run_recipe(
        "demo", ofol, repo / "notebooks" / "recipes", experiment=ENAME, repo_root=repo,
        input_root="/in", cache_dir="/c", model_id="ACCESS-OM3",
        patterns=["Omon.tos:output*/access-om3.mom6.2d.tos.1mon.mean.*.nc"], base_config_dir=str(base), runner=runner,
    )
    cfg_dir = ofol / recipes.CONFIG_SUBDIR
    prep = calls[0]
    assert prep[prep.index("--output-config") + 1] == str(cfg_dir / recipes.MOPPY_CONFIG_NAME)
    assert prep[prep.index("--model-id") + 1] == "ACCESS-OM3"
    assert prep[prep.index("--pattern") + 1] == "Omon.tos:output*/access-om3.mom6.2d.tos.1mon.mean.*.nc"
    assert all(e["ESMVALTOOL_CONFIG_DIR"] == str(cfg_dir) for e in envs)
    assert (cfg_dir / "data.yml").read_text() == "rootpath: {}\n"
    # MOPPy's creator profile defaults to the output folder: not $HOME, not the ESMValTool config dir
    assert prep[prep.index("--user-config") + 1] == str(ofol / recipes.MOPPY_USER_CONFIG_NAME)


def test_run_recipe_passes_extra_moppy_args_and_a_given_user_config(recipe_repo):
    repo, ofol = recipe_repo
    calls: list[list[str]] = []
    extra = ["--supergrid", "/g/hgrid.nc", "--user-config", "/cfg/me.yml"]
    assert recipes.run_recipe("demo", ofol, repo / "notebooks" / "recipes", experiment=ENAME, repo_root=repo,
                              input_root="/in", moppy_args=extra, runner=fake_runner(calls=calls))
    assert calls[0][-len(extra):] == extra
    assert calls[0].count("--user-config") == 1


def test_moppy_args_from_env():
    env = {"MKFIGS_MOPPY_SUPERGRID": "/g/hgrid.nc", "MKFIGS_MOPPY_NOMINAL_RES": "25 km", "MKFIGS_MOPPY_WORKERS": " ",
           "MKFIGS_MOPPY_DATASETS": "ACCESS-OM3:ACCESS-OM3-alt"}
    assert recipes.moppy_args_from_env(env) == [
        "--supergrid", "/g/hgrid.nc", "--nominal-resolution", "25 km",
        "--dataset", "ACCESS-OM3", "--dataset", "ACCESS-OM3-alt",
    ]
    assert recipes.moppy_args_from_env({}) == []


def test_find_run_dir_real_layout(tmp_path):
    """Run dirs are <recipe stem>_<YYYYMMDD_HHMMSS>; newest wins; dirs without plots/ or a timestamp are ignored."""
    assert recipes.find_run_dir(tmp_path / "missing") is None
    make_run_dir(tmp_path, name="om3_mlotst_basic", stamp="20261110_220040")
    newest = make_run_dir(tmp_path, name="om3_mlotst_basic", stamp="20261110_235640")
    (tmp_path / "om3_mlotst_basic_20261111_000000").mkdir()  # failed run: no plots/
    (tmp_path / "notes" / "plots").mkdir(parents=True)        # not a run dir
    assert recipes.find_run_dir(tmp_path) == newest


def test_find_filled_recipe(tmp_path):
    run_dir = make_run_dir(tmp_path, name="om3_mlotst_basic")
    assert recipes.find_filled_recipe(run_dir, "om3_mlotst_basic") == run_dir / "run" / "om3_mlotst_basic_filled.yml"
    assert recipes.find_filled_recipe(run_dir, "other") == run_dir / "run" / "om3_mlotst_basic_filled.yml"
    (run_dir / "run" / "om3_mlotst_basic_filled.yml").unlink()
    assert recipes.find_filled_recipe(run_dir, "om3_mlotst_basic") is None


def test_run_recipe_drops_config_files_left_by_an_earlier_run(recipe_repo):
    repo, ofol = recipe_repo
    cfg_dir = ofol / recipes.CONFIG_SUBDIR
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "old-cache-data.yml").write_text("rootpath: {CMIP6: /old/cache}\n")
    assert recipes.run_recipe("demo", ofol, repo / "notebooks" / "recipes", experiment=ENAME, repo_root=repo,
                              runner=fake_runner())
    assert not (cfg_dir / "old-cache-data.yml").exists()


def test_recipes_get_their_own_section_before_miscellaneous():
    from datetime import datetime, timezone
    cats = {"Sea ice": ["SeaIce_area"], "Miscellaneous": ["odd"]}
    _, md = pushit.build_run_summary(
        "e", "/x.json", ok_nbs=["SeaIce_area", "zz_recipe", "odd", "a_recipe"], failed_nbs=[], not_run_nbs=[],
        prev_committed_nbs=[], run_time=datetime(2026, 1, 1, tzinfo=timezone.utc), categories=cats,
        recipes={"zz_recipe", "a_recipe"})
    heads = [line for line in md.splitlines() if line.startswith("### ")]
    assert [h.split(" {")[0] for h in heads] == ["### Sea ice", f"### {pushit.RECIPES_CATEGORY}", "### Miscellaneous"]
    section = md[md.index(f"### {pushit.RECIPES_CATEGORY}"):md.index("### Miscellaneous")]
    assert section.index("`a_recipe`") < section.index("`zz_recipe`") and "`odd`" not in section


def test_nav_regrouping_replaces_an_old_recipes_group(patch_repo_paths):
    pushit.update_mkdocs_nav(ENAME, ["SST", "demo"], recipes={"demo"})
    pushit.update_mkdocs_nav(ENAME, ["SST", "demo"], recipes={"demo"})
    nav = yaml.safe_load((patch_repo_paths / "documentation" / "mkdocs.yml").read_text())["nav"]
    assert sum(1 for i in nav if pushit.RECIPES_CATEGORY in i) == 1


def test_recipe_page_ends_with_commands_that_reproduce_the_run(recipe_repo, monkeypatch, tmp_path):
    repo, ofol = recipe_repo
    monkeypatch.setenv("MKFIGS_CONDA_MODULE", "conda/analysis3-26.11")
    assert recipes.run_recipe(
        "demo", ofol, repo / "notebooks" / "recipes", experiment=ENAME, repo_root=repo, input_root="/g/data/ol01/run",
        model_id="ACCESS-OM3", patterns=["Omon.mlotst:output[0-9]*/x.*.nc"], esmvaltool_args="--max-parallel-tasks 4",
        moppy_args=["--supergrid", "/g/grid.nc", "--nominal-resolution", "25 km", "--user-config", "/mine.yml"],
        runner=fake_runner())
    text = recipes.write_recipe_page(ofol, "demo", tmp_path / "recipes" / "demo.md", ENAME).read_text()
    section = text.split("## Reproduce these plots")[1]
    assert "module load conda/analysis3-26.11" in section
    assert "python -m mkfigs.moppy_prepare notebooks/recipes/demo.yml" in section
    assert "--pattern 'Omon.mlotst:output[0-9]*/x.*.nc'" in section and "--nominal-resolution '25 km'" in section
    assert "/mine.yml" not in section and "--user-config $WORK/moppy_user.yml" in section
    assert "esmvaltool run notebooks/recipes/demo.yml --output_dir $WORK/output --max-parallel-tasks 4" in section
    assert text.rstrip().endswith("{% endraw %}")


def test_recipe_page_without_reproduce_info_has_no_commands(tmp_path):
    ofol = tmp_path / "o"
    ofol.mkdir()
    recipes.marker_path(ofol, "demo").write_text(json.dumps({"returncode": 0}))
    assert "Reproduce" not in recipes.write_recipe_page(ofol, "demo", tmp_path / "d.md", ENAME).read_text()


def test_conda_module_detection():
    assert recipes.conda_module({"MKFIGS_CONDA_MODULE": "conda/a"}) == "conda/a"
    assert recipes.conda_module({"LOADEDMODULES": "pbs:conda/analysis3-26.11:git"}) == "conda/analysis3-26.11"
    assert recipes.conda_module({}) is None


def test_run_recipe_copies_named_esmvalcore_presets(recipe_repo, tmp_path):
    repo, ofol = recipe_repo
    presets = tmp_path / "configurations"
    presets.mkdir()
    (presets / "data-hpc-nci.yml").write_text("projects: {OBS6: {}}\n")
    (presets / "data-local.yml").write_text("projects: {}\n")
    assert recipes.run_recipe("demo", ofol, repo / "notebooks" / "recipes", experiment=ENAME, repo_root=repo,
                              presets=["data-hpc-nci"], presets_dir=presets, runner=fake_runner())
    cfg = ofol / recipes.CONFIG_SUBDIR
    assert (cfg / "data-hpc-nci.yml").read_text() == "projects: {OBS6: {}}\n"
    assert not (cfg / "data-local.yml").exists()
    assert recipes.read_marker(ofol, "demo")["reproduce"]["presets"] == ["data-hpc-nci"]


def test_run_recipe_unknown_preset_is_a_failure_not_a_crash(recipe_repo, tmp_path):
    repo, ofol = recipe_repo
    calls = []
    assert not recipes.run_recipe("demo", ofol, repo / "notebooks" / "recipes", experiment=ENAME, repo_root=repo,
                                  presets=["data-hpc-nowhere"], presets_dir=tmp_path, runner=fake_runner(calls=calls))
    assert "data-hpc-nowhere.yml" in recipes.read_marker(ofol, "demo")["error"]
    assert calls == []


def test_presets_from_env():
    assert recipes.presets_from_env({}) == []
    assert recipes.presets_from_env({"MKFIGS_ESMVALTOOL_PRESETS": "data-hpc-nci, extra:more"}) == [
        "data-hpc-nci", "extra", "more"]


def test_reproduce_commands_copy_the_presets():
    text = recipes.reproduce_commands("demo", {"reproduce": {"recipe": "notebooks/recipes/demo.yml",
                                                            "presets": ["data-hpc-nci"]}})
    assert ("esmvaltool config copy data-hpc-nci.yml --target_file $WORK/esmvaltool_config/data-hpc-nci.yml"
            in text)


def test_group_recipes_by_category_orders_and_collects_unlisted(capsys):
    cats = {"B group": ["r2", "r1"], "A group": ["r1", "r3"], "Empty": ["missing"]}
    got = pushit.group_recipes_by_category(["r3", "r1", "x", "r2"], cats, warn=True)
    assert got == {"B group": ["r1", "r2"], "A group": ["r3"], pushit.UNCATEGORISED_RECIPES: ["x"]}
    assert "'x' is not in RECIPE_CATEGORIES" in capsys.readouterr().out
    assert pushit.group_recipes_by_category(["b", "a"], {}) == {pushit.RECIPES_CATEGORY: ["a", "b"]}
    assert pushit.group_recipes_by_category([], {"A": ["a"]}) == {}


def test_recipe_categories_make_subheadings_in_the_recipes_section():
    from datetime import datetime, timezone
    _, md = pushit.build_run_summary(
        "e", "/x.json", ok_nbs=["nb", "r_flux", "r_ts", "r_new"], failed_nbs=[], not_run_nbs=[],
        prev_committed_nbs=[], run_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
        recipes={"r_flux", "r_ts", "r_new"}, recipe_categories={"Fluxes": ["r_flux"], "T and S": ["r_ts"]})
    section = md[md.index(f"### {pushit.RECIPES_CATEGORY}"):md.index("- **ESM datastore")]
    subs = [line.split(" {")[0] for line in section.splitlines() if line.startswith("#### ")]
    assert subs == ["#### Fluxes", "#### T and S", f"#### {pushit.UNCATEGORISED_RECIPES}"]
    assert section.index("`r_flux`") < section.index("#### T and S") < section.index("`r_ts`")
    assert "`nb`" not in section
    # Jump-to counts the whole recipes section.
    assert f"[{pushit.RECIPES_CATEGORY}](#cat-esmvaltool-recipes) (3)" in md
    assert '{id="cat-recipes-fluxes"}' in md


def test_recipe_categories_nest_the_nav_group(patch_repo_paths):
    pushit.update_mkdocs_nav(ENAME, ["SST", "a", "b"], recipes={"a", "b"}, recipe_categories={"Group B": ["b"]})
    nav = yaml.safe_load((patch_repo_paths / "documentation" / "mkdocs.yml").read_text())["nav"]
    group = next(i for i in nav if pushit.RECIPES_CATEGORY in i)[pushit.RECIPES_CATEGORY]
    page = lambda nb: {nb: [{"Summary": f"pages/experiments/{ENAME}/{nb}.md"},
                            {"Recipe": f"pages/experiments/{ENAME}/recipes/{nb}.md"}]}
    assert group == [{"Group B": [page("b")]}, {pushit.UNCATEGORISED_RECIPES: [page("a")]}]


def test_timeout_from_env_reads_minutes():
    assert recipes.timeout_from_env({}) is None
    assert recipes.timeout_from_env({"MKFIGS_RECIPE_TIMEOUT": ""}) is None
    assert recipes.timeout_from_env({"MKFIGS_RECIPE_TIMEOUT": "0"}) is None
    assert recipes.timeout_from_env({"MKFIGS_RECIPE_TIMEOUT": "nope"}) is None
    assert recipes.timeout_from_env({"MKFIGS_RECIPE_TIMEOUT": "1.5"}) == 90


def test_run_killable_kills_the_whole_process_group(tmp_path):
    import time
    pidfile = tmp_path / "child.pid"
    start = time.monotonic()
    # The grandchild stands in for an esmvaltool worker left behind by a hung run.
    done = recipes.run_killable(["bash", "-c", f"sleep 60 & echo $! > {pidfile}; wait"], timeout=1)
    assert time.monotonic() - start < 30
    assert done.timed_out and done.returncode != 0
    child = int(pidfile.read_text())
    for _ in range(50):
        if not Path(f"/proc/{child}").exists() or "Z" in Path(f"/proc/{child}/stat").read_text().split()[2]:
            break
        time.sleep(0.1)
    else:
        raise AssertionError("grandchild still running")
    assert not recipes.run_killable(["true"], timeout=10).timed_out


def test_timed_out_recipe_is_reported_as_failed(recipe_repo):
    repo, ofol = recipe_repo
    seen = {}

    def hung_runner(cmd, cwd=None, env=None, timeout=None):
        seen["timeout"] = timeout
        make_run_dir(Path(cmd[cmd.index("--output_dir") + 1]))
        done = SimpleNamespace(returncode=-9, timed_out=True)
        return done

    assert not recipes.run_recipe("demo", ofol, repo / "notebooks" / "recipes", experiment=ENAME, repo_root=repo,
                                  timeout=120, runner=hung_runner)
    assert seen["timeout"] == 120
    marker = recipes.read_marker(ofol, "demo")
    assert "killed after 2 min" in marker["error"] and "MKFIGS_RECIPE_TIMEOUT" in marker["error"]


def test_pinned_copy_from_an_earlier_run_is_not_reused(recipe_repo):
    repo, ofol = recipe_repo
    calls = []
    stale = ofol / "recipes" / "demo.moppy-pinned.yml"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text("old: true\n")
    assert recipes.run_recipe("demo", ofol, repo / "notebooks" / "recipes", experiment=ENAME, repo_root=repo,
                              input_root="/g/data/ol01/run", runner=fake_runner(calls=calls))
    assert not stale.exists()
    assert calls[-1][2].endswith("demo.yml")

