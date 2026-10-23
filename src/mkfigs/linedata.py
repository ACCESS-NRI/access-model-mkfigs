"""Capture the line data a saved figure draws, for the access-om3-live figure explorer.

``MkmdWriter.savefig`` calls :func:`capture` on every figure it saves and writes
the record next to the PNG as ``<notebook>_<NN>.lines.json``. The record holds
exactly what was plotted, read back from the matplotlib figure, so no notebook
changes are needed:

* every line's values, label, colour and style;
* reference marks: ``axhline``/``axvline`` and constant two-point lines (a pair of
  unlabelled ones in one colour is a range: a band), and ``axhspan``/``axvspan``
  bands and periods;
* panel titles and axis labels, with run names and date ranges stripped out for
  the figure key but kept, alongside the originals, as metadata for display.

Each line gets a role. A line is the run's **own** when its label contains the
run's name (see :func:`run_name`), or when it is unlabelled and drawn in the same
colour as an own line on the same axes (the faint monthly values behind a rolling
mean); an unlabelled ``other`` line takes the label of the one labelled line in its
colour on the same axes. When the legend is on another panel, a panel where no
line names the run matches the run's colours across the whole figure (and a
panel with no labels takes the figure's labels). In a figure where no label names the run, every line is taken as own and
the record says so (``status: "all-lines-own"``). Marker-only lines are
observations (``reference``), and everything else is ``other`` (other runs, OM2,
CM3), which the explorer resolves against the Experiments table. Setting
``gid="reference"``, ``gid="own"`` or ``gid="other"`` on a line overrides all of this.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
from pathlib import Path

import numpy as np
from matplotlib import dates as mdates
from matplotlib.colors import to_hex
from matplotlib.container import ErrorbarContainer
from matplotlib.patches import Polygon, Rectangle

SCHEMA = 1
GENERIC_PARENT_DIRS = ("archive", "cm3-datastore")
SIG_FIGS = 6
ROLES = ("own", "other", "reference")

_DATE = r"\d{4}(?:-\d{2}(?:-\d{2})?)?"
_PERIOD = re.compile(
    rf"({_DATE})\s*(?:-|–|—|to)\s*({_DATE})(?:\s+(mean|maximum|minimum|average|climatology|max|min))?",
    re.IGNORECASE,
)


def run_name(datastore_path: str) -> str:
    """Name of the run a datastore belongs to.

    The datastore's folder, skipping generic catalogue folders, so
    ``<run>/datastore.json`` and ``<run>/archive/experiment_datastore.json`` both
    give ``<run>``. Same rule as access-om3-paper-1's ``get_exptname_from_path``,
    which builds the labels this module matches against.
    """
    parts = os.path.normpath(str(datastore_path)).split(os.sep)
    name = parts[-2] if len(parts) >= 2 else parts[-1]
    if name in GENERIC_PARENT_DIRS and len(parts) >= 3:
        name = parts[-3]
    return name


def split_title(text: str, run: str | None = None) -> tuple[str, list[str]]:
    """Return ``(title without run name or date ranges, date ranges found)``."""
    text = text or ""
    periods = [re.sub(r"\s+", " ", m.group(0)).strip() for m in _PERIOD.finditer(text)]
    clean = _PERIOD.sub(" ", text)
    if run:
        clean = clean.replace(run, " ")
    clean = re.sub(r"\s+", " ", clean).strip()
    clean = re.sub(r"(\s+(in|for|of|from))+$", "", clean).strip(" -–—:,;")
    return clean, periods


def _label(artist) -> str:
    label = artist.get_label() or ""
    return "" if label.startswith("_") else label


def _colour(artist) -> str | None:
    try:
        return to_hex(artist.get_color(), keep_alpha=False)
    except (ValueError, TypeError, AttributeError):
        return None


def _values(values) -> tuple[list, str, str | None]:
    """JSON-ready values, the axis kind ("time" or "number") and any cftime calendar."""
    if getattr(values, "dtype", None) is not None and values.dtype.kind == "M":
        # datetime64[ns] cast to object gives ints (datetime.datetime cannot hold ns); [us] gives datetimes
        values = np.asarray(values).astype("datetime64[us]")
    arr = np.asarray(values, dtype=object).ravel()
    first = next((v for v in arr if v is not None and not (isinstance(v, float) and np.isnan(v))), None)
    if isinstance(first, (np.datetime64, dt.date)) or hasattr(first, "calendar"):
        out = []
        for v in arr:
            if v is None or (isinstance(v, np.datetime64) and np.isnat(v)):
                out.append(None)
                continue
            if isinstance(v, np.datetime64):
                v = v.astype("datetime64[s]").item()
            if isinstance(v, dt.date) and not isinstance(v, dt.datetime):
                v = dt.datetime(v.year, v.month, v.day)
            out.append(v.isoformat()[:19] if hasattr(v, "isoformat") else str(v)[:19])
        return out, "time", getattr(first, "calendar", None)
    try:
        nums = np.asarray(values, dtype=float).ravel()
    except (TypeError, ValueError):
        return [None if v is None else str(v) for v in arr], "category", None
    # SIG_FIGS significant figures of the values' own variation, not of the values: a global mean such as the
    # ocean's mass (1.38128e21 kg, varying by ~1e16) would otherwise be a staircase of a few levels
    finite = nums[np.isfinite(nums)]
    digits = SIG_FIGS
    if finite.size > 1 and (span := float(finite.max() - finite.min())) > 0:
        digits += max(0, min(9, int(np.ceil(np.log10(float(np.abs(finite).max()) / span)))))
    return [float(f"{v:.{digits}g}") if np.isfinite(v) else None for v in nums], "number", None


def _date_axis(ax, which: str) -> bool:
    axis = ax.xaxis if which == "x" else ax.yaxis
    converter = axis.get_converter() if hasattr(axis, "get_converter") else getattr(axis, "converter", None)
    return converter is not None and "Date" in type(converter).__name__


def _axis_values(ax, which: str, values):
    """Convert numbers stored in axis units (as patches keep them) back to dates where possible."""
    if _date_axis(ax, which):
        return [mdates.num2date(v).replace(tzinfo=None).isoformat()[:19] if v is not None else None for v in values]
    return [float(f"{v:.{SIG_FIGS}g}") for v in values]


def _blended(artist) -> bool:
    """True for artists drawn partly in axes units (axhline, axvspan, ...).

    Patches report their full transform (patch + data) from get_transform();
    the one they were drawn with is get_data_transform().
    """
    tr = artist.get_data_transform() if hasattr(artist, "get_data_transform") else artist.get_transform()
    return type(tr).__name__.startswith("Blended")


def _errorbars(ax):
    """ax.errorbar series: the label sits on the container, not its line, and the caps are separate lines.
    Returns {id(data line): (label, [[y_lo, y_hi] per point] or None)} and the ids of the cap lines."""
    series, caps = {}, set()
    for c in ax.containers:
        if not isinstance(c, ErrorbarContainer) or c.lines[0] is None:
            continue
        data_line, cap_lines, bar_cols = c.lines
        caps |= {id(line) for line in cap_lines}
        yerr = None
        try:  # the y bars are the last collection; points with NaN values have no bar, so match bars to points by x
            if c.has_yerr and bar_cols:
                bars = {}
                for seg in bar_cols[-1].get_segments():
                    xy = np.asarray(seg, dtype=float).reshape(-1, 2)
                    if len(xy) >= 2 and np.isfinite(xy).all():
                        bars[float(xy[0, 0])] = [float(xy[:, 1].min()), float(xy[:, 1].max())]
                xs = np.atleast_1d(np.asarray(data_line.get_xdata(), dtype=float))
                yerr = [bars.get(float(x)) for x in xs] if bars else None
        except (TypeError, ValueError):  # keep the series and its label without error bars
            yerr = None
        series[id(data_line)] = (_label(c), yerr)
    return series, caps


def _marks_and_lines(ax):
    lines, marks = [], []
    errorbars, caps = _errorbars(ax)
    years = False  # an x axis of plain year numbers, read as dates: its spans and vlines are years too
    for line in ax.get_lines():
        if not line.get_visible():
            continue
        if id(line) in caps:
            continue
        x, y = line.get_xdata(orig=True), line.get_ydata(orig=True)
        label, yerr = errorbars.get(id(line), (_label(line), None))
        if _blended(line) and len(x) == 2:  # axhline / axvline
            xs, ys = np.asarray(x, dtype=object), np.asarray(y, dtype=object)
            if list(xs) == [0, 1] and ys[0] == ys[1]:
                marks.append({"kind": "hline", "label": label, "y": _values([ys[0]])[0][0], "_color": _colour(line)})
            elif list(ys) == [0, 1] and xs[0] == xs[1]:
                marks.append({"kind": "vline", "label": label, "x": _values([xs[0]])[0][0]})
            continue
        xv, x_kind, calendar = _values(x)
        yv, _, _ = _values(y)
        if x_kind == "number" and _date_axis(ax, "x"):  # e.g. plot(ax.get_xlim(), ...) on a date axis
            xv, x_kind = _axis_values(ax, "x", [float(v) if np.isfinite(v) else None for v in np.asarray(x, dtype=float).ravel()]), "time"
        elif x_kind == "number" and _year_axis(ax, xv):  # annual values plotted against year numbers
            xv, x_kind, years = [_year_date(v) for v in xv], "time", True
        markers_only = line.get_linestyle() in ("None", "none", " ", "") and line.get_marker() not in (None, "None", "none", "", " ")
        if len(xv) == 2 and yv[0] == yv[1] and not markers_only:  # constant obs line (or range bound) drawn with plot()
            marks.append({"kind": "hline", "label": label, "y": yv[0], "_color": _colour(line)})
            continue
        if not xv:
            continue
        alpha = line.get_alpha()
        # faint or hairline lines (alpha < 0.8, or linewidth < 0.6 as StraitTransports' monthly values) are "thin"
        style = "points" if markers_only else ("thin" if alpha is not None and alpha < 0.8 or line.get_linewidth() < 0.6 else "line")
        lines.append({"label": label, "role": None, "style": style, "color": _colour(line), "x": xv, "y": yv,
                      "_gid": line.get_gid(), "_x_kind": x_kind, "_calendar": calendar,
                      **({"yerr": [[float(f"{v:.{SIG_FIGS}g}") for v in pair] if pair else None for pair in yerr]}
                         if yerr and len(yerr) == len(yv) else {})})
    for patch in ax.patches:
        if not (_blended(patch) and isinstance(patch, (Rectangle, Polygon))):
            continue
        verts = patch.get_xy() if isinstance(patch, Polygon) else None
        if isinstance(patch, Rectangle):
            x0, y0, w, h = patch.get_x(), patch.get_y(), patch.get_width(), patch.get_height()
        else:
            xs, ys = verts[:, 0], verts[:, 1]
            x0, y0, w, h = xs.min(), ys.min(), xs.max() - xs.min(), ys.max() - ys.min()
        if np.isclose(w, 1.0) and np.isclose(x0, 0.0):  # axhspan: x in axes units, y in data units
            marks.append({"kind": "band", "label": _label(patch), "y": _axis_values(ax, "y", [y0, y0 + h])})
        elif np.isclose(h, 1.0) and np.isclose(y0, 0.0):  # axvspan
            marks.append({"kind": "span", "label": _label(patch), "x": _axis_values(ax, "x", [x0, x0 + w])})
    if years:
        for m in marks:
            if m["kind"] in ("span", "vline"):
                m["x"] = [_year_date(v) for v in m["x"]] if m["kind"] == "span" else _year_date(m["x"])
    # two unlabelled constant lines in one colour are a range's bounds (e.g. an observed range): one band
    # (named after the labelled line in that colour, e.g. the observed mean, when there is one)
    bounds, named = {}, {}
    for m in marks:
        if m["kind"] == "hline" and m.get("_color"):
            (named if m["label"] else bounds).setdefault(m["_color"], []).append(m)
    for colour, pair in bounds.items():
        if len(pair) == 2:
            marks = [m for m in marks if m not in pair]
            label = named[colour][0]["label"] if len(named.get(colour, [])) == 1 else ""
            marks.append({"kind": "band", "label": label, "y": sorted(m["y"] for m in pair)})
    for m in marks:
        m.pop("_color", None)
    return lines, marks


def _year_axis(ax, xv) -> bool:
    """An x axis of plain year numbers (annual values), labelled as years (on this axes or, in a grid with a
    shared x axis, on the axes that carries the label)."""
    nums = [v for v in xv if v is not None]
    labels = {a.get_xlabel().strip().lower() for a in ax.get_shared_x_axes().get_siblings(ax)} | {ax.get_xlabel().strip().lower()}
    return bool(labels & {"year", "years", "time (years)"}) and bool(nums) and all(1000 <= v < 3000 for v in nums)


def _year_date(v):
    if v is None:
        return None
    year = int(v)
    return (dt.datetime(year, 1, 1) + dt.timedelta(days=(v - year) * 365.25)).isoformat()[:19]


def _panel_title(ax) -> str:
    """The panel's title: centre, left and right titles, and text drawn just above the axes in axes
    coordinates (as notebooks do for "a) ..." and region labels), left to right, joined with " · "."""
    parts = [(0.0, ax.get_title(loc="left")), (0.5, ax.get_title()), (1.0, ax.get_title(loc="right"))]
    for t in ax.texts:
        x, y = t.get_position()
        if t.get_transform() is ax.transAxes and y >= 1 and t.get_visible():
            parts.append((x, t.get_text()))
    return " · ".join(text.strip() for _, text in sorted(parts, key=lambda p: p[0]) if text and text.strip())


def _has_content(ax) -> bool:
    return bool(ax.get_lines() or ax.collections or ax.images or ax.patches or ax.texts)


def _attribute(panels: list[dict], run: str) -> str:
    """Give every line a role; return the figure status."""
    def names_run(line):
        return bool(line["label"]) and run in line["label"]

    def colour_maps(lines):
        own = {line["color"] for line in lines if names_run(line)}
        labels = {}
        for line in lines:
            if line["label"]:
                labels.setdefault(line["color"], set()).add(line["label"])
        return own, labels

    any_named = any(names_run(line) for p in panels for line in p["lines"])
    # The figure's legend may be on another panel: a panel where no line names the run takes the run's colours
    # from the whole figure, and a panel with no labels takes the labels from it too. (Inherited labels never
    # name the run, so attributing a record again gives the same roles.)
    figure_own, figure_labels = colour_maps([line for p in panels for line in p["lines"]])
    for p in panels:
        own_colours, labels = colour_maps(p["lines"])
        if not any(names_run(line) for line in p["lines"]):
            own_colours = figure_own
        labels = labels or figure_labels
        for line in p["lines"]:
            gid = line.pop("_gid", None)
            if gid in ROLES:
                line["role"] = gid
            elif names_run(line) or not line["label"] and line["color"] in own_colours:
                line["role"] = "own"
            elif line["style"] == "points":
                line["role"] = "reference"
            elif not any_named:
                line["role"] = "own"
            else:
                line["role"] = "other"
                if not line["label"] and len(labels.get(line["color"], ())) == 1:
                    # e.g. another run's or OM2's monthly values behind its labelled running mean
                    line["label"] = next(iter(labels[line["color"]]))
    n_lines = sum(len(p["lines"]) for p in panels)
    n_own = sum(1 for p in panels for line in p["lines"] if line["role"] == "own")
    if n_lines == 0:
        return "no-lines"
    if n_own == 0:
        return "no-own-lines"
    return "ok" if any_named else "all-lines-own"


def figure_key(notebook: str, suptitle: str, descriptors: list[str], seen: set | None) -> str:
    """Stable key: notebook, figure title and panel titles; a counter only on a clash."""
    base = "|".join([notebook, suptitle, " / ".join(descriptors)])
    key, n = base, 2
    while seen is not None and key in seen:
        key, n = f"{base}#{n}", n + 1
    if seen is not None:
        seen.add(key)
    return key


def capture(figure, *, run: str, notebook: str, fignum: int, title: str, caption: str, png: str,
            seen: set | None = None) -> dict:
    """Read a figure's line data and metadata into a JSON-ready record."""
    sup = getattr(figure, "_suptitle", None)
    sup_full = sup.get_text() if sup is not None else ""
    sup_clean, sup_periods = split_title(sup_full, run)
    title_clean, title_periods = split_title(title, run)
    panels = []
    content = False
    for ax in figure.get_axes():
        content = content or _has_content(ax)
        lines, marks = _marks_and_lines(ax)
        if not lines and not marks:
            continue
        full = _panel_title(ax)
        clean, periods = split_title(full, run)
        kinds = {line.pop("_x_kind") for line in lines}
        calendars = {c for c in (line.pop("_calendar") for line in lines) if c}
        panels.append({
            "title": clean, "title_full": full, "periods": periods,
            "x_label": ax.get_xlabel(), "y_label": ax.get_ylabel(),
            "x_kind": "time" if "time" in kinds else ("category" if kinds == {"category"} else "number"),
            "x_calendar": calendars.pop() if len(calendars) == 1 else None,
            "y_log": ax.get_yscale() == "log",
            "lines": lines, "marks": marks,
        })
    status = _attribute(panels, run) if panels else ("no-lines" if content else "empty")
    descriptors = [p["title"] or split_title(p["y_label"], run)[0] for p in panels]
    return {
        "schema": SCHEMA,
        "key": figure_key(notebook, sup_clean, descriptors, seen) if panels else None,
        "notebook": notebook, "fignum": fignum, "png": png, "run": run,
        "title": title, "title_clean": title_clean, "caption": caption,
        "suptitle": sup_clean, "suptitle_full": sup_full,
        "periods": sorted(set(title_periods + sup_periods + [x for p in panels for x in p["periods"]])),
        "status": status, "panels": panels,
    }


def summary(record: dict) -> str:
    """One console line per figure, so problems show up in the job log."""
    lines = [line for p in record["panels"] for line in p["lines"]]
    roles = {r: sum(1 for line in lines if line["role"] == r) for r in ROLES}
    marks = sum(len(p["marks"]) for p in record["panels"])
    flag = "" if record["status"] in ("ok", "no-lines") else "  <-- check"
    return (f"[linedata] {Path(record['png']).stem}: {record['status']}, {len(record['panels'])} panel(s), "
            f"{roles['own']} own, {roles['other']} other, {roles['reference']} reference line(s), {marks} mark(s){flag}")


def write(record: dict, path) -> None:
    Path(path).write_text(json.dumps(record, separators=(",", ":"), ensure_ascii=False))
