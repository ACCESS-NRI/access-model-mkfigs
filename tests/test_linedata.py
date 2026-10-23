"""Tests for mkfigs.linedata: what the figure explorer reads back from saved figures."""

import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import dates as mdates
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


def test_nanosecond_datetimes_export_iso_dates():
    # xarray/pandas time coordinates are datetime64[ns]: cast to object they become ints
    t = np.array(["1958-01-16T12", "1958-02-15", "1970-01-01", "2018-12-16T12"], dtype="datetime64[ns]")
    fig, ax = plt.subplots()
    ax.plot(t, [1.0, 2.0, 3.0, 4.0], label=RUN)
    rec = linedata.capture(fig, run=RUN, notebook="SeaIce_Vol", fignum=1, title="", caption="", png="SeaIce_Vol_01.png")
    p = rec["panels"][0]
    assert p["x_kind"] == "time"
    assert p["lines"][0]["x"] == ["1958-01-16T12:00:00", "1958-02-15T00:00:00", "1970-01-01T00:00:00", "2018-12-16T12:00:00"]


def test_lines_drawn_in_axis_units_on_a_date_axis_are_dates_and_range_bounds_a_band():
    # StraitTransports: plt.plot(plt.gca().get_xlim(), [hi, hi], 'k:', alpha=0.2), and the same for lo
    t = np.arange("1958-01", "1961-01", dtype="datetime64[M]")
    fig, ax = plt.subplots()
    ax.plot(t, np.linspace(160, 175, t.size), label=RUN)
    ax.plot(ax.get_xlim(), [173.3, 173.3], "k--", label="Donohue et al. (2016)")
    ax.plot(ax.get_xlim(), [183.0, 183.0], "k:", alpha=0.2)
    ax.plot(ax.get_xlim(), [163.6, 163.6], "k:", alpha=0.2)
    x0, x1 = ax.get_xlim()
    ax.plot([x0, x1], [150.0, 151.0], color="C1", label="other")  # numeric x, not constant
    rec = linedata.capture(fig, run=RUN, notebook="StraitTransports", fignum=12, title="", caption="", png="StraitTransports_12.png")
    p = rec["panels"][0]
    assert {"kind": "hline", "label": "Donohue et al. (2016)", "y": 173.3} in p["marks"]
    assert {"kind": "band", "label": "Donohue et al. (2016)", "y": [163.6, 183.0]} in p["marks"]
    assert len(p["lines"]) == 2 and all(isinstance(x, str) for line in p["lines"] for x in line["x"])
    assert p["lines"][1]["x"] == [mdates.num2date(v).replace(tzinfo=None).isoformat()[:19] for v in (x0, x1)]


def test_unlabelled_other_lines_take_the_label_of_their_colour():
    t = np.arange("1958-01", "1961-01", dtype="datetime64[M]")
    fig, ax = plt.subplots()
    ax.plot(t, np.linspace(160, 175, t.size), color="C3", label=f"ACCESS-OM3 {RUN}")
    ax.plot(t, np.linspace(140, 145, t.size), color="C0", linewidth=0.3)                    # OM2 monthly
    ax.plot(t, np.linspace(141, 144, t.size), color="C0", label="ACCESS-OM2 025deg_jra55_iaf_omip2_cycle1")
    ax.plot(t, np.linspace(130, 135, t.size), color="C2", linewidth=0.3)                    # no labelled partner
    rec = linedata.capture(fig, run=RUN, notebook="StraitTransports", fignum=12, title="", caption="", png="x.png")
    lines = rec["panels"][0]["lines"]
    assert [(line["role"], line["label"]) for line in lines[1:]] == [
        ("other", "ACCESS-OM2 025deg_jra55_iaf_omip2_cycle1"), ("other", "ACCESS-OM2 025deg_jra55_iaf_omip2_cycle1"), ("other", "")]


