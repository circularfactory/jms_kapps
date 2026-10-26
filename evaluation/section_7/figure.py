"""The §7 figures: the commit latency of KAPPS against the writes-only replay, from recorded run
folders of the measurement harness.

    uv run python evaluation/section_7/figure.py <folder> [<folder> ...] [--output <folder>]

Each folder is a store folder of the experiment (`<store>/heartbeats-<level>/runs/<run id>/`), a
bare runs folder or a single run folder. The outputs go to `--output`, by default `figures/`
beside the first folder. The script reads the runs, builds the two tidy tables of the harness
(`jms_kapps.evaluation.commit_table`), writes them, and then calls each figure function of
`FIGURES` in turn. Each figure function takes the two tables and the output folder, writes its
files, and returns what it made, the figures it drew and the plots it skipped with why. The
manifest `figures.json` lists all of it.

What is plotted (the method of the §7 spec):

- The KAPPS curves: every admitted commit of the window of every run that counts, heartbeat
  commits included; the latency is the commit span's duration.
- The writes-only curve: the replayed checked write of each of those commits of the SHACL-on arm,
  from its run's writes-only replay; a commit whose write was recorded after the window's end has
  no replayed write and is counted in the manifest.
- Every interval is a bootstrap over whole repetitions (seed 0, 10 000 resamples); the SHACL
  difference resamples each arm's repetitions independently.

Every value that sets how a figure looks is a named constant below.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import matplotlib
import pandas as pd

matplotlib.use("Agg")

from matplotlib.artist import Artist
from matplotlib.axes import Axes
from matplotlib.figure import Figure

from jms_kapps.evaluation import commit_table, stats
from jms_kapps.evaluation.commit_table import (
    KAPPS_SHACL_OFF,
    KAPPS_SHACL_ON,
    PHASES,
    WRITES_ONLY,
    phase_column,
)
from jms_kapps.evaluation.table import (
    DEFAULT_RESAMPLES,
    DEFAULT_SEED,
    LATENCY_QUANTILES,
)

# -- what the figures look like ------------------------------------------------------------------

# One column of the journal: 8.5 cm wide, 8 pt fonts, 1 pt lines; the panels side by side.
COLUMN_WIDTH_CM = 8.5
FIGURE_HEIGHT_CM = 6.0
FONT_POINTS = 8
SMALL_FONT_POINTS = 6
LINE_POINTS = 1.0
MARKER_POINTS = 3
CM_PER_INCH = 2.54
DPI = 300
INK = "black"
GUIDE = "grey"

# The three curves: the arm, its label, its line style and its marker, so that greyscale tells
# them apart.
CURVES = (
    (WRITES_ONLY, "writes only", "-", "o"),
    (KAPPS_SHACL_OFF, "KAPPS, SHACL off", "--", "s"),
    (KAPPS_SHACL_ON, "KAPPS, SHACL on", "-.", "^"),
)
MARKERS_PER_CURVE = 8
# The CDF's legend stands below its panels, out of the axes, so that it covers no curve; a curve's
# label gives its n for each store, in the order of the panels. The figure is taller by the
# legend's three rows.
CDF_HEIGHT_CM = 7.5
STORE_SHORT = {"kit": "KIT", "local": "local"}

# The axes.
LATENCY_LABEL = "time per commit (ms)"
SHARE_LABEL = "share of commits"
MS_PER_SECOND = 1000

# The module transit time: a dashed line, labelled on the line (no legend entry), on every axis
# that shows a latency where the axis reaches 3 s (`transit_line`).
TRANSIT_MS = 3000
TRANSIT_LABEL = "module transit time (3 s)"
TRANSIT_GID = "module transit time"
# The label of a line across a panel too narrow for the long one.
TRANSIT_SHORT_LABEL = "transit (3 s)"
# How far the label stands from the line, in points.
TRANSIT_LABEL_OFFSET_POINTS = 2
# Where a vertical line's label goes right of the line, the axis ends this many times 3 s, to make
# room for it.
TRANSIT_ROOM = 2.0

# The box plot's whiskers: the 1st and the 99th percentile (interim choice); the commits beyond
# them are small hollow points; the mean is a diamond.
WHISKERS = (1, 99)
BOX_WIDTH = 0.6
MEAN_MARKER = "D"
OUTLIER_POINTS = 2
OUTLIER_ALPHA = 0.3

# The panels, left to right; a store of another name comes after them.
STORE_ORDER = ("kit", "local")
STORE_TITLES = {"kit": "KIT GraphDB", "local": "local GraphDB"}

# The bootstrap, as the harness's table draws it.
SEED = DEFAULT_SEED
RESAMPLES = DEFAULT_RESAMPLES

# The files the script writes.
COMMITS_CSV = "commits.csv"
REPLAYED_WRITES_CSV = "replayed_writes.csv"
PERCENTILES_CSV = "percentiles.csv"
PHASES_CSV = "phases.csv"
PHASES_MD = "phases.md"
MANIFEST = "figures.json"


@dataclass
class Made:
    """What one figure function made: the files it wrote, the figures it drew, the plots it
    skipped with why, and the caption draft of each figure, by its file."""

    files: list[str] = field(default_factory=list)
    figures: list[Figure] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    captions: dict[str, str] = field(default_factory=dict)


# -- the values the figures read -----------------------------------------------------------------


def stores_of(commits: pd.DataFrame) -> list[str]:
    """The stores of `commits`, in the order of the panels."""
    found = set(commits["store"])
    return [s for s in STORE_ORDER if s in found] + sorted(found - set(STORE_ORDER))


def levels_of(commits: pd.DataFrame) -> list[str]:
    """The heartbeat levels of `commits`: on, then off."""
    found = set(commits["heartbeats"])
    return [level for level in ("on", "off") if level in found]


def admitted_in_window(commits: pd.DataFrame, store: str, level: str) -> pd.DataFrame:
    """The commits of `store` at heartbeat `level` that were admitted and started in the window:
    the commit set of every curve."""
    return commits[
        (commits["store"] == store)
        & (commits["heartbeats"] == level)
        & commits["admitted"]
        & commits["in_window"]
    ]


def curve_values(
    commits: pd.DataFrame, store: str, level: str, arm: str
) -> list[list[float]]:
    """The seconds of the curve of `arm` at `store` and heartbeat `level`, one list for each
    repetition: a KAPPS arm's admitted commits of the window, by run; the writes-only arm's
    replayed checked writes of the SHACL-on arm's commits, by replay."""
    here = admitted_in_window(commits, store, level)
    if arm == WRITES_ONLY:
        paired = here[
            (here["arm"] == KAPPS_SHACL_ON) & here["replayed_update_seconds"].notna()
        ]
        groups = paired.groupby("replay_run_id")["replayed_update_seconds"]
    else:
        groups = here[here["arm"] == arm].groupby("run_id")["seconds"]
    return [[float(value) for value in values] for _, values in groups]


