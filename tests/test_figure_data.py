"""Tests for the figure-explorer data path: Experiments page, bundles, index, backfill, pushit."""

import gzip
import json
import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import responses

from mkfigs import backfill, experiments, figuredata, pushit
from mkfigs.configdoc import FigshareUploader
from tests.conftest import FIGSHARE_BASE, FakeFigshareServer

IAF = "MC_25km_jra_iaf+wombatlite-test4-d28e0359"
RYF = "MC_25km_jra_ryf+wombatlite-test3-f4d79e82"
TABLE = f"""# Experiments

## Key experiments

| Experiment | Evaluation | Base Configuration | Date completed | Description | Model build | Length (years) | ESM Datastore |
|---|---|---|---|---|---|---|---|
| [{IAF}](https://github.com/x/tree/a) | [link](https://access-om3-paper-1.readthedocs.io/mc_25km_jra_iafwombatlite-test4-d28e0359-2026.07.000/) | dev-MC_25km_jra_iaf+wombatlite | 1-Jul-26 | 25km IAF<br>with WOMBAT-lite | 2026.05.002 | 66 years (1958-2023) | `{{DS}}/{IAF}/datastore.json` |
| [{RYF}](https://github.com/x/tree/b) | [link](https://access-om3-paper-1.readthedocs.io/mc_25km_jra_ryfwombatlite-test3-f4d79e82-2026.07.000/) | dev-MC_25km_jra_iaf+wombatlite | 26-Jun-26 | RYF test | 2026.05.002 | 31 years (1900-1930) | `{{DS}}/{RYF}/experiment_datastore.json` |

## Community run sensitivity tests

| Experiment | Evaluation | Base Configuration | Model build | Length (years) | ESM Datastore |
|---|---|---|---|---|---|
| [gm1](https://github.com/x/tree/c) | [link](https://access-om3-paper-1.readthedocs.io/mc_25km_jra_iaf-1.0-beta-gm1-d968c801-2026.07.000/) | dev-MC_25km_jra_iaf | 2025.08.001 | 32 years (1979-2010) | `{{DS}}/MC_25km_jra_iaf-1.0-beta-gm1-d968c801/archive/experiment_datastore.json` |
"""


def _table(tmp_path: Path, datastores: Path) -> Path:
    path = tmp_path / "Experiments.md"
    path.write_text(TABLE.replace("{DS}", str(datastores)))
    return path


def test_experiments_page_parses_names_forcing_and_metadata(tmp_path):
    runs = experiments.parse_experiments(_table(tmp_path, Path("/g/data/ol01/outputs")).read_text())
    assert [r.name for r in runs] == [IAF, RYF, "MC_25km_jra_iaf-1.0-beta-gm1-d968c801"]
    assert [r.group for r in runs] == ["Key experiments", "Key experiments", "Community run sensitivity tests"]
    iaf, ryf, gm1 = runs
    assert (iaf.forcing, iaf.forcing_mismatch, iaf.description) == ("IAF", False, "25km IAF with WOMBAT-lite")
    assert (ryf.forcing, ryf.forcing_mismatch) == ("RYF", True)  # listed with an iaf base configuration
    assert gm1.length == "32 years (1979-2010)" and gm1.date_completed == ""


def test_tag_lookup_matches_readthedocs_slugs():
    run = experiments.Run(name=IAF, datastore="", group="", table_name="", evaluation_url=(
        "https://access-om3-paper-1.readthedocs.io/mc_25km_jra_iafwombatlite-test4-d28e0359-2026.07.000/"))
    tags = [f"{IAF}-2026.07.000", "MC_25km_jra_iaf-1.0-beta-gm1-d968c801-2026.07.000"]
    assert experiments.tag_for(run, tags) == f"{IAF}-2026.07.000"
    assert experiments.tag_for(run, tags[1:]) is None


def _paper_repo_with_tag(fake_paper_repo: Path, run: str, article: int) -> None:
    """Turn the fake paper repo into a git repo with a tagged evaluation linking to *article*."""
    def git(*args):
        subprocess.run(["git", "-C", str(fake_paper_repo), *args], check=True, capture_output=True)
    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (fake_paper_repo / "documentation" / "docs" / "pages" / "index.md").write_text(
        f"# {run}\n\n[Figshare](https://figshare.com/articles/figure/x/{article})\n")
    git("add", "-A")
    git("commit", "-qm", "evaluation")
    git("tag", f"{run}-2026.07.000")


def _seed_line_data(mdfol: Path, run: str, statuses=("ok",)) -> None:
    mdfol.mkdir(parents=True, exist_ok=True)
    for i, status in enumerate(statuses, 1):
        (mdfol / f"timeseries_{i:02d}.lines.json").write_text(json.dumps({
            "schema": 1, "key": f"timeseries||thetaoga {i}", "notebook": "timeseries", "fignum": i,
            "png": f"timeseries_{i:02d}.png", "run": run, "status": status, "panels": []}))


def test_bundles_are_deterministic_and_report_problems(tmp_path):
    mdfol = tmp_path / "mkmd"
    _seed_line_data(mdfol, IAF, statuses=("ok", "empty", "all-lines-own"))
    prov = {"paper_commit": "abc", "mkfigs_commit": "def", "generated_at": "2026-10-18T00:00:00+00:00"}
    first = figuredata.sha256(figuredata.build_bundle(mdfol, IAF, tmp_path, prov))
    second = figuredata.sha256(figuredata.build_bundle(mdfol, IAF, tmp_path, prov))
    assert first == second
    data = json.loads(gzip.decompress((tmp_path / figuredata.bundle_name(IAF)).read_bytes()))
    assert data["run"] == IAF and len(data["figures"]) == 3 and data["provenance"]["paper_commit"] == "abc"
    notes = figuredata.problems(data["figures"])
    assert len(notes) == 2 and "blank figure" in notes[0]