def test_panels_without_a_legend_match_colours_across_the_figure():
    # SeaIce_Vol: four panels, the legend (labels) only on one of them
    t = np.arange("1958-01", "1961-01", dtype="datetime64[M]")
    fig, axs = plt.subplots(2, 2)
    for k, ax in enumerate(axs.flat):
        labelled = k == 1
        ax.plot(t, np.linspace(1, 2, t.size), color="C0", label=f"ACCESS-OM3: {RUN}" if labelled else None)
        ax.plot(t, np.linspace(2, 3, t.size), color="C1", label="ACCESS-OM2: 025deg_jra55_iaf_omip2_cycle6" if labelled else None)
        ax.set_title(f"panel {k}")
    rec = linedata.capture(fig, run=RUN, notebook="SeaIce_Vol", fignum=1, title="", caption="", png="SeaIce_Vol_01.png")
    assert [[line["role"] for line in p["lines"]] for p in rec["panels"]] == [["own", "other"]] * 4
    assert {p["lines"][1]["label"] for p in rec["panels"]} == {"ACCESS-OM2: 025deg_jra55_iaf_omip2_cycle6"}


def test_attributing_a_record_again_gives_the_same_roles_and_labels():
    t = np.arange("1958-01", "1961-01", dtype="datetime64[M]")
    fig, axs = plt.subplots(1, 2)
    for k, ax in enumerate(axs):
        ax.plot(t, np.linspace(1, 2, t.size), color="C0", label=f"ACCESS-OM3: {RUN}" if k else None)
        ax.plot(t, np.linspace(2, 3, t.size), color="C1", label="ACCESS-OM2: 025deg_jra55_iaf_omip2_cycle6" if k else None)
    rec = linedata.capture(fig, run=RUN, notebook="SeaIce_Vol", fignum=1, title="", caption="", png="x.png")
    first = [(line["role"], line["label"]) for p in rec["panels"] for line in p["lines"]]
    for p in rec["panels"]:
        for line in p["lines"]:
            line["role"] = None
    linedata._attribute(rec["panels"], RUN)
    assert [(line["role"], line["label"]) for p in rec["panels"] for line in p["lines"]] == first


def test_slowly_varying_values_keep_their_variation():
    # timeseries' masso: 1.38128e21 kg varying by ~1e16 was a staircase at 6 significant figures
    t = np.arange("1958-01", "1961-01", dtype="datetime64[M]")
    y = 1.38128e21 + 1e16 * np.sin(np.arange(t.size) / 3)
    fig, ax = plt.subplots()
    ax.plot(t, y, label=RUN)
    rec = linedata.capture(fig, run=RUN, notebook="timeseries", fignum=1, title="", caption="", png="t.png")
    got = np.array(rec["panels"][0]["lines"][0]["y"])
    assert len(set(got)) == t.size and np.allclose(got, y, rtol=0, atol=1e16 * 1e-5)


def test_annual_year_axes_panel_titles_from_text_and_zero_lines():
    # SeaIce_mass_budget_climatology: annual values against year numbers, titles drawn with ax.text above
    # the axes, an unlabelled axhline(0) and an analysis-period axvspan
    years = np.arange(1958, 1970)
    fig, ax = plt.subplots()
    ax.plot(years, np.linspace(3, 4, years.size), label="ACCESS-OM3")
    ax.axhline(0, color="k", lw=0.5)
    ax.axvspan(1965.5, 1969.5, color="0.9")
    ax.set_xlabel("Year")
    ax.text(0.0, 1.03, "a) Frazil ice growth", transform=ax.transAxes)
    ax.text(0.99, 1.03, "ANTARCTICA", ha="right", transform=ax.transAxes)
    rec = linedata.capture(fig, run=RUN, notebook="SeaIce_mass_budget_climatology", fignum=4, title="", caption="", png="b.png")
    p = rec["panels"][0]
    assert p["title"] == "a) Frazil ice growth · ANTARCTICA" and p["x_kind"] == "time"
    assert p["lines"][0]["x"][:2] == ["1958-01-01T00:00:00", "1959-01-01T00:00:00"]
    assert {"kind": "hline", "label": "", "y": 0.0} in p["marks"]
    assert {"kind": "span", "label": "", "x": ["1965-07-02T15:00:00", "1969-07-02T15:00:00"]} in p["marks"]


def test_an_observed_range_band_is_named_after_its_mean_line():
    t = np.arange("1958-01", "1961-01", dtype="datetime64[M]")
    fig, ax = plt.subplots()
    ax.plot(t, np.linspace(160, 175, t.size), label=RUN)
    ax.plot(ax.get_xlim(), [173.3, 173.3], "k--", label="Donohue et al. (2016)")
    ax.plot(ax.get_xlim(), [183.0, 183.0], "k:", alpha=0.2)
    ax.plot(ax.get_xlim(), [163.6, 163.6], "k:", alpha=0.2)
    rec = linedata.capture(fig, run=RUN, notebook="StraitTransports", fignum=12, title="", caption="", png="s.png")
    assert {"kind": "band", "label": "Donohue et al. (2016)", "y": [163.6, 183.0]} in rec["panels"][0]["marks"]