def paired_differences(
    commits: pd.DataFrame, store: str, level: str
) -> list[list[float]]:
    """KAPPS with SHACL on minus writes only, commit by commit, at `store` and `level`, one list
    for each repetition."""
    here = admitted_in_window(commits, store, level)
    here = here[(here["arm"] == KAPPS_SHACL_ON) & here["difference_seconds"].notna()]
    return [
        [float(value) for value in values]
        for _, values in here.groupby("run_id")["difference_seconds"]
    ]


def pooled(repetitions: Sequence[Sequence[float]]) -> list[float]:
    """The values of all `repetitions` in one list."""
    return [value for repetition in repetitions for value in repetition]


def in_ms(seconds: Sequence[float]) -> list[float]:
    """`seconds` in milliseconds, as the figures' axes show them."""
    return [value * MS_PER_SECOND for value in seconds]


def _figure(
    panels: int, height_cm: float = FIGURE_HEIGHT_CM
) -> tuple[Figure, list[Axes]]:
    """A figure one column wide with `panels` panels side by side, sharing their axes."""
    figure = Figure(
        figsize=(COLUMN_WIDTH_CM / CM_PER_INCH, height_cm / CM_PER_INCH),
        layout="constrained",
    )
    axes = figure.subplots(1, panels, sharex=True, sharey=True, squeeze=False)
    return figure, list(axes[0])


