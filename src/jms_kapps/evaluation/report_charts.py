"""The charts of the report of a spec (`report`): PNG files, drawn with matplotlib's headless
backend, so that no display is needed. Each chart takes the runs table as a pandas frame.

**A run is a dot.** A valid run is a filled dot and an invalid run is a ring. A failed or an
interrupted run has no values, and is not plotted. A tick marks the median of the valid runs of a
configuration (`stats.median_and_iqr`, as the table of a spec reads it).

**The charts:** the throughput of each configuration, its commit p95, its read p95 and write p95,
and, for each family of configurations that differ only in the size of their prefill, the
throughput and the commit p95 against that size, on a log scale.

The steps chart (`steps_chart`, jms_kapps 87) compares the configurations of one family step by
step: a dot at the median and a tick at the p95 of each step, on a log time scale.

The by-hour chart (`hours_chart`, jms_kapps 97) shows, for one configuration, four measures against
the hour of day (UTC) of the window's start.

(Added on 2026-10-12, jms_kapps 86; interim, jms_kapps 83.)
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import matplotlib

# The headless backend, set before pyplot is imported: the report needs no display.
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.axes import Axes
from matplotlib.lines import Line2D

from jms_kapps.evaluation.stats import median_and_iqr

# The status of a valid run and of an invalid one: the two a chart plots.
VALID = "completed"
INVALID = "invalid"

# The files of the charts.
THROUGHPUT_PNG = "throughput.png"
COMMIT_P95_PNG = "commit-p95.png"
READ_WRITE_P95_PNG = "read-write-p95.png"


def family_png(number: int) -> str:
    """The file of the chart of the family of prefill sizes whose lowest configuration is
    `number`."""
    return f"prefill-family-{number}.png"


def steps_png(number: int) -> str:
    """The file of the steps chart of the family of prefill sizes whose lowest configuration is
    `number`."""
    return f"steps-family-{number}.png"


_COLOUR = "#1f6fb4"
_SPREAD = 0.12


def _plotted(runs: pd.DataFrame, column: str) -> pd.DataFrame:
    """The runs with a value in `column` that a chart plots: the valid and the invalid."""
    return runs[runs["status"].isin([VALID, INVALID]) & runs[column].notna()]


def _median(values: Sequence[float]) -> float | None:
    found = median_and_iqr(list(values))
    return None if found is None else float(found["median"])


def _legend(ax: Axes) -> None:
    ax.legend(
        handles=[
            Line2D([], [], marker="o", linestyle="", color=_COLOUR, label="valid run"),
            Line2D(
                [],
                [],
                marker="o",
                linestyle="",
                markerfacecolor="none",
                color=_COLOUR,
                label="invalid run (ring)",
            ),
            Line2D(
                [],
                [],
                marker="_",
                markersize=14,
                linestyle="",
                color="black",
                label="median of the valid runs",
            ),
        ],
        fontsize="small",
        loc="best",
    )


def _categories(
    ax: Axes, runs: pd.DataFrame, column: str, title: str, unit: str
) -> None:
    """One column of dots for each label (a configuration with its series, and its run kind
    unless `uc2`, jms_kapps 110)."""
    plotted = _plotted(runs, column)
    labels = list(dict.fromkeys(plotted["label"]))
    for place, name in enumerate(labels):
        mine = plotted[plotted["label"] == name]
        for position, (_, run) in enumerate(mine.iterrows()):
            x = place + (position - (len(mine) - 1) / 2) * _SPREAD
            ring = run["status"] == INVALID
            ax.scatter(
                [x],
                [run[column]],
                s=40,
                facecolors="none" if ring else _COLOUR,
                edgecolors=_COLOUR,
                linewidths=1.5,
                zorder=3,
            )
        middle = _median(list(mine[mine["status"] == VALID][column]))
        if middle is not None:
            ax.hlines(
                middle, place - 0.3, place + 0.3, colors="black", linewidth=2, zorder=4
            )
    ax.set_xticks(range(len(labels)), labels, rotation=45, ha="right")
    ax.set_xlabel("configuration")
    ax.set_ylabel(unit)
    ax.set_title(title)
    ax.grid(axis="y", alpha=0.3)
    if not labels:
        ax.text(0.5, 0.5, "no run has a value", transform=ax.transAxes, ha="center")
    else:
        _legend(ax)


def category_chart(
    runs: pd.DataFrame, column: str, title: str, unit: str, path: Path
) -> None:
    """Draw `column` of the runs table for each configuration into `path`."""
    figure, ax = plt.subplots(
        figsize=(max(6.0, 0.8 * runs["label"].nunique() + 2), 4.5)
    )
    try:
        _categories(ax, runs, column, title, unit)
        figure.tight_layout()
        figure.savefig(path, dpi=110)
    finally:
        plt.close(figure)


def pair_chart(
    runs: pd.DataFrame, columns: Sequence[tuple[str, str, str]], path: Path
) -> None:
    """Draw two columns of the runs table side by side, each for each configuration: a
    `(column, title, unit)` each."""
    width = max(10.0, 1.6 * runs["label"].nunique() + 4)
    figure, axes = plt.subplots(1, len(columns), figsize=(width, 4.5))
    try:
        for ax, (column, title, unit) in zip(axes, columns, strict=True):
            _categories(ax, runs, column, title, unit)
        figure.tight_layout()
        figure.savefig(path, dpi=110)
    finally:
        plt.close(figure)


def family_chart(
    runs: pd.DataFrame, title: str, panels: Sequence[tuple[str, str, str]], path: Path
) -> None:
    """Draw, for one family of configurations, each of `panels` (a `(column, title, unit)`) against
    the prefill size on a log scale. `runs` holds the family's runs, with their `prefill_size` and
    their `label`; a line joins the medians of the valid runs of one label."""
    figure, axes = plt.subplots(1, len(panels), figsize=(5.5 * len(panels), 4.5))
    try:
        for ax, (column, panel, unit) in zip(axes, panels, strict=True):
            plotted = _plotted(runs, column)
            for name, mine in plotted.groupby("label", sort=False):
                ring = mine["status"] == INVALID
                ax.scatter(
                    mine["prefill_size"],
                    mine[column],
                    s=40,
                    facecolors=["none" if r else _COLOUR for r in ring],
                    edgecolors=_COLOUR,
                    linewidths=1.5,
                    zorder=3,
                    label=f"{name}",
                )
                valid = mine[mine["status"] == VALID]
                middles = {
                    size: _median(list(group[column]))
                    for size, group in valid.groupby("prefill_size")
                }
                sizes = sorted(s for s, m in middles.items() if m is not None)
                ax.plot(
                    sizes,
                    [middles[s] for s in sizes],
                    color="black",
                    linewidth=1,
                    marker="_",
                    markersize=14,
                    zorder=2,
                )
            ax.set_xscale("log")
            ax.set_xlabel("prefill size (statements)")
            ax.set_ylabel(unit)
            ax.set_title(f"{panel}\n{title}", fontsize="small")
            ax.grid(alpha=0.3)
            if plotted.empty:
                ax.text(
                    0.5, 0.5, "no run has a value", transform=ax.transAxes, ha="center"
                )
            else:
                _legend(ax)
        figure.tight_layout()
        figure.savefig(path, dpi=110)
    finally:
        plt.close(figure)


_STEP_COLOURS = ("#1f6fb4", "#d95f02", "#1b9e77", "#7570b3", "#e7298a", "#66a61e")


def steps_chart(
    steps: pd.DataFrame,
    labels: dict[int, str],
    title: str,
    path: Path,
    names: dict[int, str] | None = None,
) -> None:
    """Draw, for one family of configurations, the steps table `steps` (`report_steps`): a row for
    each role and step, and for each configuration a dot at the median seconds and a tick at the p95
    seconds, on a log time scale. `labels` names each configuration's prefill, and `names` shows
    its number, with the spec's series (jms_kapps 110): the number alone where it has none."""
    names = names or {}
    rows = list(dict.fromkeys(zip(steps["role"], steps["step"], strict=True)))
    numbers = sorted(set(steps["configuration"]))
    index = {key: i for i, key in enumerate(rows)}
    figure, ax = plt.subplots(figsize=(11.0, max(4.0, 0.32 * len(rows) + 2)))
    try:
        for place, number in enumerate(numbers):
            mine = steps[steps["configuration"] == number]
            colour = _STEP_COLOURS[place % len(_STEP_COLOURS)]
            y = [
                index[(r, s)] + (place - (len(numbers) - 1) / 2) * 0.18
                for r, s in zip(mine["role"], mine["step"], strict=True)
            ]
            ax.scatter(
                mine["median_seconds"],
                y,
                s=24,
                color=colour,
                zorder=3,
                label=f"configuration {names.get(number, number)} "
                f"({labels.get(number, '')}): median",
            )
            ax.scatter(
                mine["p95_seconds"],
                y,
                s=30,
                marker="|",
                color=colour,
                zorder=3,
                label=f"configuration {names.get(number, number)}: p95",
            )
        ax.set_xscale("log")
        ax.set_yticks(range(len(rows)), [f"{r}: {s}" for r, s in rows], fontsize=7)
        ax.invert_yaxis()
        ax.set_xlabel("seconds (log scale)")
        ax.set_title(title, fontsize="small")
        ax.grid(axis="x", alpha=0.3)
        ax.legend(fontsize="x-small", loc="best")
        figure.tight_layout()
        figure.savefig(path, dpi=110)
    finally:
        plt.close(figure)


