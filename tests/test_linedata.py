"""Tests for mkfigs.linedata: what the figure explorer reads back from saved figures."""

import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pytest

from mkfigs import linedata
from mkfigs.configdoc import MkmdWriter

RUN = "MC_25km_jra_iaf+wombatlite-test4-d28e0359"


@pytest.fixture(autouse=True)
def _close_figures():
    yield
    plt.close("all")


@pytest.mark.parametrize("path, expected", [
    (f"/g/data/ol01/outputs/access-om3-25km/{RUN}/datastore.json", RUN),
    (f"/g/data/ol01/outputs/access-om3-25km/{RUN}/experiment_datastore.json", RUN),
    (f"/scratch/x77/someone/{RUN}/archive/experiment_datastore.json", RUN),
    ("/g/data/zv30/non-cmip/ACCESS-CM3/cm3-run-27-07-2026-PD-control/cm3-datastore/cm3-datastore.json",
     "cm3-run-27-07-2026-PD-control"),
])
def test_run_name_skips_generic_catalogue_folders(path, expected):
    assert linedata.run_name(path) == expected


def test_split_title_keeps_periods_and_drops_run_names():
    clean, periods = linedata.split_title("Global Ocean Meridional Heat Transport\n1990-01 - 2018-12 mean")
    assert clean == "Global Ocean Meridional Heat Transport"
    assert periods == ["1990-01 - 2018-12 mean"]
    clean, periods = linedata.split_title(
        f"Ocean Mixed Layer Thickness JFM 2013-12-16 - 2023-12-16 mean in {RUN}", RUN)
    assert clean == "Ocean Mixed Layer Thickness JFM"
    assert periods == ["2013-12-16 - 2023-12-16 mean"]


def _multi_run_figure():
    t = np.arange("1958-01", "1961-01", dtype="datetime64[M]")
    fig, ax = plt.subplots()
    ax.plot(t, np.linspace(160, 175, t.size), color="C3", alpha=0.3)                 # monthly, unlabelled
    ax.plot(t, np.linspace(161, 174, t.size), color="C3", label=f"ACCESS-OM3 {RUN}")  # rolling mean
    ax.plot(t, np.linspace(140, 145, t.size), color="C0", label="ACCESS-OM2 025deg_jra55_iaf_omip2_cycle1")
    ax.axhline(173.3, color="k", ls="--", label="Donohue et al. (2016)")
    ax.axvspan(np.datetime64("1959-01-01"), np.datetime64("1960-01-01"), color="grey", alpha=0.2)
    ax.set_title("Drake Passage transport")
    ax.set_ylabel("Eastward Transport (Sv)")
    return fig


def test_capture_credits_own_lines_and_keeps_references():
    rec = linedata.capture(_multi_run_figure(), run=RUN, notebook="StraitTransports", fignum=12,
                           title="Strait Transports", caption="Drake Passage transport.", png="StraitTransports_12.png")
    assert rec["status"] == "ok"
    assert rec["key"] == "StraitTransports||Drake Passage transport"
    (panel,) = rec["panels"]
    roles = [(line["style"], line["role"]) for line in panel["lines"]]
    assert roles == [("thin", "own"), ("line", "own"), ("line", "other")]
    assert panel["x_kind"] == "time" and panel["lines"][0]["x"][0] == "1958-01-01T00:00:00"
    kinds = {m["kind"]: m for m in panel["marks"]}
    assert kinds["hline"]["y"] == 173.3 and kinds["hline"]["label"] == "Donohue et al. (2016)"
    assert kinds["span"]["x"] == ["1959-01-01T00:00:00", "1960-01-01T00:00:00"]
    json.dumps(rec)  # JSON-ready throughout


def test_single_run_figure_takes_every_line_and_markers_are_references():
    fig, (a, b) = plt.subplots(1, 2)
    a.plot(np.arange(1, 13), np.arange(12.0), label="Total freeze")
    a.plot(np.arange(1, 13), -np.arange(12.0), label="Total melt")
    b.plot([10, 24, 47], [0.4, 1.3, 0.6], "kd", label="Ganachaud and Wunsch 2003")
    b.plot(np.arange(-80, 90), np.zeros(170), label="model")
    rec = linedata.capture(fig, run=RUN, notebook="X", fignum=1, title="t", caption="c", png="X_01.png")
    assert rec["status"] == "all-lines-own"
    roles = [line["role"] for p in rec["panels"] for line in p["lines"]]
    assert roles == ["own", "own", "reference", "own"]


