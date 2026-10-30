"""The figure of the commit time measurement: the time of a commit in the reference implementation
against the replay, from recorded run folders of the measurement harness, with its numbers
written beside it.

    uv run python commit_time/figure.py <folder> [<folder> ...] [--output <folder>]

Each folder is a store folder of the experiment (`<store>/heartbeats-<level>/runs/<run id>/`), a
bare runs folder or a single run folder. The outputs go to `--output`, by default `figures/`
beside the first folder. The script reads the runs, builds the two tidy tables of the harness
(`jms_kapps.commit_time.commit_table`): the per-commit table (`commits.csv`, whose writes-only
column is the replayed update of `replayed_writes.csv`, paired by the commit's span id) and the
replayed writes, writes them, and then draws the figure and its two tables (`draw`). The
manifest `figures.json` lists what it made, the runs, the counts, what it left out and why, and
the numbers for the caption and the text.

`combined_figure`, heartbeats on only, writes `commit-time-paper.pdf` and `.png`: one
column of the journal (78 × 70 mm, every glyph at 7 pt, no title
inside the figure), black and grey only. A row for each arm of each store, shared by both panels,
in two groups under a bold label: "local" (store folder `local`) on top, "remote" (store folder
`kit`) under it; in each, SHACL on, SHACL off, and "replay", the writes-only replay of the same
SHACL-on commits. The panel letters stand at the top left of each panel.

- Panel (a), "time per commit (ms)": a horizontal box per row on a log axis (major ticks 10,
  100, 1000 as plain numbers, minor ticks shown): the quartiles as a white box, the median as a
  bold line across it, the mean as a white diamond above the median, whiskers with caps at the
  1st and the 99th percentile (1 % of a row's commits lie beyond each whisker; the legend says
  so), and every commit of 1 s or more as a hollow grey circle: commits beyond the
  whiskers but under 1 s are not drawn singly. A
  dashed line at 3 s, labelled "3 s" at its top. Each row label gives the row's number of
  commits, n, on a second line, counted from the same commits the box
  draws. Its numbers: `time_per_commit.csv`.
- Panel (b), "mean time per commit (ms)": the mean of the ten phases of the per-commit table in
  four parts, stacked, so that the bar ends at the mean time per commit; the replay row one
  segment, its mean update. Beside each bar its mean in whole ms. Δ, the mean commit time of
  SHACL on minus that of SHACL off, is not in the figure: the manifest
  gives it for the text. Every part's mean: `time_per_commit_by_phase.csv`.
- The legend under both panels: the symbols of panel (a) in its first row, at most three to a
  row (median, mean, the whiskers with their percentiles; the circle only if a row has a commit
  of 1 s or more), the four parts in two columns under it, read row by row in the order of the
  bar.

The numbers for the caption and the text go into the manifest (`figures.json`, under
`numbers`): n per row and the smallest and largest n; Δ per store with its bootstrap interval,
drawn with the harness's seed and resamples (`stats.difference_of_means`); the mean class
derivation and class query per row; the share of heartbeat commits per row and the heartbeat
level; the count and the share of commits of 1 s or more per row.

The figure keeps only the commits of the measured window, and of them only the admitted ones, as
the per-commit table's figures do. Every statistic comes from the harness's statistics functions
(`jms_kapps.commit_time.stats`), and the figure draws exactly those numbers.

The figure's further choices:

- The bars of panel (b) are as high as the boxes of panel (a), 60 % of the row pitch.
- The whiskers and their caps are drawn in black at 0.6 pt, as the box; the caps half the box's
  height.
- The "3 s" label stands inside panel (a), centred on the line in the local group's header row
  (empty in panel (a)), on a white ground that interrupts the line.
- Without Δ the groups' header rows stay: they hold the bold group labels.
- The mean beside a bar is the number alone; the axis title gives the unit.
- In the legend, the median's handle is the median line at the height of a box, the parts'
  handles are patches of the legend's size in the bar's fill, hatch and outline.
- The manifest's `share_above_1_s` and `commits_1_s_or_more` count the commits of 1 s or more,
  as the circles do; the share of heartbeat commits of the replay row is that of its paired
  SHACL-on commits, its class phases have no value.
- `time_per_commit_by_phase.csv` keeps its update-phase difference ("admission" rows): it is a
  table, not the figure.

Every value that sets how the figure looks is a named constant below.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import matplotlib
import pandas as pd

matplotlib.use("Agg")

from matplotlib.axes import Axes
from matplotlib.figure import Figure
from matplotlib.layout_engine import ConstrainedLayoutEngine
from matplotlib.legend_handler import HandlerLine2D
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, Rectangle
from matplotlib.ticker import FixedLocator, FuncFormatter, LogLocator, NullFormatter
from matplotlib.transforms import blended_transform_factory

from jms_kapps.commit_time import commit_table, stats
from jms_kapps.commit_time.commit_table import (
    CLASS_DERIVATION,
    CLASS_QUERY,
    CLIENT_REMAINDER,
    DIFF,
    FETCH_CLIENT,
    FETCH_READS,
    HEARTBEAT_KIND,
    HEARTBEATS_ON,
    KAPPS_SHACL_OFF,
    KAPPS_SHACL_ON,
    OTHER_REQUESTS,
    RECEIPT_ASK,
    RECEIPT_REMOVAL,
    UPDATE,
    WRITES_ONLY,
    phase_column,
)

# -- what the figure looks like ------------------------------------------------------------------

# One column of the journal: 78 mm wide; the figure 70 mm high.
# Every glyph at 7 pt, the least Elsevier allows at the printed size; no sub- or superscript.
WIDTH_MM = 78.0
HEIGHT_MM = 70.0
MM_PER_INCH = 25.4
POINTS_PER_INCH = 72
FONT_POINTS = 7
# The axes' lines.
LINE_POINTS = 0.8
# The PNG is for review, at 600 dpi; the PDF is vector.
DPI = 600
INK = "black"
MS_PER_SECOND = 1000

# The rows: the local store on top, the remote store under it, each under its bold group label; in
# each, the arms top to bottom.
STORE_ORDER = ("local", "kit")
STORE_HEADINGS = {"local": "local", "kit": "remote"}
ARMS = (KAPPS_SHACL_ON, KAPPS_SHACL_OFF, WRITES_ONLY)
ARM_LABELS = {
    KAPPS_SHACL_ON: "SHACL on",
    KAPPS_SHACL_OFF: "SHACL off",
    WRITES_ONLY: "replay",
}
# A store's group label stands one row step above its first arm, less this; the next store's
# label one row step under the last arm of the one before.
HEADING_GAP = 0.8
# The height of a box and of a bar: 60 % of the row pitch (1).
ROW_HEIGHT = 0.6
# The left edge of the group labels, from the left edge of the figure.
HEADING_INSET = 0.005
# The width of panel (a) against panel (b).
PANEL_WIDTHS = (0.8, 1.0)
# The panel letters, at the top left of each panel.
PANEL_LETTERS = ("(a)", "(b)")
# How far a label stands from what it labels, in points.
LABEL_OFFSET_POINTS = 2

# Panel (a): the box is the interquartile range, white with a black outline; the median a bold
# line across the full height of the box; the mean a white diamond above the median line; the
# whiskers reach the 1st and the 99th percentile, with caps; every commit of OUTLIER_SECONDS or
# more is a small hollow grey circle, whatever the whiskers.
TIME_LABEL = "time per commit (ms)"
WHISKERS = (0.01, 0.99)
BOX_POINTS = 0.6
WHISKER_POINTS = 0.6
# Thicker than the box outline, so that the median stands out, but not a bar of its own.
MEDIAN_POINTS = 1.0
MEAN_MARKER = "D"
MEAN_POINTS = 4.0
MEAN_EDGE_POINTS = 0.8
OUTLIER_SECONDS = 1.0
OUTLIER_MS = OUTLIER_SECONDS * 1000
OUTLIER_MARKER = "o"
OUTLIER_POINTS = 2.5
OUTLIER_EDGE_POINTS = 0.5
OUTLIER_COLOUR = "0.45"
# The order the box's parts are drawn in: the mean above the median, both above the box.
BOX_ZORDER = 2
MEDIAN_ZORDER = 3
MEAN_ZORDER = 4
# The axis starts at 10 ms, or at the decade under the fastest commit if one is faster; it ends at
# 6 s or a bit beyond the slowest commit, so that the 3 s line and its label show.
AXIS_START_MS = 10.0
AXIS_END_MS = 6000.0
END_ROOM = 1.3
MINOR_TICKS = tuple(range(2, 10))
# Enough ticks that the locator never thins the minor ticks out on a narrow panel.
MINOR_TICK_LIMIT = 50
# The 3 s line: dashed, in ink, under the boxes, labelled at its top.
THREE_SECONDS_MS = 3000.0
THREE_SECONDS_LABEL = "3 s"
THREE_SECONDS_GID = "3 s"
THREE_SECONDS_POINTS = 0.6
THREE_SECONDS_ZORDER = 1
# The label's ground: white, so that it interrupts the line, this many points round the text.
LABEL_GROUND = "white"
LABEL_GROUND_PAD_POINTS = 0.5

# Panel (b): the ten phases of the per-commit table in four parts, each with its grey and its
# hatch, so that greyscale tells them apart. The means of the four parts add up to the commit's
# mean. The diff is client time and goes into the client remainder; the receipt's query and its
# removal are store requests and go into fetch and other requests.
PARTS_LABEL = "mean time per commit (ms)"
UPDATE_PART = "update"
PARTS = (
    ("class reads", (CLASS_QUERY, CLASS_DERIVATION)),
    (
        "fetch and other requests",
        (FETCH_READS, FETCH_CLIENT, RECEIPT_ASK, RECEIPT_REMOVAL, OTHER_REQUESTS),
    ),
    (UPDATE_PART, (UPDATE,)),
    ("client remainder", (DIFF, CLIENT_REMAINDER)),
)
UPDATE_PHASES = (UPDATE,)
PART_GREYS = ("0.25", "0.55", "0.9", "1.0")
PART_HATCHES = ("", "////", "....", "\\\\\\\\")
BAR_EDGE_POINTS = 0.5
HATCH_POINTS = 0.5
# The bar panel leaves room right of the longest bar for its mean.
TEXT_ROOM = 1.25
# The kind of the rows over all commits, pooled.
ALL_KINDS = "all"

# The legend under both panels, in a band of the figure kept free of the panels: the symbols of
# panel (a) in one row, the four parts under it in two columns.
LEGEND_BAND_MM = 13.5
# The band grows by one legend row when the circle needs a second row of symbols.
LEGEND_ROW_MM = 3.5
# The whiskers' label names the percentiles they reach.
SYMBOL_LABELS = ("median", "mean", "1st–99th percentile", "1 s or more")
# The median's handle: a vertical line as high as a box, as wide as the median line.
MEDIAN_HANDLE_MARKER = "|"
# The whiskers' handle: a line with a cap at each end, as high as the caps in the plot.
WHISKER_HANDLE_MARKER = "|"
# Each row label carries the row's number of commits on a second line,
# the two lines this close together.
ROW_LABEL_LINE_SPACING = 1.0
PART_COLUMNS = 2
# At most three symbols in a row, so that the row fits the column's width when the circle shows.
SYMBOL_COLUMNS = 3
LEGEND_COLUMN_SPACING = 1.2
LEGEND_HANDLE_LENGTH = 1.6

# The bootstrap of Δ: the harness's seed and resamples, and the name the interval is drawn
# for, so that the interval stays the one the paper gives.
SEED = stats.DEFAULT_SEED
RESAMPLES = stats.DEFAULT_RESAMPLES
DELTA_DRAWN_FOR = "{store}/{level}/shacl on minus off"

# The files.
TIME_PER_COMMIT_CSV = "time_per_commit.csv"
TIME_PER_COMMIT_BY_PHASE_CSV = "time_per_commit_by_phase.csv"
FIGURE_NAME = "commit-time-paper"

# The tidy tables and the manifest the script writes beside the figure.
COMMITS_CSV = "commits.csv"
REPLAYED_WRITES_CSV = "replayed_writes.csv"
MANIFEST = "figures.json"

# The arm and the row of the update-phase difference in `time_per_commit_by_phase.csv`.
ADMISSION = "admission"
ADMISSION_ARM = f"{KAPPS_SHACL_ON} - {KAPPS_SHACL_OFF}"


@dataclass
class Drawn:
    """What one paper figure function made: the files it wrote, the figures it drew, what it
    skipped and why, the caption draft of each figure, by its file, and the numbers of each
    figure for its caption and the text (the manifest's `numbers`), by its file."""

    files: list[str] = field(default_factory=list)
    figures: list[Figure] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    captions: dict[str, str] = field(default_factory=dict)
    numbers: dict[str, Any] = field(default_factory=dict)


# -- the values ----------------------------------------------------------------------------------


def stores_of(table: pd.DataFrame) -> list[str]:
    """The stores of `table`, in the order of the rows."""
    found = set(table["store"])
    return [s for s in STORE_ORDER if s in found] + sorted(found - set(STORE_ORDER))


def admitted_commits(
    commits: pd.DataFrame, store: str, level: str, arm: str
) -> pd.DataFrame:
    """The admitted commits of the window of the KAPPS `arm` at `store` and heartbeat `level`."""
    return commits[
        (commits["store"] == store)
        & (commits["heartbeats"] == level)
        & (commits["arm"] == arm)
        & commits["admitted"].astype(bool)
        & commits["in_window"].astype(bool)
    ]


def part_seconds(commits: pd.DataFrame, phases: Sequence[str]) -> list[float]:
    """For each commit of `commits`, the sum of its seconds in `phases`."""
    total = sum(commits[phase_column(phase)] for phase in phases)
    return [float(value) for value in total]


def writes_only_seconds(commits: pd.DataFrame, store: str, level: str) -> list[float]:
    """The replayed updates of the admitted SHACL-on commits of the window at `store` and
    `level`: the writes-only row."""
    here = admitted_commits(commits, store, level, KAPPS_SHACL_ON)
    return [float(value) for value in here["replayed_update_seconds"].dropna()]


def commit_seconds(
    commits: pd.DataFrame, store: str, level: str, arm: str
) -> list[float]:
    """The commit times of one row: the admitted commits of the window of a KAPPS `arm`, or the
    replayed updates of the writes-only arm."""
    if arm == WRITES_ONLY:
        return writes_only_seconds(commits, store, level)
    here = admitted_commits(commits, store, level, arm)
    return [float(value) for value in here["seconds"]]


def box_statistics(values: Sequence[float]) -> dict[str, Any]:
    """The numbers of one box, by the harness's statistics functions: the quartiles and the
    median, the mean, the whiskers at the 1st and the 99th percentile, every value of OUTLIER_MS
    or more, and n."""
    low = stats.quantile(values, WHISKERS[0])
    high = stats.quantile(values, WHISKERS[1])
    assert low is not None and high is not None, "a box needs at least one value"
    return {
        "whislo": low,
        "q1": stats.quantile(values, 0.25),
        "med": stats.quantile(values, 0.5),
        "q3": stats.quantile(values, 0.75),
        "whishi": high,
        "mean": stats.mean(values),
        "fliers": [v for v in values if v >= OUTLIER_MS],
        "n": len(values),
    }


def time_per_commit_by_phase_rows(
    commits: pd.DataFrame, store: str, level: str
) -> list[dict[str, Any]]:
    """The rows of `time_per_commit_by_phase.csv` at `store` and `level`.

    Over all commits (kind `all`): for each KAPPS arm and part, n, the mean, the median and the
    share of the commit's mean; the writes-only update; and the admission, the mean update phase
    of SHACL on minus that of SHACL off, as a share of the SHACL-on commit's mean (a table only:
    the figure shows Δ, the whole commit's difference).

    Then, for each commit kind that both KAPPS arms have, the update part of each arm and the
    admission over the commits of that kind only (the arms are offered different work,
    so their kind mix differs, and the pooled admission mixes it in).
    """
    on = admitted_commits(commits, store, level, KAPPS_SHACL_ON)
    off = admitted_commits(commits, store, level, KAPPS_SHACL_OFF)
    rows = []
    for arm, here in ((KAPPS_SHACL_ON, on), (KAPPS_SHACL_OFF, off)):
        if here.empty:
            continue
        mean_of_commit = _mean_seconds(here)
        for part, phases in PARTS:
            values = part_seconds(here, phases)
            rows.append(
                _time_row(
                    store,
                    level,
                    ALL_KINDS,
                    arm,
                    part,
                    values,
                    (stats.mean(values) or 0.0) / mean_of_commit,
                )
            )
    writes = writes_only_seconds(commits, store, level)
    if writes:
        share = (
            (stats.mean(writes) or 0.0) / _mean_seconds(on) if not on.empty else None
        )
        rows.append(
            _time_row(store, level, ALL_KINDS, WRITES_ONLY, UPDATE_PART, writes, share)
        )
    if not on.empty and not off.empty:
        rows.append(_admission_row(store, level, ALL_KINDS, on, off))
        for kind in sorted(set(on["kind"]) & set(off["kind"])):
            on_kind = on[on["kind"] == kind]
            off_kind = off[off["kind"] == kind]
            for arm, here in ((KAPPS_SHACL_ON, on_kind), (KAPPS_SHACL_OFF, off_kind)):
                values = part_seconds(here, UPDATE_PHASES)
                rows.append(
                    _time_row(
                        store,
                        level,
                        kind,
                        arm,
                        UPDATE_PART,
                        values,
                        (stats.mean(values) or 0.0) / _mean_seconds(here),
                    )
                )
            rows.append(_admission_row(store, level, kind, on_kind, off_kind))
    return rows


def admission_seconds(on: pd.DataFrame, off: pd.DataFrame) -> float:
    """The admission: the mean update phase of the SHACL-on commits `on` minus that of the
    SHACL-off commits `off`."""
    on_mean = stats.mean(part_seconds(on, UPDATE_PHASES))
    off_mean = stats.mean(part_seconds(off, UPDATE_PHASES))
    assert on_mean is not None and off_mean is not None
    return on_mean - off_mean


def _admission_row(
    store: str, level: str, kind: str, on: pd.DataFrame, off: pd.DataFrame
) -> dict[str, Any]:
    admission = admission_seconds(on, off)
    return {
        "store": store,
        "heartbeats": level,
        "kind": kind,
        "arm": ADMISSION_ARM,
        "part": ADMISSION,
        "n": None,
        "mean_seconds": admission,
        "median_seconds": None,
        "share_of_commit_mean": admission / _mean_seconds(on),
    }


def _mean_seconds(commits: pd.DataFrame) -> float:
    """The mean time of `commits`."""
    found = stats.mean([float(value) for value in commits["seconds"]])
    assert found is not None
    return found


def _time_row(
    store: str,
    level: str,
    kind: str,
    arm: str,
    part: str,
    values: Sequence[float],
    share: float | None,
) -> dict[str, Any]:
    return {
        "store": store,
        "heartbeats": level,
        "kind": kind,
        "arm": arm,
        "part": part,
        "n": len(values),
        "mean_seconds": stats.mean(values),
        "median_seconds": stats.quantile(values, 0.5),
        "share_of_commit_mean": share,
    }


def in_ms(seconds: Sequence[float]) -> list[float]:
    """`seconds` in milliseconds, as the axes show them."""
    return [value * MS_PER_SECOND for value in seconds]


# -- the figure ----------------------------------------------------------------------------------


def row_positions(blocks: Sequence[tuple[str, Sequence[str]]]) -> dict[str, float]:
    """The height of each row, top down, by `store` (its group label) and by `store/arm`, for the
    stores and their arms in `blocks`."""
    found: dict[str, float] = {}
    at = 0.0
    for store, arms in blocks:
        found[store] = at
        at += HEADING_GAP
        for arm in arms:
            found[f"{store}/{arm}"] = at
            at += 1.0
    return found


def by_run(commits: pd.DataFrame) -> list[list[float]]:
    """The commit times of `commits`, one list for each run, in the order of the run ids: the
    repetitions a bootstrap resamples."""
    return [
        [float(value) for value in values]
        for _, values in commits.groupby("run_id")["seconds"]
    ]


def shacl_difference(
    commits: pd.DataFrame, store: str, level: str
) -> dict[str, Any] | None:
    """Δ at `store` and `level`: the mean commit time of SHACL on minus that of SHACL off, from
    the unrounded means, with its bootstrap interval (`stats.difference_of_means`, the
    harness's seed and resamples); None without both arms."""
    on = by_run(admitted_commits(commits, store, level, KAPPS_SHACL_ON))
    off = by_run(admitted_commits(commits, store, level, KAPPS_SHACL_OFF))
    if not on or not off:
        return None
    return stats.difference_of_means(
        on,
        off,
        seed=SEED,
        drawn_for=DELTA_DRAWN_FOR.format(store=store, level=level),
        resamples=RESAMPLES,
    )


def whole_ms(seconds: float) -> str:
    """`seconds` in whole ms, as the figure writes a number."""
    return f"{seconds * MS_PER_SECOND:.0f}"


def row_numbers(
    commits: pd.DataFrame, store: str, level: str, arm: str
) -> dict[str, Any]:
    """The manifest's numbers of one row: n, the mean class query and class derivation (none for
    the replay row), the share of heartbeat commits (for the replay row, of its paired SHACL-on
    commits) and the count and the share of commits of 1 s or more."""
    values = in_ms(commit_seconds(commits, store, level, arm))
    if arm == WRITES_ONLY:
        here = admitted_commits(commits, store, level, KAPPS_SHACL_ON)
        here = here[here["replayed_update_seconds"].notna()]
        class_query = class_derivation = None
    else:
        here = admitted_commits(commits, store, level, arm)
        class_query = stats.mean(part_seconds(here, (CLASS_QUERY,)))
        class_derivation = stats.mean(part_seconds(here, (CLASS_DERIVATION,)))
    return {
        "store": store,
        "group": STORE_HEADINGS.get(store, store),
        "row": ARM_LABELS[arm],
        "arm": arm,
        "n": len(values),
        "mean_class_query_seconds": class_query,
        "mean_class_derivation_seconds": class_derivation,
        "heartbeat_share": float((here["kind"] == HEARTBEAT_KIND).mean()),
        "commits_1_s_or_more": sum(v >= OUTLIER_MS for v in values),
        "share_above_1_s": sum(v >= OUTLIER_MS for v in values) / len(values),
    }


def combined_figure(commits: pd.DataFrame, output: Path) -> Drawn:
    """The paper figure at heartbeats on, `commit-time-paper.pdf` and `.png`: for each store,
    local on top, a row for each arm. Panel (a): the box of the row's commit times on a log axis
    in ms, with the dashed 3 s line. Panel (b): the means of the four parts of the KAPPS rows
    stacked, the replay row's mean update alone, its mean beside each bar. Its numbers for the caption and the text go into `numbers`, for the manifest."""
    drawn = Drawn()
    level = HEARTBEATS_ON
    blocks = []
    for store in stores_of(commits[commits["heartbeats"] == level]):
        arms = [arm for arm in ARMS if commit_seconds(commits, store, level, arm)]
        for arm in ARMS:
            if arm not in arms:
                drawn.skipped.append(
                    f"combined figure, {store}: no {ARM_LABELS[arm]} row: no admitted "
                    "commit in the window"
                    + (" with a replayed update" if arm == WRITES_ONLY else "")
                )
        if arms:
            blocks.append((store, arms))
    if not blocks:
        drawn.skipped.append(
            f"combined figure: no admitted commit in the window at heartbeats {level}"
        )
        return drawn
    rows = row_positions(blocks)
    circles = any(
        value >= OUTLIER_SECONDS
        for store, arms in blocks
        for arm in arms
        for value in commit_seconds(commits, store, level, arm)
    )
    band = (LEGEND_BAND_MM + (LEGEND_ROW_MM if circles else 0)) / HEIGHT_MM
    with matplotlib.rc_context(
        {
            "font.size": FONT_POINTS,
            "axes.labelsize": FONT_POINTS,
            "axes.titlesize": FONT_POINTS,
            "axes.titleweight": "normal",
            "xtick.labelsize": FONT_POINTS,
            "ytick.labelsize": FONT_POINTS,
            "legend.fontsize": FONT_POINTS,
            "lines.linewidth": LINE_POINTS,
            "axes.linewidth": LINE_POINTS,
            "hatch.linewidth": HATCH_POINTS,
            "hatch.color": INK,
            # TrueType, embedded: journals refuse Type 3 fonts.
            "pdf.fonttype": 42,
        }
    ):
        figure = Figure(
            figsize=(WIDTH_MM / MM_PER_INCH, HEIGHT_MM / MM_PER_INCH),
            layout="constrained",
        )
        # The panels keep clear of the legend's band at the bottom.
        engine = figure.get_layout_engine()
        assert isinstance(engine, ConstrainedLayoutEngine)
        engine.set(rect=(0, band, 1, 1 - band))
        time_panel, parts = figure.subplots(
            1, 2, sharey=True, gridspec_kw={"width_ratios": PANEL_WIDTHS}
        )
        for ax, letter in zip((time_panel, parts), PANEL_LETTERS, strict=True):
            ax.set_title(letter, loc="left")
        row_labels = []
        numbers_of_rows = []
        differences = []
        counts = []
        for store, arms in blocks:
            heading = STORE_HEADINGS.get(store, store)
            _heading(figure, time_panel, rows[store], heading)
            for arm in arms:
                values = commit_seconds(commits, store, level, arm)
                at = rows[f"{store}/{arm}"]
                _draw_box(
                    time_panel, at, box_statistics(in_ms(values)), f"{store}/{arm}"
                )
                _draw_bar(parts, at, commits, store, level, arm)
                row_labels.append(f"{ARM_LABELS[arm]}\nn = {len(values):,}")
                numbers_of_rows.append(row_numbers(commits, store, level, arm))
                counts.append(f"{heading} {ARM_LABELS[arm]} n = {len(values)}")
            # Δ is not in the figure: the manifest gives it, for the text.
            difference = shacl_difference(commits, store, level)
            if difference is None:
                drawn.skipped.append(
                    f"combined figure, {store}: no Δ: it needs both KAPPS arms"
                )
                continue
            interval = difference["interval"] or [None, None]
            differences.append(
                {
                    "store": store,
                    "group": heading,
                    "value_seconds": difference["value"],
                    "interval_low_seconds": interval[0],
                    "interval_high_seconds": interval[1],
                    "rounded_ms": int(whole_ms(difference["value"])),
                    "repetitions_on": len(
                        by_run(admitted_commits(commits, store, level, KAPPS_SHACL_ON))
                    ),
                    "repetitions_off": len(
                        by_run(admitted_commits(commits, store, level, KAPPS_SHACL_OFF))
                    ),
                }
            )
        ticks = [rows[f"{store}/{arm}"] for store, arms in blocks for arm in arms]
        time_panel.set_yticks(ticks, row_labels)
        for label in time_panel.get_yticklabels():
            label.set_linespacing(ROW_LABEL_LINE_SPACING)
        time_panel.tick_params(axis="y", length=0)
        parts.tick_params(axis="y", left=False)
        time_panel.set_ylim(max(ticks) + 0.6, -0.5)
        _time_axis(time_panel, commits, blocks, level, rows[blocks[0][0]])
        parts.set_xlim(0, _longest_bar(parts) * TEXT_ROOM)
        parts.set_xlabel(PARTS_LABEL)
        _legends(figure, time_panel, band, circles)
        _save(figure, output, FIGURE_NAME, drawn)
        sizes = [row["n"] for row in numbers_of_rows]
        drawn.numbers[FIGURE_NAME] = {
            "heartbeats": level,
            "rows": numbers_of_rows,
            "n_smallest": min(sizes),
            "n_largest": max(sizes),
            "shacl_on_minus_off": differences,
        }
        drawn.captions[FIGURE_NAME] = (
            "Time per commit with KAPPS at heartbeats on, on the local store and on the "
            "a remote GraphDB server: KAPPS with SHACL on and with SHACL off, and replay, the "
            "plain SPARQL update of each SHACL-on commit replayed alone in the same store, "
            "paired by the commit's span id. The admitted commits of the measured window. (a) "
            "The box spans the quartiles, the line is the median, the diamond the mean; the "
            "whiskers reach the 1st and the 99th percentile, so 1 % of a row's commits lie "
            "beyond each whisker; a circle is a commit of 1 s or more. The dashed line marks "
            "3 s. (b) The mean of each part of a commit, stacked, "
            "so that the bar ends at the mean time per commit; beside each bar its mean. Class "
            "reads = class query + class derivation; fetch and other requests = fetch reads + "
            "fetch client + the receipt's query and removal + other requests; client remainder "
            "= the diff + the rest of the client's time. "
            f"{'; '.join(counts)}."
        )
    return drawn


def _heading(figure: Figure, ax: Axes, at: float, text: str) -> None:
    """A store's group label at height `at`, in bold from the left edge of the figure."""
    heading = ax.text(
        HEADING_INSET,
        at,
        text,
        transform=blended_transform_factory(figure.transFigure, ax.transData),
        horizontalalignment="left",
        verticalalignment="center",
        fontweight="bold",
        gid=f"heading:{text}",
    )
    heading.set_in_layout(False)


def _median_style() -> dict[str, Any]:
    return {
        "color": INK,
        "linewidth": MEDIAN_POINTS,
        "solid_capstyle": "butt",
        "zorder": MEDIAN_ZORDER,
    }


def _mean_style() -> dict[str, Any]:
    return {
        "marker": MEAN_MARKER,
        "markersize": MEAN_POINTS,
        "markerfacecolor": "white",
        "markeredgecolor": INK,
        "markeredgewidth": MEAN_EDGE_POINTS,
        "linestyle": "none",
        "zorder": MEAN_ZORDER,
    }


def _outlier_style() -> dict[str, Any]:
    return {
        "marker": OUTLIER_MARKER,
        "markersize": OUTLIER_POINTS,
        "markerfacecolor": "none",
        "markeredgecolor": OUTLIER_COLOUR,
        "markeredgewidth": OUTLIER_EDGE_POINTS,
        "linestyle": "none",
        "zorder": BOX_ZORDER,
    }


def _part_style(k: int) -> dict[str, Any]:
    return {
        "facecolor": PART_GREYS[k],
        "hatch": PART_HATCHES[k],
        "edgecolor": INK,
        "linewidth": BAR_EDGE_POINTS,
    }


def _draw_box(ax: Axes, at: float, numbers: dict[str, Any], gid: str) -> None:
    """One horizontal box at height `at` with the given `numbers` (no statistic is computed
    here): the box, the median, the mean as a diamond above it, the whiskers with caps, and every
    commit of 1 s or more as a hollow circle. Every artist carries `gid`, so that a test
    finds it."""
    found = ax.bxp(
        [numbers],
        positions=[at],
        widths=ROW_HEIGHT,
        orientation="horizontal",
        patch_artist=True,
        showfliers=True,
        showmeans=True,
        boxprops={
            "facecolor": "white",
            "edgecolor": INK,
            "linewidth": BOX_POINTS,
            "zorder": BOX_ZORDER,
        },
        medianprops=_median_style(),
        whiskerprops={
            "color": INK,
            "linewidth": WHISKER_POINTS,
            "zorder": BOX_ZORDER,
        },
        capprops={"color": INK, "linewidth": WHISKER_POINTS, "zorder": BOX_ZORDER},
        meanprops=_mean_style(),
        flierprops=_outlier_style(),
    )
    for part, artists in found.items():
        for artist in artists:
            artist.set_gid(f"{part}:{gid}")


def _time_axis(
    ax: Axes,
    commits: pd.DataFrame,
    blocks: Sequence[tuple[str, Sequence[str]]],
    level: str,
    header: float,
) -> None:
    """Panel (a)'s log axis in ms, plain decade labels and unlabelled minor ticks, its title, and
    the dashed 3 s line with "3 s" at its top, in the first group's header row `header`."""
    shown = [
        value
        for store, arms in blocks
        for arm in arms
        for value in in_ms(commit_seconds(commits, store, level, arm))
    ]
    start = AXIS_START_MS
    while start > min(shown):
        start /= 10
    end = max(AXIS_END_MS, max(shown) * END_ROOM)
    ax.set_xscale("log")
    ax.set_xlim(start, end)
    decades = []
    decade = start
    while decade <= end:
        decades.append(decade)
        decade *= 10
    # Plain numbers, no exponent: a superscript would fall under 7 pt.
    ax.xaxis.set_major_locator(FixedLocator(decades))
    ax.xaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:g}"))
    ax.xaxis.set_minor_locator(LogLocator(subs=MINOR_TICKS, numticks=MINOR_TICK_LIMIT))
    ax.xaxis.set_minor_formatter(NullFormatter())
    ax.set_xlabel(TIME_LABEL)
    line = ax.axvline(
        THREE_SECONDS_MS,
        linestyle="--",
        color=INK,
        linewidth=THREE_SECONDS_POINTS,
        zorder=THREE_SECONDS_ZORDER,
    )
    line.set_gid(THREE_SECONDS_GID)
    label = ax.text(
        THREE_SECONDS_MS,
        header,
        THREE_SECONDS_LABEL,
        horizontalalignment="center",
        verticalalignment="center",
        bbox={
            "facecolor": LABEL_GROUND,
            "edgecolor": "none",
            "pad": LABEL_GROUND_PAD_POINTS,
        },
        zorder=MEAN_ZORDER + 1,
    )
    label.set_gid(THREE_SECONDS_GID)