_HOUR_PANELS = (
    ("transports_ended_per_minute", "Transports ended per minute", "per minute"),
    ("completed_writes_per_minute", "Completed writes per minute", "per minute"),
    ("commit_p95", "Commit latency, p95", "seconds"),
    ("store_request_p95", "Store-request latency, p95", "seconds"),
)


def hours_chart(hours: pd.DataFrame, title: str, path: Path) -> None:
    """Draw the by-hour table of one configuration (`report_points.hours_table`): one panel for
    each measure, the hour of day (UTC) on the x axis and a colour for each run kind, each dot
    labelled with the number of valid runs it pools."""
    figure, axes = plt.subplots(2, 2, figsize=(11.0, 7.0), sharex=True)
    try:
        for ax, (column, panel, unit) in zip(axes.flat, _HOUR_PANELS, strict=True):
            plotted = hours[hours[column].notna()]
            for place, (kind, mine) in enumerate(
                plotted.groupby("run_kind", sort=False)
            ):
                colour = _STEP_COLOURS[place % len(_STEP_COLOURS)]
                ax.plot(
                    mine["hour"],
                    mine[column],
                    marker="o",
                    linestyle="",
                    color=colour,
                    label=str(kind),
                )
                for hour, value, runs in zip(
                    mine["hour"], mine[column], mine["runs"], strict=True
                ):
                    ax.annotate(
                        f"{runs}",
                        (hour, value),
                        fontsize=7,
                        xytext=(3, 3),
                        textcoords="offset points",
                    )
            ax.set_xlim(-0.5, 23.5)
            ax.set_xticks(range(0, 24, 2))
            ax.set_xlabel("hour of day (UTC)")
            ax.set_ylabel(unit)
            ax.set_title(panel, fontsize="small")
            ax.grid(alpha=0.3)
            if plotted.empty:
                ax.text(
                    0.5, 0.5, "no run has a value", transform=ax.transAxes, ha="center"
                )
            else:
                ax.legend(fontsize="x-small", loc="best")
        figure.suptitle(
            f"{title} (the number is the valid runs of the hour)", fontsize="small"
        )
        figure.tight_layout()
        figure.savefig(path, dpi=110)
    finally:
        plt.close(figure)