def transit_line(
    ax: Axes,
    at: float,
    axis: str = "x",
    side: str = "left",
    text: str = TRANSIT_LABEL,
) -> None:
    """The module transit time on `ax`: a dashed line with its label on the line.

    `at` is 3 s in the unit of the axis: `TRANSIT_MS` on an axis in ms, `TRANSIT_MS /
    MS_PER_SECOND` on one in s. With `axis="x"` the line is vertical and the label stands
    upright beside its top, on the `side` of it ("left" or "right"; for "right" the caller makes
    room, see `TRANSIT_ROOM`); with `axis="y"` the line is horizontal and the label stands under
    its middle, inside the axes (above the line it ran into the frame where 3 s is near the top,
    and at an end it met the guides of a window's start and end). These places lie beyond the
    slowest commit in the latency figures, so the label covers no data. `text` is the label,
    `TRANSIT_SHORT_LABEL` for a narrow panel. The line and its label carry `TRANSIT_GID`, and the
    line is no legend entry: the label says what it is.
    """
    if axis == "x":
        line = ax.axvline(at, linestyle="--", color=GUIDE, label="_transit")
        offset = (
            -TRANSIT_LABEL_OFFSET_POINTS
            if side == "left"
            else TRANSIT_LABEL_OFFSET_POINTS
        )
        label = ax.annotate(
            text,
            xy=(at, 1),
            xycoords=ax.get_xaxis_transform(),
            xytext=(offset, -TRANSIT_LABEL_OFFSET_POINTS),
            textcoords="offset points",
            rotation=90,
            horizontalalignment="right" if side == "left" else "left",
            verticalalignment="top",
            fontsize=SMALL_FONT_POINTS,
            color=INK,
        )
    else:
        line = ax.axhline(at, linestyle="--", color=GUIDE, label="_transit")
        label = ax.annotate(
            text,
            xy=(0.5, at),
            xycoords=ax.get_yaxis_transform(),
            xytext=(0, -TRANSIT_LABEL_OFFSET_POINTS),
            textcoords="offset points",
            horizontalalignment="center",
            verticalalignment="top",
            fontsize=SMALL_FONT_POINTS,
            color=INK,
        )
    line.set_gid(TRANSIT_GID)
    label.set_gid(TRANSIT_GID)


def _panel(ax: Axes, store: str, transit_side: str = "left") -> None:
    """What every panel has: the module transit time, the log scale, the store as its title and
    the latency axis. With `transit_side="right"` the transit time's label stands right of the
    line; the caller then lets the axis end at `TRANSIT_ROOM` times 3 s, once all panels are
    drawn."""
    transit_line(ax, TRANSIT_MS, side=transit_side)
    ax.set_xscale("log")
    ax.set_title(STORE_TITLES.get(store, store))
    ax.set_xlabel(LATENCY_LABEL)


def _save(figure: Figure, output: Path, name: str, made: Made) -> None:
    for suffix in (".pdf", ".png"):
        path = output / f"{name}{suffix}"
        figure.savefig(path, dpi=DPI)
        made.files.append(path.name)
    made.figures.append(figure)


# -- the figures ---------------------------------------------------------------------------------