def _draw_bar(
    ax: Axes, at: float, commits: pd.DataFrame, store: str, level: str, arm: str
) -> None:
    """One bar at height `at`: the means of the four parts of a KAPPS arm's commits, stacked, or
    the replay row's mean update alone; its mean in whole ms beside it."""
    values = commit_seconds(commits, store, level, arm)
    if arm == WRITES_ONLY:
        widths = {UPDATE_PART: stats.mean(values) or 0.0}
    else:
        here = admitted_commits(commits, store, level, arm)
        widths = {
            part: stats.mean(part_seconds(here, phases)) or 0.0
            for part, phases in PARTS
        }
    left = 0.0
    for k, (part, _) in enumerate(PARTS):
        if part not in widths:
            continue
        width = widths[part] * MS_PER_SECOND
        ax.barh(
            at,
            width,
            left=left,
            height=ROW_HEIGHT,
            gid=f"bar:{store}/{arm}/{part}",
            **_part_style(k),
        )
        left += width
    ax.annotate(
        whole_ms(stats.mean(values) or 0.0),
        xy=(left, at),
        xytext=(LABEL_OFFSET_POINTS, 0),
        textcoords="offset points",
        verticalalignment="center",
        gid=f"total:{store}/{arm}",
    )


def _legends(figure: Figure, time_panel: Axes, band: float, circles: bool) -> None:
    """The legend under both panels, in the band of the figure kept free of them: the symbols of
    panel (a) in the first row, drawn as in the panel, the median at the height of a box, the
    circle only if the panel has one (`circles`); under
    it the four parts in two columns, read row by row in the order of the bar."""
    figure.draw_without_rendering()
    low, high = time_panel.transData.transform([(1, 0), (1, ROW_HEIGHT)])
    box_points = abs(high[1] - low[1]) * POINTS_PER_INCH / figure.dpi
    median = {**_median_style(), "linestyle": "none"}
    median.pop("solid_capstyle")
    median.pop("linewidth")
    symbols = [
        Line2D(
            [],
            [],
            marker=MEDIAN_HANDLE_MARKER,
            markersize=box_points,
            markeredgewidth=MEDIAN_POINTS,
            label=SYMBOL_LABELS[0],
            **median,
        ),
        Line2D([], [], label=SYMBOL_LABELS[1], **_mean_style()),
        whiskers := Line2D(
            [],
            [],
            color=INK,
            linewidth=WHISKER_POINTS,
            marker=WHISKER_HANDLE_MARKER,
            markersize=box_points / 2,
            markeredgewidth=WHISKER_POINTS,
            label=SYMBOL_LABELS[2],
        ),
    ]
    if circles:
        symbols.append(Line2D([], [], label=SYMBOL_LABELS[3], **_outlier_style()))
    first = figure.legend(
        handles=column_major(symbols, SYMBOL_COLUMNS),
        # The whiskers' handle has a cap at each end; every other symbol is drawn once.
        handler_map={whiskers: HandlerLine2D(numpoints=2)},
        loc="upper center",
        bbox_to_anchor=(0.5, band),
        ncols=min(len(symbols), SYMBOL_COLUMNS),
        frameon=False,
        columnspacing=LEGEND_COLUMN_SPACING,
        handlelength=LEGEND_HANDLE_LENGTH,
    )
    first.set_gid("legend:symbols")
    patches = [Patch(label=part, **_part_style(k)) for k, (part, _) in enumerate(PARTS)]
    # The parts' legend starts right under the symbols' one.
    figure.draw_without_rendering()
    under = first.get_window_extent().y0 / figure.bbox.height
    second = figure.legend(
        handles=column_major(patches, PART_COLUMNS),
        loc="upper center",
        bbox_to_anchor=(0.5, under),
        ncols=PART_COLUMNS,
        frameon=False,
        borderaxespad=0,
        columnspacing=LEGEND_COLUMN_SPACING,
        handlelength=LEGEND_HANDLE_LENGTH,
    )
    second.set_gid("legend:parts")