def test_gid_overrides_the_rule():
    fig, ax = plt.subplots()
    ax.plot([1, 2, 3], [1, 2, 3], label=f"obs climatology for {RUN}", gid="reference")
    ax.plot([1, 2, 3], [3, 2, 1], label=f"OM3 {RUN}")
    rec = linedata.capture(fig, run=RUN, notebook="X", fignum=1, title="t", caption="c", png="X_01.png")
    assert [line["role"] for line in rec["panels"][0]["lines"]] == ["reference", "own"]


def test_blank_and_map_figures_are_flagged_not_dropped():
    blank = linedata.capture(plt.figure(), run=RUN, notebook="SeaIce_area", fignum=1,
                             title="Sea Ice Area", caption="c", png="SeaIce_area_01.png")
    assert blank["status"] == "empty" and blank["key"] is None
    fig, ax = plt.subplots()
    ax.pcolormesh(np.random.default_rng(0).random((4, 4)))
    assert linedata.capture(fig, run=RUN, notebook="SST", fignum=1, title="t", caption="c",
                            png="SST_01.png")["status"] == "no-lines"
    assert "check" in linedata.summary(blank)


def test_keys_are_stable_across_periods_and_numbered_only_on_a_clash():
    seen = set()
    keys = []
    for period in ("1990-01 - 2018-12 mean", "1979-01 - 2008-12 mean", "1990-01 - 2018-12 mean"):
        fig, ax = plt.subplots()
        ax.plot([1, 2, 3], [1, 2, 3], label=f"OM3 {RUN}")
        ax.set_title(f"Global Ocean Meridional Heat Transport\n{period}")
        keys.append(linedata.capture(fig, run=RUN, notebook="MHT", fignum=1, title="t", caption="c",
                                     png="MHT_01.png", seen=seen if period.startswith("1990") else None)["key"])
    assert keys[0] == keys[1] == "MHT||Global Ocean Meridional Heat Transport"
    assert keys[2] == keys[0] + "#2"


def test_cftime_axes_export_iso_dates():
    cftime = pytest.importorskip("cftime")
    pytest.importorskip("nc_time_axis")
    t = [cftime.DatetimeNoLeap(1900 + i // 12, i % 12 + 1, 15) for i in range(24)]
    fig, ax = plt.subplots()
    ax.plot(t, np.arange(24.0), label=f"OM3 {RUN}")
    (panel,) = linedata.capture(fig, run=RUN, notebook="timeseries", fignum=1, title="t", caption="c",
                                png="timeseries_01.png")["panels"]
    assert panel["x_kind"] == "time" and panel["x_calendar"] == "noleap"
    assert panel["lines"][0]["x"][0] == "1900-01-15T00:00:00"


def test_savefig_writes_line_data_next_to_the_png(tmp_path):
    w = MkmdWriter(f"/g/data/x/{RUN}/datastore.json", "StraitTransports.ipynb", str(tmp_path) + "/", pm=True)
    w.savefig(_multi_run_figure(), "Strait Transports", "Drake Passage transport.")
    assert (tmp_path / "mkmd" / "StraitTransports_01.png").exists()
    rec = json.loads((tmp_path / "mkmd" / "StraitTransports_01.lines.json").read_text())
    assert rec["run"] == RUN and rec["status"] == "ok"


def test_a_failed_export_never_stops_the_figure_being_saved(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("unexpected artist")
    monkeypatch.setattr(linedata, "capture", boom)
    w = MkmdWriter(f"/g/data/x/{RUN}/datastore.json", "SST.ipynb", str(tmp_path) + "/", pm=True)
    w.savefig(_multi_run_figure(), "SST", "caption")
    assert (tmp_path / "mkmd" / "SST_01.png").exists()
    assert (tmp_path / "mkmd" / "SST.md").exists()
    assert not (tmp_path / "mkmd" / "SST_01.lines.json").exists()