def cdf_figure(commits: pd.DataFrame, replayed: pd.DataFrame, output: Path) -> Made:
    """The CDF of the commit latency, once for each heartbeat level: a panel for each store with
    the same axes, KIT left, the three curves, and the module transit time. The legend stands
    below the panels and gives each curve's n for each store."""
    made = Made()
    for level in levels_of(commits):
        stores = stores_of(commits[commits["heartbeats"] == level])
        with matplotlib.rc_context(
            {"font.size": FONT_POINTS, "lines.linewidth": LINE_POINTS}
        ):
            figure, axes = _figure(len(stores), CDF_HEIGHT_CM)
            counts = []
            # Each curve's n, by its label, one entry for each store it has a curve in; and
            # the first line drawn of each curve, which the legend shows.
            n_of: dict[str, list[str]] = {label: [] for _, label, _, _ in CURVES}
            line_of: dict[str, Artist] = {}
            for ax, store in zip(axes, stores, strict=True):
                for arm, label, style, marker in CURVES:
                    values = sorted(pooled(curve_values(commits, store, level, arm)))
                    if not values:
                        made.skipped.append(
                            f"CDF, heartbeats {level}, {store}: no {label} curve: {_why_none(arm)}"
                        )
                        continue
                    shares = [(rank + 1) / len(values) for rank in range(len(values))]
                    [line] = ax.plot(
                        in_ms(values),
                        shares,
                        linestyle=style,
                        marker=marker,
                        markevery=max(1, len(values) // MARKERS_PER_CURVE),
                        markersize=MARKER_POINTS,
                        color=INK,
                        label=label,
                    )
                    counts.append(f"{store} {label}: n = {len(values)}")
                    n_of[label].append(f"{len(values)} {STORE_SHORT.get(store, store)}")
                    line_of.setdefault(label, line)
                _panel(ax, store)
                ax.set_ylim(0, 1)
            axes[0].set_ylabel(SHARE_LABEL)
            # One legend below both panels, out of the axes: inside a panel at column width it
            # covered the lower part of every curve.
            handles = []
            labels = []
            for _, label, _, _ in CURVES:
                if label not in line_of:
                    continue
                handles.append(line_of[label])
                labels.append(f"{label} (n = {', '.join(n_of[label])})")
            if handles:
                figure.legend(
                    handles,
                    labels,
                    loc="outside lower center",
                    fontsize=SMALL_FONT_POINTS,
                )
            name = f"commit-latency-cdf-heartbeats-{level}"
            _save(figure, output, name, made)
            made.captions[name] = (
                f"Commit latency, heartbeats {level}: the share of commits that took at most "
                "the time on the x axis (log scale), for the writes-only replay of the SHACL-on "
                "runs, KAPPS with SHACL off and KAPPS with SHACL on; every admitted commit of the "
                "measured window of every run that counts, heartbeat commits included. The "
                f"dashed line marks the module transit time (3 s). {'; '.join(counts)}."
            )
    return made


def box_figure(commits: pd.DataFrame, replayed: pd.DataFrame, output: Path) -> Made:
    """The box plot of the commit latency, once for each heartbeat level: a panel for each store,
    KIT left, the three arms as horizontal boxes on a log scale, each with its n and the share of
    its commits beyond the whiskers."""
    made = Made()
    labels = [label for _, label, _, _ in CURVES]
    for level in levels_of(commits):
        stores = stores_of(commits[commits["heartbeats"] == level])
        with matplotlib.rc_context(
            {"font.size": FONT_POINTS, "lines.linewidth": LINE_POINTS}
        ):
            figure, axes = _figure(len(stores))
            for ax, store in zip(axes, stores, strict=True):
                for position, (arm, label, _, _) in enumerate(CURVES, start=1):
                    values = in_ms(pooled(curve_values(commits, store, level, arm)))
                    if not values:
                        made.skipped.append(
                            f"box plot, heartbeats {level}, {store}: no {label} box: "
                            f"{_why_none(arm)}"
                        )
                        continue
                    low = stats.quantile(values, WHISKERS[0] / 100)
                    high = stats.quantile(values, WHISKERS[1] / 100)
                    assert low is not None and high is not None
                    outside = [v for v in values if v < low or v > high]
                    ax.boxplot(
                        [values],
                        positions=[position],
                        orientation="horizontal",
                        whis=WHISKERS,
                        showmeans=True,
                        showfliers=False,
                        widths=BOX_WIDTH,
                        medianprops={"color": INK},
                        meanprops={
                            "marker": MEAN_MARKER,
                            "markerfacecolor": INK,
                            "markeredgecolor": INK,
                            "markersize": MARKER_POINTS,
                        },
                    )
                    ax.plot(
                        outside,
                        [position] * len(outside),
                        linestyle="none",
                        marker="o",
                        markersize=OUTLIER_POINTS,
                        markerfacecolor="none",
                        markeredgecolor=INK,
                        alpha=OUTLIER_ALPHA,
                    )
                    # n and the share beyond the whiskers, above the box, on two short lines
                    # that end well before the transit time's line and label, and out of the
                    # layout: in it, the note squeezed each panel to a sliver.
                    note = ax.text(
                        0.02,
                        position + BOX_WIDTH / 2,
                        f"n = {len(values)}, {len(outside) / len(values):.1%}\nbeyond whiskers",
                        transform=ax.get_yaxis_transform(),
                        fontsize=SMALL_FONT_POINTS,
                        verticalalignment="bottom",
                    )
                    note.set_in_layout(False)
                # The transit time's label goes right of the line: left of it, the notes are.
                _panel(ax, store, transit_side="right")
            # Room right of the transit line for its label, set once after every panel is drawn:
            # set in the loop, it froze the shared axis at the first panel's data and cut the
            # next panel's faster boxes at the left frame.
            axes[0].set_xlim(right=TRANSIT_MS * TRANSIT_ROOM)
            axes[0].set_yticks(range(1, len(CURVES) + 1), labels)
            axes[0].set_ylim(0.5, len(CURVES) + 0.9)
            # One latency label under both panels: each panel's own ran into the next.
            for ax in axes:
                ax.set_xlabel("")
            figure.supxlabel(LATENCY_LABEL, fontsize=FONT_POINTS)
            name = f"commit-latency-box-heartbeats-{level}"
            _save(figure, output, name, made)
            made.captions[name] = (
                f"Commit latency, heartbeats {level}, log scale: the box is the interquartile "
                "range, the line the median, the diamond the mean; the whiskers reach the "
                f"{WHISKERS[0]}st and the {WHISKERS[1]}th percentile, and every commit beyond "
                "them is a hollow point. The dashed line marks the module transit time (3 s)."
            )
    return made


PERCENTILE_COLUMNS = (
    "store",
    "heartbeats",
    "measure",
    "curve",
    "statistic",
    "n",
    "repetitions",
    "value_seconds",
    "interval_low_seconds",
    "interval_high_seconds",
)


def percentiles_csv(
    commits: pd.DataFrame, replayed: pd.DataFrame, output: Path
) -> Made:
    """The percentile CSV: per store, level and curve the median, the 95th and the 99th
    percentile with their intervals; KAPPS with SHACL on minus writes only, paired by commit, as
    median and 95th percentile; and the mean difference SHACL on minus SHACL off."""
    made = Made()
    rows = []
    for store in stores_of(commits):
        for level in levels_of(commits):
            for arm, label, _, _ in CURVES:
                repetitions = curve_values(commits, store, level, arm)
                if not pooled(repetitions):
                    made.skipped.append(
                        f"percentiles, {store}, heartbeats {level}: no {label} rows: "
                        f"{_why_none(arm)}"
                    )
                    continue
                found = stats.pooled_quantiles(
                    repetitions,
                    [p for _, p in LATENCY_QUANTILES],
                    seed=SEED,
                    drawn_for=f"{store}/{level}/{arm}/latency",
                    resamples=RESAMPLES,
                )
                for (name, _), quantile in zip(LATENCY_QUANTILES, found, strict=True):
                    rows.append(
                        _row(store, level, "latency", arm, name, repetitions, quantile)
                    )

            differences = paired_differences(commits, store, level)
            if pooled(differences):
                found = stats.pooled_quantiles(
                    differences,
                    [0.5, 0.95],
                    seed=SEED,
                    drawn_for=f"{store}/{level}/kapps minus writes only",
                    resamples=RESAMPLES,
                )
                for name, quantile in zip(("p50", "p95"), found, strict=True):
                    rows.append(
                        _row(
                            store,
                            level,
                            "KAPPS with SHACL on minus writes only, per commit",
                            KAPPS_SHACL_ON,
                            name,
                            differences,
                            quantile,
                        )
                    )
            else:
                made.skipped.append(
                    f"percentiles, {store}, heartbeats {level}: no paired difference: no "
                    "SHACL-on commit with a replayed update"
                )

            on = curve_values(commits, store, level, KAPPS_SHACL_ON)
            off = curve_values(commits, store, level, KAPPS_SHACL_OFF)
            difference = stats.difference_of_means(
                on,
                off,
                seed=SEED,
                drawn_for=f"{store}/{level}/shacl on minus off",
                resamples=RESAMPLES,
            )
            if difference is not None:
                rows.append(
                    _row(
                        store,
                        level,
                        "KAPPS mean, SHACL on minus SHACL off",
                        f"{KAPPS_SHACL_ON} - {KAPPS_SHACL_OFF}",
                        "mean difference",
                        [*on, *off],
                        difference,
                    )
                )
            else:
                made.skipped.append(
                    f"percentiles, {store}, heartbeats {level}: no SHACL difference: "
                    "no admitted commit of both KAPPS arms"
                )
    path = output / PERCENTILES_CSV
    pd.DataFrame(rows, columns=list(PERCENTILE_COLUMNS)).to_csv(path, index=False)
    made.files.append(path.name)
    return made


def _row(
    store: str,
    level: str,
    measure: str,
    curve: str,
    statistic: str,
    repetitions: Sequence[Sequence[float]],
    found: dict[str, Any] | None,
) -> dict[str, Any]:
    interval = (found or {}).get("interval") or [None, None]
    return {
        "store": store,
        "heartbeats": level,
        "measure": measure,
        "curve": curve,
        "statistic": statistic,
        "n": len(pooled(repetitions)),
        "repetitions": len(repetitions),
        "value_seconds": None if found is None else found["value"],
        "interval_low_seconds": interval[0],
        "interval_high_seconds": interval[1],
    }


PHASE_COLUMNS = (
    "store",
    "heartbeats",
    "arm",
    "part",
    "commits",
    "median_seconds",
    "mean_seconds",
    "share_of_commit_mean",
)

# The part of the phase table that is the writes-only latency of the same commits.
WRITES_ONLY_PART = "writes-only update (replayed)"


def phase_table(commits: pd.DataFrame, replayed: pd.DataFrame, output: Path) -> Made:
    """The phase table, supplementary: per store, level and KAPPS arm, each phase's median, mean
    and share of the commit's mean over the admitted commits of the window, with the commit
    itself and, for SHACL on, the writes-only latency of the same commits beside."""
    made = Made()
    rows = []
    for store in stores_of(commits):
        for level in levels_of(commits):
            for arm in (KAPPS_SHACL_ON, KAPPS_SHACL_OFF):
                here = admitted_in_window(commits, store, level)
                here = here[here["arm"] == arm]
                if here.empty:
                    continue
                commit_mean = stats.mean(list(here["seconds"]))
                parts = [("commit", list(here["seconds"]))]
                for phase in PHASES:
                    parts.append((phase, list(here[phase_column(phase)])))
                if arm == KAPPS_SHACL_ON:
                    parts.append(
                        (
                            WRITES_ONLY_PART,
                            list(here["replayed_update_seconds"].dropna()),
                        )
                    )
                for part, values in parts:
                    if not values:
                        continue
                    part_mean = stats.mean(values)
                    rows.append(
                        {
                            "store": store,
                            "heartbeats": level,
                            "arm": arm,
                            "part": part,
                            "commits": len(values),
                            "median_seconds": stats.quantile(values, 0.5),
                            "mean_seconds": part_mean,
                            "share_of_commit_mean": part_mean / commit_mean
                            if part_mean is not None and commit_mean
                            else None,
                        }
                    )
    found = pd.DataFrame(rows, columns=list(PHASE_COLUMNS))
    found.to_csv(output / PHASES_CSV, index=False)
    (output / PHASES_MD).write_text(_markdown(found), encoding="utf-8")
    made.files += [PHASES_CSV, PHASES_MD]
    return made


def _markdown(found: pd.DataFrame) -> str:
    lines = [
        "# The time of a commit by phase",
        "",
        (
            "Per store, heartbeat level and KAPPS arm, over the admitted commits of the window: "
            "each phase's median and mean in milliseconds and its share of the commit's mean."
        ),
        "",
        "| store | heartbeats | arm | part | commits | median (ms) | mean (ms) | share |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in found.itertuples(index=False):
        lines.append(
            f"| {row.store} | {row.heartbeats} | {row.arm} | {row.part} | {row.commits} | "
            f"{row.median_seconds * MS_PER_SECOND:.2f} | "
            f"{row.mean_seconds * MS_PER_SECOND:.2f} | "
            f"{row.share_of_commit_mean:.1%} |"
        )
    return "\n".join(lines) + "\n"


def _why_none(arm: str) -> str:
    if arm == WRITES_ONLY:
        return (
            "no SHACL-on commit with a replayed update from a valid writes-only replay"
        )
    return "no admitted commit of the window of this arm"


# The figure functions, in the order the script calls them. A plot is one function and one entry.
# Each takes both tables, so that a plot of the replayed writes needs no other signature.
FIGURES: list[Callable[[pd.DataFrame, pd.DataFrame, Path], Made]] = [
    cdf_figure,
    box_figure,
    percentiles_csv,
    phase_table,
]


# -- the manifest --------------------------------------------------------------------------------


def commit_counts(commits: pd.DataFrame) -> list[dict[str, Any]]:
    """The commit counts per store, level and KAPPS arm, with the kind mix of the admitted
    commits of the window and the commits of the window not admitted, by run."""
    counts = []
    for (store, level, arm), here in commits.groupby(["store", "heartbeats", "arm"]):
        window = here[here["in_window"]]
        admitted = window[window["admitted"]]
        counts.append(
            {
                "store": store,
                "heartbeats": level,
                "arm": arm,
                "commits": len(here),
                "in_window": len(window),
                "admitted_in_window": len(admitted),
                "not_admitted_in_window_by_kind": window[~window["admitted"]]["kind"]
                .value_counts()
                .to_dict(),
                "paired": int(admitted["replayed_update_seconds"].notna().sum()),
                "without_a_replayed_update": int(
                    admitted["replayed_update_seconds"].isna().sum()
                )
                if arm == KAPPS_SHACL_ON
                else None,
                "kind_mix": (admitted["kind"].value_counts(normalize=True)).to_dict(),
                "not_admitted_in_window_by_run": {
                    str(run_id): int((~found["admitted"]).sum())
                    for run_id, found in window.groupby("run_id")
                },
            }
        )
    return counts


def run_list(runs: commit_table.Runs) -> list[dict[str, Any]]:
    """Every run and replay that counts, with its labels."""
    found = []
    for run in [*runs.kapps, *runs.replays.values()]:
        found.append(
            {
                "run_id": run.run_id,
                "arm": run.arm,
                "store": run.store,
                "heartbeats": run.heartbeats,
                "repetition": run.repetition,
                "pick_seed": run.pick_seed,
            }
        )
    return found


def write_manifest(
    output: Path,
    runs: commit_table.Runs,
    commits: pd.DataFrame,
    made: Sequence[tuple[str, Made]],
    no_table: Sequence[dict[str, str]],
) -> Path:
    """Write `figures.json`: every figure and table made, the runs, the counts, the runs left out
    and the plots skipped, with why, and the caption drafts."""
    manifest = {
        "made": {name: found.files for name, found in made},
        "tables": [COMMITS_CSV, REPLAYED_WRITES_CSV],
        "runs": run_list(runs),
        "commit_counts": commit_counts(commits) if not commits.empty else [],
        "left_out": [*runs.left_out, *no_table],
        "skipped": [why for _, found in made for why in found.skipped],
        "captions": {
            key: text for _, found in made for key, text in found.captions.items()
        },
    }
    path = output / MANIFEST
    path.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    return path


# -- the script ----------------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("folders", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, default=None)
    arguments = parser.parse_args(argv)
    folders: list[Path] = arguments.folders
    output: Path = arguments.output or folders[0].parent / "figures"
    output.mkdir(parents=True, exist_ok=True)
    print(f"Reading the runs below {', '.join(str(f) for f in folders)}")

    runs = commit_table.read_runs(folders)
    print(
        f"{len(runs.kapps)} KAPPS runs and {len(runs.replays)} writes-only replays count"
    )
    for left in runs.left_out:
        print(f"Left out: {left['run_id']}: {left['why']}")

    commit_rows: list[dict[str, Any]] = []
    replayed_rows: list[dict[str, Any]] = []
    no_table: list[dict[str, str]] = []
    for run in runs.kapps:
        replay = runs.replays.get(run.run_id)
        rows = commit_table.commit_rows(run, replay)
        if rows is None:
            why = "no spans: no per-commit table"
            print(f"No table of {run.run_id}: {why}")
            no_table.append({"run_id": run.run_id, "why": why})
            continue
        commit_rows += rows
        if replay is not None:
            replayed_rows += commit_table.replayed_write_rows(replay, run)
    commits = commit_table.table(commit_rows, commit_table.COMMIT_COLUMNS)
    replayed = commit_table.table(replayed_rows, commit_table.REPLAYED_WRITE_COLUMNS)
    commits.to_csv(output / COMMITS_CSV, index=False, quoting=csv.QUOTE_MINIMAL)
    replayed.to_csv(output / REPLAYED_WRITES_CSV, index=False)
    print(f"Wrote {len(commits)} commits to {output / COMMITS_CSV}")
    print(f"Wrote {len(replayed)} replayed writes to {output / REPLAYED_WRITES_CSV}")

    made: list[tuple[str, Made]] = []
    if commits.empty:
        print("No commit to plot: no figure made")
    else:
        for figure in FIGURES:
            found = figure(commits, replayed, output)
            made.append((figure.__name__, found))
            print(f"{figure.__name__}: wrote {', '.join(found.files) or 'nothing'}")
            for why in found.skipped:
                print(f"{figure.__name__}: skipped {why}")

    manifest = write_manifest(output, runs, commits, made, no_table)
    print(f"Wrote the manifest {manifest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