def column_major(items: Sequence[Any], columns: int) -> list[Any]:
    """`items`, meant to be read row by row in `columns` columns, in the order a legend fills
    its columns: column by column."""
    rows = -(-len(items) // columns)
    return [
        items[row * columns + column]
        for column in range(columns)
        for row in range(rows)
        if row * columns + column < len(items)
    ]


def _longest_bar(ax: Axes) -> float:
    """The right end of the longest bar on `ax`."""
    return max(
        patch.get_x() + patch.get_width()
        for patch in ax.patches
        if isinstance(patch, Rectangle)
    )


# -- the numbers ---------------------------------------------------------------------------------


def time_per_commit_csv(commits: pd.DataFrame, output: Path) -> Drawn:
    """The numbers of panel (a): per store, heartbeat level and arm, n, the mean, the whiskers,
    the quartiles and the median, and the 95th percentile, of the row's commit times."""
    drawn = Drawn()
    rows = []
    for store in stores_of(commits):
        for level in sorted(set(commits["heartbeats"])):
            for arm in ARMS:
                values = commit_seconds(commits, store, level, arm)
                if not values:
                    continue
                rows.append(
                    {
                        "store": store,
                        "heartbeats": level,
                        "arm": arm,
                        "n": len(values),
                        "mean_seconds": stats.mean(values),
                        "p01_seconds": stats.quantile(values, WHISKERS[0]),
                        "p25_seconds": stats.quantile(values, 0.25),
                        "p50_seconds": stats.quantile(values, 0.5),
                        "p75_seconds": stats.quantile(values, 0.75),
                        "p95_seconds": stats.quantile(values, 0.95),
                        "p99_seconds": stats.quantile(values, WHISKERS[1]),
                    }
                )
    path = output / TIME_PER_COMMIT_CSV
    pd.DataFrame(
        rows,
        columns=[
            "store",
            "heartbeats",
            "arm",
            "n",
            "mean_seconds",
            "p01_seconds",
            "p25_seconds",
            "p50_seconds",
            "p75_seconds",
            "p95_seconds",
            "p99_seconds",
        ],
    ).to_csv(path, index=False)
    drawn.files.append(path.name)
    return drawn


def time_per_commit_by_phase_csv(commits: pd.DataFrame, output: Path) -> Drawn:
    """The numbers of panel (b) (`time_per_commit_by_phase_rows`), for every store and level."""
    drawn = Drawn()
    rows = [
        row
        for store in stores_of(commits)
        for level in sorted(set(commits["heartbeats"]))
        for row in time_per_commit_by_phase_rows(commits, store, level)
    ]
    path = output / TIME_PER_COMMIT_BY_PHASE_CSV
    pd.DataFrame(
        rows,
        columns=[
            "store",
            "heartbeats",
            "kind",
            "arm",
            "part",
            "n",
            "mean_seconds",
            "median_seconds",
            "share_of_commit_mean",
        ],
    ).to_csv(path, index=False)
    drawn.files.append(path.name)
    return drawn


def _save(figure: Figure, output: Path, name: str, drawn: Drawn) -> None:
    # No tight bounding box: it would change the page from 78 × 70 mm.
    for suffix in (".pdf", ".png"):
        path = output / f"{name}{suffix}"
        figure.savefig(path, dpi=DPI)
        drawn.files.append(path.name)
    drawn.figures.append(figure)


def draw(commits: pd.DataFrame, output: Path) -> list[tuple[str, Drawn]]:
    """The paper figure and its numbers into `output`, each by its name."""
    return [
        ("combined_figure", combined_figure(commits, output)),
        ("time_per_commit_csv", time_per_commit_csv(commits, output)),
        ("time_per_commit_by_phase_csv", time_per_commit_by_phase_csv(commits, output)),
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
    made: Sequence[tuple[str, Drawn]],
    no_table: Sequence[dict[str, str]],
) -> Path:
    """Write `figures.json`: every figure and table made, the runs, the counts, the runs left out
    and the plots skipped, with why, the caption drafts, and the numbers of each figure for its
    caption and the text."""
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
        "numbers": {
            key: numbers for _, found in made for key, numbers in found.numbers.items()
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

    made: list[tuple[str, Drawn]] = []
    if commits.empty:
        print("No commit to plot: no figure made")
    else:
        for name, drawn in draw(commits, output):
            made.append((name, drawn))
            print(f"{name}: wrote {', '.join(drawn.files) or 'nothing'}")
            for why in drawn.skipped:
                print(f"{name}: skipped {why}")

    manifest = write_manifest(output, runs, commits, made, no_table)
    print(f"Wrote the manifest {manifest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