def test_year_axes_shared_in_a_grid_are_all_time():
    # SeaIce_mass_budget_climatology figure 4: 2x4 panels sharing x, "Year" only on the bottom row
    years = np.arange(1958, 1970)
    fig, axs = plt.subplots(2, 2, sharex=True)
    for ax in axs.flat:
        ax.plot(years, np.linspace(3, 4, years.size), label="ACCESS-OM3")
    for ax in axs[1]:
        ax.set_xlabel("Year")
    rec = linedata.capture(fig, run=RUN, notebook="SeaIce_mass_budget_climatology", fignum=4, title="", caption="", png="b.png")
    assert [p["x_kind"] for p in rec["panels"]] == ["time"] * 4


def test_hairline_monthly_values_are_thin():
    # StraitTransports: trans.plot(linewidth=0.3) behind the 12-month running mean
    t = np.arange("1958-01", "1961-01", dtype="datetime64[M]")
    fig, ax = plt.subplots()
    ax.plot(t, np.linspace(1, 2, t.size), color="C0", linewidth=0.3)
    ax.plot(t, np.linspace(1, 2, t.size), color="C0", linewidth=1.5, label=f"ACCESS-OM3 {RUN}")
    rec = linedata.capture(fig, run=RUN, notebook="StraitTransports", fignum=13, title="", caption="", png="s.png")
    assert [(line["role"], line["style"]) for line in rec["panels"][0]["lines"]] == [("own", "thin"), ("own", "line")]


def test_errorbar_series_are_named_with_their_error_bars_and_caps_skipped():
    # MeridionalHeatTransport: observational estimates drawn with ax.errorbar(..., label=...)
    lat = np.array([-30.0, -10.0, 24.0])
    fig, ax = plt.subplots()
    ax.plot(lat, [0.1, 0.2, 0.3], label=RUN)
    ax.errorbar(lat, [-0.9, -0.5, 1.3], yerr=[0.3, 0.2, 0.3], fmt="^", capsize=2, label="Ganachaud and Wunsch 2003")
    ax.errorbar(lat, [-1.0, -0.6, 1.5], yerr=0.2, fmt="o:", label="NCEP (Trenberth and Caron 2001)")
    rec = linedata.capture(fig, run=RUN, notebook="MeridionalHeatTransport", fignum=5, title="", caption="", png="m.png")
    lines = rec["panels"][0]["lines"]
    assert [(line["label"], line["style"], line["role"]) for line in lines] == [
        (RUN, "line", "own"), ("Ganachaud and Wunsch 2003", "points", "reference"), ("NCEP (Trenberth and Caron 2001)", "line", "other")]
    assert lines[1]["yerr"] == [[-1.2, -0.6], [-0.7, -0.3], [1.0, 1.6]]


def test_errorbars_with_gaps_and_single_points_keep_their_line_data():
    # MeridionalHeatTransport's Trenberth & Caron (2001) series have NaN gaps; some estimates are one point
    lat = np.array([-30.0, -10.0, 10.0, 24.0])
    fig, ax = plt.subplots()
    ax.plot(lat, [0.1, 0.2, 0.3, 0.4], label=RUN)
    ax.errorbar(lat, [-0.9, np.nan, 0.5, 1.3], yerr=[0.3, 0.2, np.nan, 0.3], fmt="s:", capsize=2, label="NCEP (Trenberth and Caron 2001)")
    ax.errorbar(24, 2.0, yerr=0.3, fmt="x", capsize=2, label="Bryden et al. 1991")
    rec = linedata.capture(fig, run=RUN, notebook="MeridionalHeatTransport", fignum=5, title="", caption="", png="m.png")
    ncep, bryden = rec["panels"][0]["lines"][1:]
    assert ncep["label"].startswith("NCEP") and ncep["yerr"] == [[-1.2, -0.6], None, None, [1.0, 1.6]]
    assert bryden["label"] == "Bryden et al. 1991" and bryden["yerr"] == [[1.7, 2.3]]