def test_index_keeps_one_entry_per_run(tmp_path):
    index = tmp_path / "documentation" / "figure-data.json"
    figuredata.update_index(index, IAF, {"url": "u1", "sha256": "s1"})
    figuredata.update_index(index, RYF, {"url": "u2", "sha256": "s2"})
    figuredata.update_index(index, IAF, {"url": "u3", "sha256": "s3"})
    assert figuredata.index_entry(index, IAF)["url"] == "u3"
    assert sorted(json.loads(index.read_text())["runs"]) == sorted([IAF, RYF])


def test_upload_is_checked_against_figshares_own_md5(tmp_path, fake_figshare):
    up = FigshareUploader("tok", IAF, str(tmp_path))
    article = up._get_or_create_article()
    bundle = tmp_path / figuredata.bundle_name(IAF)
    bundle.write_bytes(b"bundle bytes")
    up._upload_file(article, str(bundle))
    assert figuredata.uploaded_intact(up, article, bundle)
    bundle.write_bytes(b"changed after upload")
    assert not figuredata.uploaded_intact(up, article, bundle)


@pytest.fixture
def fake_figshare_publishing():
    """The fake server plus a publish endpoint. Opt-in: only backfill tests may publish."""
    server = FakeFigshareServer()
    server.published = []
    with responses.RequestsMock(assert_all_requests_are_fired=False) as mock:
        server.register(mock)

        def _publish(request):
            server.published.append(int(re.search(r"articles/(\d+)/publish", request.url).group(1)))
            return (201, {}, json.dumps({"location": "published"}))
        mock.add_callback(responses.POST, re.compile(re.escape(FIGSHARE_BASE) + r"/account/articles/(\d+)/publish$"),
                          callback=_publish)
        yield server


def _backfill_setup(fake_paper_repo, tmp_path, server, monkeypatch):
    stores = tmp_path / "outputs"
    (stores / IAF).mkdir(parents=True)
    (stores / IAF / "datastore.json").write_text("{}")
    article = server._next_article_id
    server._articles[article] = {"title": f"ACCESS-OM3 evaluation figures – {IAF}"}
    server._article_files[article] = []
    server._next_article_id += 1
    _paper_repo_with_tag(fake_paper_repo, IAF, article)
    _seed_line_data(fake_paper_repo / "notebooks" / f"mkfigs_output_{IAF}" / "mkmd", IAF)
    monkeypatch.chdir(fake_paper_repo / "notebooks")
    return _table(tmp_path, stores), article


def test_backfill_upload_publishes_only_after_the_check_and_writes_the_index(
        patch_repo_paths, tmp_path, fake_figshare_publishing, figshare_token, monkeypatch):
    table, article = _backfill_setup(patch_repo_paths, tmp_path, fake_figshare_publishing, monkeypatch)
    status = backfill.main(["upload", "--table", str(table), "--only", IAF])
    assert status == 0
    assert fake_figshare_publishing.published == [article]
    entry = figuredata.index_entry(patch_repo_paths / figuredata.INDEX_RELPATH, IAF)
    assert entry["url"].startswith("https://ndownloader.figshare.com/files/")
    assert entry["figshare_article"] == article and len(entry["sha256"]) == 64


def test_backfill_upload_with_no_publish_never_publishes(
        patch_repo_paths, tmp_path, fake_figshare, figshare_token, monkeypatch):
    # The default fake has no publish endpoint: any publish attempt would raise ConnectionError.
    table, _ = _backfill_setup(patch_repo_paths, tmp_path, fake_figshare, monkeypatch)
    assert backfill.main(["upload", "--no-publish", "--table", str(table), "--only", IAF]) == 0
    assert figuredata.index_entry(patch_repo_paths / figuredata.INDEX_RELPATH, IAF)


def test_backfill_submit_sends_one_job_per_run_and_skips_missing_datastores(
        patch_repo_paths, tmp_path, monkeypatch, capsys):
    stores = tmp_path / "outputs"
    (stores / IAF).mkdir(parents=True)
    (stores / IAF / "datastore.json").write_text("{}")
    monkeypatch.chdir(patch_repo_paths / "notebooks")
    with patch.object(backfill.subprocess, "run") as qsub:
        qsub.return_value.returncode, qsub.return_value.stdout = 0, "123.gadi-pbs"
        status = backfill.main(["submit", "--table", str(_table(tmp_path, stores))])
    (call,) = qsub.call_args_list
    assert call.args[0][:2] == ["qsub", "-v"] and f"ENAME={IAF}," in call.args[0][2]
    assert "MKFIGS_NOTEBOOKS=timeseries:Timeseries_daily_extreme_from_2D_fields:" in call.args[0][2]
    out = capsys.readouterr().out
    assert status == 1 and "SKIPPED" in out and RYF in out


def test_pushit_uploads_the_bundle_and_indexes_it_with_the_runs_pages(
        patch_repo_paths, fake_figshare, figshare_token):
    """New runs: the normal pushit run uploads the bundle and adds the run to the index."""
    from tests.test_pushit_modes import _seed_notebook_outputs
    ename = "test_experiment_01"
    _, mdfol = _seed_notebook_outputs(patch_repo_paths, ename, ["SST"])
    _seed_line_data(mdfol, ename)
    with patch.object(sys, "argv", ["mkfigs-pushit"]), patch.object(pushit.subprocess, "run"):
        pushit.main()
    entry = figuredata.index_entry(patch_repo_paths / figuredata.INDEX_RELPATH, ename)
    assert entry and entry["url"].startswith("https://ndownloader.figshare.com/files/")
    names = [f["name"] for f in fake_figshare._files.values()]
    assert figuredata.bundle_name(ename) in names
